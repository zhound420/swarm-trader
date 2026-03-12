"""Alpaca paper trading integration for the AI hedge fund.

Fetches positions, converts to portfolio format, and executes trades with safety rails.

Safety Rails (day trading mode):
- Max single trade: 15% of portfolio value
- Max daily trades: 20
- Minimum 55% confidence from portfolio manager
- Every buy order gets a bracket (stop + take profit) unless explicitly overridden
- Circuit breaker: stop all trading if down 3% on the day (MAX_LOSS_PER_DAY)
- Short selling supported: side='sell' with no existing position = short
- Paper trading only (enforces paper-api endpoint)
- DRY_RUN mode by default
"""

import os
import requests
from datetime import datetime

# Credentials — MUST be set via environment variables or .env file
ALPACA_API_KEY = os.getenv("ALPACA_API_KEY", "")
ALPACA_API_SECRET = os.getenv("ALPACA_API_SECRET", "")

if not ALPACA_API_KEY or not ALPACA_API_SECRET:
    raise EnvironmentError(
        "ALPACA_API_KEY and ALPACA_API_SECRET must be set. "
        "Add them to the .env file or export as environment variables."
    )

# Always paper trading - never use live endpoint
ALPACA_BASE_URL = "https://paper-api.alpaca.markets/v2"

_HEADERS = {
    "APCA-API-KEY-ID": ALPACA_API_KEY,
    "APCA-API-SECRET-KEY": ALPACA_API_SECRET,
    "Content-Type": "application/json",
}

# Safety rail constants — day trading mode
MAX_TRADE_PCT    = 0.15    # Max 15% of portfolio per trade (day trading needs size)
MAX_DAILY_TRADES = 20      # Max 20 trades per session
MIN_CONFIDENCE   = 55      # Lower bar — more opportunities in intraday
MAX_LOSS_PER_DAY = 0.03    # Circuit breaker: stop trading if down 3% today

# Removed: MIN_KEEP_PCT — day traders exit fully, no partial holds

# Track trades placed this session
_session_trade_count = 0


def get_alpaca_account() -> dict:
    """Fetch account information from Alpaca."""
    resp = requests.get(f"{ALPACA_BASE_URL}/account", headers=_HEADERS, timeout=10)
    resp.raise_for_status()
    return resp.json()


def get_alpaca_positions() -> list[dict]:
    """Fetch all open positions from Alpaca. Returns list of position dicts."""
    resp = requests.get(f"{ALPACA_BASE_URL}/positions", headers=_HEADERS, timeout=10)
    resp.raise_for_status()
    return resp.json()


def get_alpaca_portfolio_value(account: dict, positions: list[dict]) -> float:
    """Calculate total portfolio value (cash + positions)."""
    cash = float(account.get("cash", 0))
    position_value = sum(float(p.get("market_value", 0)) for p in positions)
    return cash + position_value


def convert_to_portfolio(
    alpaca_positions: list[dict],
    alpaca_account: dict,
    tickers: list[str] | None = None,
) -> dict:
    """Convert Alpaca positions + account into the portfolio format expected by the hedge fund.

    Args:
        alpaca_positions: Raw list of Alpaca position objects
        alpaca_account: Raw Alpaca account object
        tickers: Full list of tickers to analyze (including ones not currently held).
                 If None, uses only tickers from existing positions.

    Returns:
        Portfolio dict compatible with run_hedge_fund()
    """
    cash = float(alpaca_account.get("cash", 0))

    # Build lookup by symbol
    positions_by_symbol: dict[str, dict] = {p["symbol"]: p for p in alpaca_positions}

    # Determine all tickers (existing positions + any additional from args)
    all_tickers = list(positions_by_symbol.keys())
    if tickers:
        for t in tickers:
            if t not in all_tickers:
                all_tickers.append(t)

    positions = {}
    for ticker in all_tickers:
        pos = positions_by_symbol.get(ticker, {})
        qty = int(float(pos.get("qty", 0)))
        avg_price = float(pos.get("avg_entry_price", 0))

        positions[ticker] = {
            "long": qty if qty > 0 else 0,
            "short": abs(qty) if qty < 0 else 0,
            "long_cost_basis": avg_price if qty > 0 else 0.0,
            "short_cost_basis": avg_price if qty < 0 else 0.0,
            "short_margin_used": 0.0,
        }

    realized_gains = {
        ticker: {"long": 0.0, "short": 0.0}
        for ticker in all_tickers
    }

    return {
        "cash": cash,
        "margin_requirement": 0.5,
        "margin_used": 0.0,
        "positions": positions,
        "realized_gains": realized_gains,
    }


