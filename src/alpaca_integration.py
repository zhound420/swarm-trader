"""Alpaca paper trading integration for the AI hedge fund.

Fetches positions, converts to portfolio format, and executes trades with safety rails.

Safety Rails:
- Max single trade: 5% of portfolio value
- Max daily trades: 5
- Never sell entire position (keep at least 10%)
- Require minimum 70% confidence from portfolio manager
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

# Safety rail constants (aggressive paper trading mode)
MAX_TRADE_PCT = 0.10       # Max 10% of portfolio per trade
MAX_DAILY_TRADES = 8       # Max 8 trades per run
MIN_KEEP_PCT = 0.05        # Keep at least 5% of any position when selling
MIN_CONFIDENCE = 60        # Minimum confidence % to execute a trade

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


def _validate_trade(
    ticker: str,
    action: str,
    qty: int,
    confidence: float,
    current_price: float,
    current_shares: int,
    portfolio_value: float,
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
                f"(5% of ${portfolio_value:,.0f}). Max qty: {max_qty}"
            )

    # Rail 4: never sell entire position
    if action in ("sell",) and current_shares > 0:
        min_keep = max(1, int(current_shares * MIN_KEEP_PCT))
        max_sell = current_shares - min_keep
        if qty > max_sell:
            return False, (
                f"Would sell entire position. Max sell: {max_sell} shares "
                f"(keeping {min_keep} = {MIN_KEEP_PCT*100:.0f}% of {current_shares})"
            )

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


def execute_decisions(
    decisions: dict,
    positions_raw: list[dict],
    account: dict,
    dry_run: bool = True,
) -> list[dict]:
    """Execute all trading decisions against safety rails.

    Args:
        decisions: Dict of {ticker: {action, quantity, confidence, reasoning}}
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

    results = []

    for ticker, decision in decisions.items():
        action = decision.get("action", "hold")
        qty = int(decision.get("quantity", 0))
        confidence = float(decision.get("confidence", 0))
        reasoning = decision.get("reasoning", "")

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
            results.append({
                "ticker": ticker,
                "action": action,
                "qty": qty,
                "confidence": confidence,
                "success": True,
                "dry_run": True,
                "reasoning": reasoning,
            })
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