def get_daily_pnl(account: dict) -> float:
    """Calculate today's P&L as a fraction of starting equity."""
    equity = float(account.get("equity", 0))
    last_equity = float(account.get("last_equity", equity))
    if last_equity <= 0:
        return 0.0
    return (equity - last_equity) / last_equity


def _validate_trade(
    ticker: str,
    action: str,
    qty: int,
    confidence: float,
    current_price: float,
    current_shares: int,
    portfolio_value: float,
    daily_pnl_pct: float = 0.0,
) -> tuple[bool, str]:
    """Validate a trade against all safety rails.

    Returns:
        (is_valid, reason_if_invalid)
    """
    global _session_trade_count

    if action == "hold" or qty <= 0:
        return False, "Hold or zero quantity — no trade needed"

    # Rail 1: daily trade limit
    if _session_trade_count >= MAX_DAILY_TRADES:
        return False, f"Daily trade limit ({MAX_DAILY_TRADES}) reached for this session"

    # Rail 2: confidence threshold
    if confidence < MIN_CONFIDENCE:
        return False, f"Confidence {confidence:.0f}% is below minimum {MIN_CONFIDENCE}%"

    # Rail 3: max trade size
    if current_price > 0:
        trade_value = qty * current_price
        max_trade_value = portfolio_value * MAX_TRADE_PCT
        if trade_value > max_trade_value:
            max_qty = int(max_trade_value / current_price)
            return False, (
                f"Trade value ${trade_value:,.0f} exceeds max ${max_trade_value:,.0f} "
                f"({MAX_TRADE_PCT*100:.0f}% of ${portfolio_value:,.0f}). Max qty: {max_qty}"
            )

    # Rail 4: circuit breaker — stop all new buys if down MAX_LOSS_PER_DAY
    if action in ("buy", "cover") and daily_pnl_pct <= -MAX_LOSS_PER_DAY:
        return False, (
            f"Circuit breaker triggered: down {abs(daily_pnl_pct)*100:.1f}% today "
            f"(limit {MAX_LOSS_PER_DAY*100:.0f}%). No new buys until tomorrow."
        )

    # Note: MIN_KEEP_PCT removed — day traders exit fully
    # Short selling is allowed: action='short' or action='sell' with no existing long

    return True, ""


def _place_alpaca_order(ticker: str, action: str, qty: int) -> dict:
    """Place a market order via Alpaca API."""
    side = "buy" if action in ("buy", "cover") else "sell"
    order_data = {
        "symbol": ticker,
        "qty": str(qty),
        "side": side,
        "type": "market",
        "time_in_force": "day",
    }
    resp = requests.post(
        f"{ALPACA_BASE_URL}/orders",
        headers=_HEADERS,
        json=order_data,
        timeout=10,
    )
    if resp.status_code in (200, 201):
        order = resp.json()
        return {
            "success": True,
            "order_id": order.get("id"),
            "status": order.get("status"),
        }
    return {
        "success": False,
        "reason": f"Alpaca API error {resp.status_code}: {resp.text[:200]}",
    }


def _place_bracket_order(
    ticker: str,
    action: str,
    qty: int,
    stop_price: float,
    take_profit_price: float,
) -> dict:
    """Place a bracket order via Alpaca API (entry + stop-loss + take-profit as one atomic order).

    Args:
        ticker: Stock symbol
        action: "buy" or "sell"
        qty: Number of shares
        stop_price: Stop-loss trigger price
        take_profit_price: Take-profit limit price

    Returns:
        Dict with success, order_id, status, or reason on failure
    """
    side = "buy" if action in ("buy", "cover") else "sell"
    order_data = {
        "symbol": ticker,
        "qty": str(qty),
        "side": side,
        "type": "market",
        "time_in_force": "gtc",
        "order_class": "bracket",
        "stop_loss": {"stop_price": str(round(stop_price, 2))},
        "take_profit": {"limit_price": str(round(take_profit_price, 2))},
    }
    resp = requests.post(
        f"{ALPACA_BASE_URL}/orders",
        headers=_HEADERS,
        json=order_data,
        timeout=10,
    )
    if resp.status_code in (200, 201):
        order = resp.json()
        return {
            "success": True,
            "order_id": order.get("id"),
            "status": order.get("status"),
            "order_class": "bracket",
            "stop_price": stop_price,
            "take_profit_price": take_profit_price,
        }
    return {
        "success": False,
        "reason": f"Alpaca API error {resp.status_code}: {resp.text[:200]}",
    }


def flatten_positions(
    positions_raw: list[dict],
    dry_run: bool = True,
    tickers: list[str] | None = None,
) -> list[dict]:
    """Market-sell all open positions (end-of-day flatten).

    Args:
        positions_raw: Raw Alpaca positions list
        dry_run: If True, show what would be sold without placing orders
        tickers: If provided, only flatten these tickers. Default: all positions.

    Returns:
        List of result dicts per position flattened
    """
    results = []
    for pos in positions_raw:
        symbol = pos["symbol"]
        if tickers and symbol not in tickers:
            continue

        qty = int(float(pos.get("qty", 0)))
        if qty == 0:
            continue

        side = "sell" if qty > 0 else "buy"  # longs → sell, shorts → buy to cover
        abs_qty = abs(qty)

        if dry_run:
            results.append({
                "ticker": symbol,
                "action": "flatten",
                "qty": abs_qty,
                "side": side,
                "success": True,
                "dry_run": True,
            })
        else:
            order_data = {
                "symbol": symbol,
                "qty": str(abs_qty),
                "side": side,
                "type": "market",
                "time_in_force": "day",
            }
            resp = requests.post(
                f"{ALPACA_BASE_URL}/orders",
                headers=_HEADERS,
                json=order_data,
                timeout=10,
            )
            if resp.status_code in (200, 201):
                order = resp.json()
                results.append({
                    "ticker": symbol,
                    "action": "flatten",
                    "qty": abs_qty,
                    "side": side,
                    "success": True,
                    "order_id": order.get("id"),
                    "status": order.get("status"),
                })
            else:
                results.append({
                    "ticker": symbol,
                    "action": "flatten",
                    "qty": abs_qty,
                    "success": False,
                    "reason": f"Alpaca API error {resp.status_code}: {resp.text[:200]}",
                })

    return results


def execute_decisions(
    decisions: dict,
    positions_raw: list[dict],
    account: dict,
    dry_run: bool = True,
) -> list[dict]:
    """Execute all trading decisions against safety rails.

    Args:
        decisions: Dict of {ticker: {action, quantity, confidence, reasoning,
                   stop_price (optional), take_profit (optional)}}
                   If stop_price and take_profit are both provided, a bracket order is placed.
        positions_raw: Raw Alpaca positions list
        account: Raw Alpaca account dict
        dry_run: If True, validate but don't actually place orders

    Returns:
        List of result dicts with success/failure info per ticker
    """
    global _session_trade_count

    # Build position lookup
    positions_by_symbol: dict[str, dict] = {p["symbol"]: p for p in positions_raw}
    portfolio_value = get_alpaca_portfolio_value(account, positions_raw)
    daily_pnl_pct = get_daily_pnl(account)

    results = []

    for ticker, decision in decisions.items():
        action = decision.get("action", "hold")
        qty = int(decision.get("quantity", 0))
        confidence = float(decision.get("confidence", 0))
        reasoning = decision.get("reasoning", "")
        stop_price = decision.get("stop_price")
        take_profit = decision.get("take_profit")
        order_type = decision.get("order_type", "market")
        limit_price = decision.get("limit_price")
        trail_percent = decision.get("trail_percent")

        # Auto-enforce bracket on buy orders: calculate stop/target from DEFAULT_STOP_PCT
        # if agent didn't provide them.
        if action in ("buy", "cover") and order_type not in ("limit", "stop", "trailing_stop", "oco"):
            pos = positions_by_symbol.get(ticker, {})
            entry_price = float(pos.get("current_price", 0))
            if entry_price <= 0:
                # Try to get from decision
                entry_price = float(decision.get("limit_price") or decision.get("entry_price") or 0)
            if entry_price > 0:
                from src.config import DEFAULT_STOP_PCT, DEFAULT_TARGET_MULTIPLIER
                if stop_price is None:
                    stop_price = round(entry_price * (1 - DEFAULT_STOP_PCT), 2)
                if take_profit is None and stop_price is not None:
                    stop_dist = entry_price - float(stop_price)
                    take_profit = round(entry_price + stop_dist * DEFAULT_TARGET_MULTIPLIER, 2)

        use_bracket = stop_price is not None and take_profit is not None and order_type != "oco"

        if action == "hold" or qty <= 0:
            results.append({
                "ticker": ticker,
                "action": action,
                "qty": qty,
                "success": False,
                "reason": "Hold — no trade needed",
                "skipped": True,
            })
            continue

        pos = positions_by_symbol.get(ticker, {})
        current_price = float(pos.get("current_price", 0))
        current_shares = int(float(pos.get("qty", 0)))

        is_valid, reason = _validate_trade(
            ticker=ticker,
            action=action,
            qty=qty,
            confidence=confidence,
            current_price=current_price,
            current_shares=current_shares,
            portfolio_value=portfolio_value,
            daily_pnl_pct=daily_pnl_pct,
        )

        if not is_valid:
            results.append({
                "ticker": ticker,
                "action": action,
                "qty": qty,
                "confidence": confidence,
                "success": False,
                "reason": reason,
            })
            continue

        if dry_run:
            dry_result: dict = {
                "ticker": ticker,
                "action": action,
                "qty": qty,
                "confidence": confidence,
                "success": True,
                "dry_run": True,
                "reasoning": reasoning,
                "order_type": order_type,
            }
            if use_bracket:
                dry_result["order_class"] = "bracket"
                dry_result["stop_price"] = stop_price
                dry_result["take_profit_price"] = take_profit
            elif order_type == "oco" and stop_price is not None and take_profit is not None:
                dry_result["order_class"] = "oco"
                dry_result["stop_price"] = stop_price
                dry_result["take_profit_price"] = take_profit
            elif order_type == "limit" and limit_price is not None:
                dry_result["limit_price"] = limit_price
            elif order_type == "stop" and stop_price is not None:
                dry_result["stop_price"] = stop_price
            elif order_type == "trailing_stop" and trail_percent is not None:
                dry_result["trail_percent"] = trail_percent
            results.append(dry_result)
        else:
            if use_bracket:
                order_result = _place_bracket_order(ticker, action, qty, float(stop_price), float(take_profit))
            elif order_type == "oco" and stop_price is not None and take_profit is not None:
                order_result = _place_oco_order(ticker, action, qty, float(stop_price), float(take_profit))
            elif order_type == "limit" and limit_price is not None:
                order_result = _place_limit_order(ticker, action, qty, float(limit_price))
            elif order_type == "stop" and stop_price is not None:
                order_result = _place_stop_order(ticker, action, qty, float(stop_price))
            elif order_type == "trailing_stop" and trail_percent is not None:
                order_result = _place_trailing_stop(ticker, action, qty, float(trail_percent))
            else:
                order_result = _place_alpaca_order(ticker, action, qty)
            if order_result["success"]:
                _session_trade_count += 1
            results.append({
                "ticker": ticker,
                "action": action,
                "qty": qty,
                "confidence": confidence,
                **order_result,
            })

    return results


def get_open_orders(status: str = "open") -> list[dict]:
    """Fetch all open orders from Alpaca.

    Args:
        status: Order status filter — "open", "closed", or "all"

    Returns:
        List of order dicts from Alpaca
    """
    resp = requests.get(
        f"{ALPACA_BASE_URL}/orders",
        headers=_HEADERS,
        params={"status": status, "limit": 100},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def get_order(order_id: str) -> dict:
    """Fetch a single order by ID.

    Returns:
        Full order dict (status, filled_qty, etc.) or error dict
    """
    resp = requests.get(f"{ALPACA_BASE_URL}/orders/{order_id}", headers=_HEADERS, timeout=10)
    if resp.status_code == 200:
        return resp.json()
    return {"success": False, "reason": f"Alpaca API error {resp.status_code}: {resp.text[:200]}"}


def cancel_order(order_id: str) -> dict:
    """Cancel a single open order by ID.

    Returns:
        {"success": True/False, "order_id": ..., "reason": ...}
    """
    resp = requests.delete(f"{ALPACA_BASE_URL}/orders/{order_id}", headers=_HEADERS, timeout=10)
    if resp.status_code in (200, 204):
        return {"success": True, "order_id": order_id}
    return {
        "success": False,
        "order_id": order_id,
        "reason": f"Alpaca API error {resp.status_code}: {resp.text[:200]}",
    }


def cancel_all_orders() -> dict:
    """Cancel all open orders.

    Returns:
        {"success": True/False, "cancelled_count": int}
    """
    resp = requests.delete(f"{ALPACA_BASE_URL}/orders", headers=_HEADERS, timeout=10)
    if resp.status_code in (200, 207):
        cancelled = resp.json() if resp.text else []
        return {"success": True, "cancelled_count": len(cancelled) if isinstance(cancelled, list) else 0}
    return {
        "success": False,
        "cancelled_count": 0,
        "reason": f"Alpaca API error {resp.status_code}: {resp.text[:200]}",
    }


def _place_limit_order(
    ticker: str,
    action: str,
    qty: int,
    limit_price: float,
    time_in_force: str = "day",
) -> dict:
    """Place a limit order via Alpaca API."""
    side = "buy" if action in ("buy", "cover") else "sell"
    order_data = {
        "symbol": ticker,
        "qty": str(qty),
        "side": side,
        "type": "limit",
        "time_in_force": time_in_force,
        "limit_price": str(round(limit_price, 2)),
    }
    resp = requests.post(f"{ALPACA_BASE_URL}/orders", headers=_HEADERS, json=order_data, timeout=10)
    if resp.status_code in (200, 201):
        order = resp.json()
        return {
            "success": True,
            "order_id": order.get("id"),
            "status": order.get("status"),
            "order_type": "limit",
            "limit_price": limit_price,
        }
    return {"success": False, "reason": f"Alpaca API error {resp.status_code}: {resp.text[:200]}"}


def _place_stop_order(
    ticker: str,
    action: str,
    qty: int,
    stop_price: float,
    time_in_force: str = "gtc",
) -> dict:
    """Place a standalone stop order via Alpaca API.

    Use this for stop-losses on EXISTING positions (not as part of a bracket entry).
    """
    side = "buy" if action in ("buy", "cover") else "sell"
    order_data = {
        "symbol": ticker,
        "qty": str(qty),
        "side": side,
        "type": "stop",
        "time_in_force": time_in_force,
        "stop_price": str(round(stop_price, 2)),
    }
    resp = requests.post(f"{ALPACA_BASE_URL}/orders", headers=_HEADERS, json=order_data, timeout=10)
    if resp.status_code in (200, 201):
        order = resp.json()
        return {
            "success": True,
            "order_id": order.get("id"),
            "status": order.get("status"),
            "order_type": "stop",
            "stop_price": stop_price,
        }
    return {"success": False, "reason": f"Alpaca API error {resp.status_code}: {resp.text[:200]}"}


def _place_trailing_stop(
    ticker: str,
    action: str,
    qty: int,
    trail_percent: float,
    time_in_force: str = "gtc",
) -> dict:
    """Place a trailing stop order via Alpaca API.

    Args:
        trail_percent: Percentage trail (e.g. 2.0 = 2% trailing stop)
    """
    side = "buy" if action in ("buy", "cover") else "sell"
    order_data = {
        "symbol": ticker,
        "qty": str(qty),
        "side": side,
        "type": "trailing_stop",
        "time_in_force": time_in_force,
        "trail_percent": str(round(trail_percent, 2)),
    }
    resp = requests.post(f"{ALPACA_BASE_URL}/orders", headers=_HEADERS, json=order_data, timeout=10)
    if resp.status_code in (200, 201):
        order = resp.json()
        return {
            "success": True,
            "order_id": order.get("id"),
            "status": order.get("status"),
            "order_type": "trailing_stop",
            "trail_percent": trail_percent,
        }
    return {"success": False, "reason": f"Alpaca API error {resp.status_code}: {resp.text[:200]}"}


def _place_oco_order(
    ticker: str,
    action: str,
    qty: int,
    stop_price: float,
    take_profit_price: float,
) -> dict:
    """Place an OCO (One-Cancels-Other) order via Alpaca API.

    Use for managing exits on ALREADY HELD positions — no new entry leg.
    Different from bracket: bracket = entry + exits; OCO = just exits.
    """
    side = "buy" if action in ("buy", "cover") else "sell"
    order_data = {
        "symbol": ticker,
        "qty": str(qty),
        "side": side,
        "type": "limit",
        "time_in_force": "gtc",
        "order_class": "oco",
        "stop_loss": {"stop_price": str(round(stop_price, 2))},
        "take_profit": {"limit_price": str(round(take_profit_price, 2))},
    }
    resp = requests.post(f"{ALPACA_BASE_URL}/orders", headers=_HEADERS, json=order_data, timeout=10)
    if resp.status_code in (200, 201):
        order = resp.json()
        return {
            "success": True,
            "order_id": order.get("id"),
            "status": order.get("status"),
            "order_class": "oco",
            "stop_price": stop_price,
            "take_profit_price": take_profit_price,
        }
    return {"success": False, "reason": f"Alpaca API error {resp.status_code}: {resp.text[:200]}"}


def format_positions_summary(positions_raw: list[dict], account: dict) -> str:
    """Format a human-readable summary of current positions."""
    cash = float(account.get("cash", 0))
    portfolio_value = get_alpaca_portfolio_value(account, positions_raw)
    lines = [
        f"Portfolio Value: ${portfolio_value:,.2f}",
        f"Cash: ${cash:,.2f}",
        f"Positions ({len(positions_raw)}):",
    ]
    for pos in sorted(positions_raw, key=lambda p: abs(float(p.get("market_value", 0))), reverse=True):
        symbol = pos["symbol"]
        qty = float(pos.get("qty", 0))
        market_value = float(pos.get("market_value", 0))
        unrealized_pl = float(pos.get("unrealized_pl", 0))
        pl_sign = "+" if unrealized_pl >= 0 else ""
        lines.append(
            f"  {symbol}: {qty:.0f} shares  ${market_value:,.2f}  ({pl_sign}{unrealized_pl:,.2f} P&L)"
        )
    return "\n".join(lines)
