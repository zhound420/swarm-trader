#!/usr/bin/env python3
"""
backtest_fast.py — Deterministic backtester for strategy.py.

Supports two modes:
  --mode day   (default) — 5-min bars, intraday simulation, flatten at 3:45 PM
  --mode swing           — daily bars, multi-day holding, SWING_UNIVERSE tickers

Usage:
    poetry run python autoresearch/backtest_fast.py
    poetry run python autoresearch/backtest_fast.py --days 20 --capital 50000
    poetry run python autoresearch/backtest_fast.py --ticker-filter NVDA,AAPL
    poetry run python autoresearch/backtest_fast.py --days 5 --quiet
    poetry run python autoresearch/backtest_fast.py --mode swing --days 30

Output: JSON to stdout (for evolve.py to parse).
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).parent.parent
AUTORESEARCH_DIR = Path(__file__).parent
DATA_CACHE_DIR = AUTORESEARCH_DIR / "data_cache"
DATA_CACHE_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Alpaca API
# ---------------------------------------------------------------------------
DATA_BASE = "https://data.alpaca.markets/v2"
ALPACA_HEADERS = {
    "APCA-API-KEY-ID": os.environ.get("ALPACA_API_KEY", ""),
    "APCA-API-SECRET-KEY": os.environ.get("ALPACA_API_SECRET", ""),
}

# ---------------------------------------------------------------------------
# Simulation constants (not in strategy.py — backtest-level)
# ---------------------------------------------------------------------------
SLIPPAGE_PCT = 0.0005           # 0.05% slippage on fills
CIRCUIT_BREAKER_LOSS_PCT = 0.03 # Stop new entries when daily loss >= 3%
FLATTEN_TIME = "15:45"          # Force-close all positions at this ET time
MAX_CONCURRENT_POSITIONS = 5    # Max simultaneous open positions

# Day trading universe (mirrors src/config.py DAY_TRADE_UNIVERSE)
DAY_TRADE_TICKERS = [
    "NVDA", "AVGO", "TSM", "AMD", "MSFT", "AAPL", "META", "GOOGL", "AMZN",
    "PLTR", "COIN", "MSTR", "RKLB",
    "SPY", "QQQ",
]

# Swing trading universe (mirrors src/config.py SWING_UNIVERSE)
SWING_TICKERS = [
    "NVDA", "AVGO", "SMCI", "TSM",         # AI Infrastructure
    "TQQQ", "SOXL", "UPRO",                 # Leveraged ETFs
    "PLTR", "MSTR", "COIN", "RKLB",         # Momentum
    "IONQ", "RGTI", "SOUN", "LUNR",         # Moonshots
    "SPY",                                   # Broad market reference
]


# ---------------------------------------------------------------------------
# Data fetching & caching
# ---------------------------------------------------------------------------

def _alpaca_get(endpoint: str, params: dict) -> dict:
    """Single GET to Alpaca Data API with retries."""
    url = f"{DATA_BASE}/{endpoint}"
    for attempt in range(3):
        try:
            r = requests.get(url, headers=ALPACA_HEADERS, params=params, timeout=20)
            r.raise_for_status()
            return r.json()
        except requests.HTTPError as e:
            if attempt == 2:
                raise
            time.sleep(1.5 ** attempt)
    return {}


def fetch_5min_bars(ticker: str, date_str: str) -> list[dict]:
    """
    Fetch all 5-min bars for `ticker` on `date_str` (market hours only).
    Results are cached to DATA_CACHE_DIR/bars5m_{ticker}_{date_str}.json.
    """
    cache_path = DATA_CACHE_DIR / f"bars5m_{ticker}_{date_str}.json"
    if cache_path.exists():
        with open(cache_path) as f:
            return json.load(f)

    bars: list[dict] = []
    params: dict[str, Any] = {
        "timeframe": "5Min",
        "start": f"{date_str}T09:30:00-04:00",
        "end": f"{date_str}T16:00:00-04:00",
        "limit": 100,
        "feed": "iex",
        "sort": "asc",
    }

    # Paginate
    while True:
        try:
            data = _alpaca_get(f"stocks/{ticker}/bars", params)
        except Exception:
            break
        batch = data.get("bars") or []
        bars.extend(batch)
        next_token = data.get("next_page_token")
        if not next_token:
            break
        params["page_token"] = next_token

    if bars:
        with open(cache_path, "w") as f:
            json.dump(bars, f, separators=(",", ":"))

    return bars


def fetch_daily_bars(ticker: str, start_date: str, end_date: str) -> list[dict]:
    """
    Fetch daily bars for volume / prev-close lookback.
    Cached per ticker (whole range).
    """
    cache_path = DATA_CACHE_DIR / f"daily_{ticker}_{start_date}_{end_date}.json"
    if cache_path.exists():
        with open(cache_path) as f:
            return json.load(f)

    bars: list[dict] = []
    params: dict[str, Any] = {
        "timeframe": "1Day",
        "start": start_date,
        "end": end_date,
        "limit": 250,
        "feed": "iex",
        "sort": "asc",
    }

    while True:
        try:
            data = _alpaca_get(f"stocks/{ticker}/bars", params)
        except Exception:
            break
        batch = data.get("bars") or []
        bars.extend(batch)
        next_token = data.get("next_page_token")
        if not next_token:
            break
        params["page_token"] = next_token

    with open(cache_path, "w") as f:
        json.dump(bars, f, separators=(",", ":"))

    return bars


def get_trading_days(n: int, end_date: date | None = None) -> list[str]:
    """Return the last N trading day date strings (Mon–Fri, excludes today)."""
    if end_date is None:
        end_date = date.today() - timedelta(days=1)
    days = []
    cursor = end_date
    while len(days) < n:
        if cursor.weekday() < 5:  # Mon=0 ... Fri=4
            days.append(cursor.strftime("%Y-%m-%d"))
        cursor -= timedelta(days=1)
    return list(reversed(days))


# ---------------------------------------------------------------------------
# Regime detection from SPY intraday bars
# ---------------------------------------------------------------------------

def detect_regime(spy_bars: list[dict], qqq_bars: list[dict]) -> tuple[str, float, float]:
    """
    Classify today's market regime from SPY intraday progress.
    Returns (regime, spy_change_pct, qqq_change_pct).
    """
    if not spy_bars:
        return "unknown", 0.0, 0.0

    spy_open = float(spy_bars[0]["o"])
    spy_current = float(spy_bars[-1]["c"])
    spy_high = max(float(b["h"]) for b in spy_bars)
    spy_low = min(float(b["l"]) for b in spy_bars)
    spy_chg = (spy_current - spy_open) / spy_open * 100.0 if spy_open else 0.0

    qqq_chg = 0.0
    if qqq_bars:
        qqq_open = float(qqq_bars[0]["o"])
        qqq_current = float(qqq_bars[-1]["c"])
        qqq_chg = (qqq_current - qqq_open) / qqq_open * 100.0 if qqq_open else 0.0

    # Intraday range as % of open
    intraday_range_pct = (spy_high - spy_low) / spy_open * 100.0 if spy_open else 0.0

    if intraday_range_pct > 2.5:
        regime = "volatile"
    elif spy_chg > 0.5:
        regime = "trending_up"
    elif spy_chg < -0.5:
        regime = "trending_down"
    else:
        regime = "range_bound"

    return regime, spy_chg, qqq_chg


# ---------------------------------------------------------------------------
# Position tracking
# ---------------------------------------------------------------------------

@dataclass
class Position:
    ticker: str
    direction: str          # "long" | "short"
    shares: int
    entry_price: float
    stop_price: float
    target_price: float
    entry_time: str
    cost_basis: float       # shares * entry_price (no slippage in basis; slippage is P&L deduction)


@dataclass
class Trade:
    ticker: str
    direction: str
    shares: int
    entry_price: float
    exit_price: float
    entry_time: str
    exit_time: str
    exit_reason: str        # "target" | "stop" | "flatten" | "eod"
    pnl: float
    pnl_pct: float


# ---------------------------------------------------------------------------
# Simulation helpers
# ---------------------------------------------------------------------------

def _bar_time_et(bar: dict) -> tuple[int, int] | None:
    """Extract HH:MM from bar timestamp and convert UTC → ET (assumes EDT, UTC-4)."""
    t = bar.get("t", "")
    if len(t) < 16:
        return None
    try:
        h_utc, m = int(t[11:13]), int(t[14:16])
        h_et = (h_utc - 4) % 24
        return h_et, m
    except (ValueError, IndexError):
        return None


def _should_flatten(bar: dict) -> bool:
    """Return True if this bar is at or after FLATTEN_TIME (ET)."""
    hm = _bar_time_et(bar)
    if hm is None:
        return False
    h, m = hm
    flatten_h, flatten_m = int(FLATTEN_TIME.split(":")[0]), int(FLATTEN_TIME.split(":")[1])
    return h * 60 + m >= flatten_h * 60 + flatten_m


def _fill_price(price: float, direction: str, action: str) -> float:
    """Apply slippage to a fill price. action = 'entry' | 'exit'."""
    if direction == "long":
        mult = 1.0 + SLIPPAGE_PCT if action == "entry" else 1.0 - SLIPPAGE_PCT
    else:
        mult = 1.0 - SLIPPAGE_PCT if action == "entry" else 1.0 + SLIPPAGE_PCT
    return round(price * mult, 4)


def _check_bracket_fill(
    pos: Position,
    bar: dict,
) -> tuple[float | None, str | None]:
    """
    Check if stop or target was hit within this bar.
    Returns (fill_price, exit_reason) or (None, None).
    Uses conservative worst-case ordering: stop hit before target.
    """
    bar_low = float(bar["l"])
    bar_high = float(bar["h"])
    bar_open = float(bar["o"])

    if pos.direction == "long":
        if bar_low <= pos.stop_price:
            fill = _fill_price(min(pos.stop_price, bar_open), "long", "exit")
            return fill, "stop"
        if bar_high >= pos.target_price:
            fill = _fill_price(max(pos.target_price, bar_open), "long", "exit")
            return fill, "target"
    else:  # short
        if bar_high >= pos.stop_price:
            fill = _fill_price(max(pos.stop_price, bar_open), "short", "exit")
            return fill, "stop"
        if bar_low <= pos.target_price:
            fill = _fill_price(min(pos.target_price, bar_open), "short", "exit")
            return fill, "target"

    return None, None


def _calc_pnl(pos: Position, exit_price: float) -> float:
    if pos.direction == "long":
        return (exit_price - pos.entry_price) * pos.shares
    else:
        return (pos.entry_price - exit_price) * pos.shares


# ---------------------------------------------------------------------------
# Single-day simulation
# ---------------------------------------------------------------------------

def simulate_day(
    date_str: str,
    bars_by_ticker: dict[str, list[dict]],
    daily_context: dict,
    capital: float,
    strategy_module: Any,
) -> tuple[float, list[Trade]]:
    """
    Simulate one trading day. Returns (end_of_day_capital, trades).

    Args:
        date_str:        'YYYY-MM-DD'
        bars_by_ticker:  {ticker: [bar_dicts]}  — full day of 5-min bars
        daily_context:   market_context base (regime, prev_closes, avg_volumes)
        capital:         starting capital for the day
        strategy_module: loaded strategy module
    """
    if not bars_by_ticker:
        return capital, []

    # Build sorted list of all bar timestamps across tickers
    all_timestamps: list[str] = sorted({
        bar["t"]
        for bars in bars_by_ticker.values()
        for bar in bars
    })

    positions: dict[str, Position] = {}   # ticker -> Position
    cash = capital
    trades: list[Trade] = []
    start_of_day_capital = capital
    circuit_breaker_triggered = False

    for ts in all_timestamps:
        # Slice bars up to and including this timestamp for each ticker
        bars_up_to_now: dict[str, list[dict]] = {
            ticker: [b for b in ticker_bars if b["t"] <= ts]
            for ticker, ticker_bars in bars_by_ticker.items()
            if any(b["t"] == ts for b in ticker_bars)
        }

        if not bars_up_to_now:
            continue

        # Get the current bar for each ticker at this timestamp
        current_bars: dict[str, dict] = {
            ticker: next((b for b in ticker_bars if b["t"] == ts), None)  # type: ignore[arg-type]
            for ticker, ticker_bars in bars_by_ticker.items()
            if any(b["t"] == ts for b in ticker_bars)
        }
        current_bars = {t: b for t, b in current_bars.items() if b is not None}

        # Get a representative bar for time checks
        any_bar = next(iter(current_bars.values()))

        # --- 1. Check existing positions for stops/targets/flatten ---
        for ticker in list(positions.keys()):
            pos = positions[ticker]
            if ticker not in current_bars:
                continue
            bar = current_bars[ticker]

            # Force flatten near close
            if _should_flatten(bar):
                exit_price = _fill_price(float(bar["c"]), pos.direction, "exit")
                pnl = _calc_pnl(pos, exit_price)
                pnl_pct = pnl / pos.cost_basis * 100.0
                trades.append(Trade(
                    ticker=ticker,
                    direction=pos.direction,
                    shares=pos.shares,
                    entry_price=pos.entry_price,
                    exit_price=exit_price,
                    entry_time=pos.entry_time,
                    exit_time=ts,
                    exit_reason="flatten",
                    pnl=pnl,
                    pnl_pct=pnl_pct,
                ))
                cash += pos.cost_basis + pnl
                del positions[ticker]
                continue

            fill_price, exit_reason = _check_bracket_fill(pos, bar)
            if fill_price is not None and exit_reason is not None:
                pnl = _calc_pnl(pos, fill_price)
                pnl_pct = pnl / pos.cost_basis * 100.0
                trades.append(Trade(
                    ticker=ticker,
                    direction=pos.direction,
                    shares=pos.shares,
                    entry_price=pos.entry_price,
                    exit_price=fill_price,
                    entry_time=pos.entry_time,
                    exit_time=ts,
                    exit_reason=exit_reason,
                    pnl=pnl,
                    pnl_pct=pnl_pct,
                ))
                cash += pos.cost_basis + pnl
                del positions[ticker]

        # --- 2. Circuit breaker check ---
        current_equity = cash + sum(
            p.cost_basis + _calc_pnl(p, float(current_bars[t]["c"]))
            for t, p in positions.items()
            if t in current_bars
        )
        daily_loss_pct = (current_equity - start_of_day_capital) / start_of_day_capital
        if daily_loss_pct <= -CIRCUIT_BREAKER_LOSS_PCT:
            circuit_breaker_triggered = True

        # Skip signal generation if circuit breaker active, in flatten zone, or no new entries allowed
        if circuit_breaker_triggered or _should_flatten(any_bar):
            continue

        # --- 3. Generate signals ---
        # Build bars history up to current bar for all tickers (including inactive ones for context)
        all_bars_up_to_now = {
            ticker: [b for b in ticker_bars if b["t"] <= ts]
            for ticker, ticker_bars in bars_by_ticker.items()
        }

        # Determine regime dynamically from SPY progress
        spy_bars_so_far = all_bars_up_to_now.get("SPY", [])
        qqq_bars_so_far = all_bars_up_to_now.get("QQQ", [])
        regime, spy_chg, qqq_chg = detect_regime(spy_bars_so_far, qqq_bars_so_far)

        market_context = {
            **daily_context,
            "current_bar_time": ts,
            "regime": regime,
            "spy_change_pct": spy_chg,
            "qqq_change_pct": qqq_chg,
        }

        # Only pass non-anchor tickers to generate_signals (SPY/QQQ are in context, not traded here)
        tradeable_bars = {t: b for t, b in all_bars_up_to_now.items() if t not in ("SPY", "QQQ")}

        try:
            signals = strategy_module.generate_signals(tradeable_bars, market_context)
        except Exception:
            signals = []

        # --- 4. Process new signals ---
        for sig in sorted(signals, key=lambda s: s.confidence, reverse=True):
            ticker = sig.ticker

            # Skip if already in a position for this ticker
            if ticker in positions:
                continue

            # Skip if at max concurrent positions
            if len(positions) >= MAX_CONCURRENT_POSITIONS:
                break

            # Size the position: max 15% of current equity
            position_value = min(current_equity * 0.15, cash * 0.95)
            if position_value < 100:  # too small to trade
                continue

            entry_fill = _fill_price(sig.entry_price, sig.direction, "entry")
            shares = max(1, int(position_value / entry_fill))
            cost = shares * entry_fill

            if cost > cash:
                shares = max(1, int(cash * 0.95 / entry_fill))
                cost = shares * entry_fill
                if cost > cash or shares < 1:
                    continue

            positions[ticker] = Position(
                ticker=ticker,
                direction=sig.direction,
                shares=shares,
                entry_price=entry_fill,
                stop_price=sig.stop_price,
                target_price=sig.target_price,
                entry_time=ts,
                cost_basis=cost,
            )
            cash -= cost

    # --- End of day: close any remaining open positions at last bar ---
    for ticker, pos in list(positions.items()):
        last_bars = bars_by_ticker.get(ticker, [])
        if last_bars:
            exit_price = _fill_price(float(last_bars[-1]["c"]), pos.direction, "exit")
        else:
            exit_price = pos.entry_price  # can't find price, flat at entry (no P&L)
        pnl = _calc_pnl(pos, exit_price)
        pnl_pct = pnl / pos.cost_basis * 100.0
        trades.append(Trade(
            ticker=ticker,
            direction=pos.direction,
            shares=pos.shares,
            entry_price=pos.entry_price,
            exit_price=exit_price,
            entry_time=pos.entry_time,
            exit_time="EOD",
            exit_reason="eod",
            pnl=pnl,
            pnl_pct=pnl_pct,
        ))
        cash += pos.cost_basis + pnl

    end_capital = cash
    return end_capital, trades


# ---------------------------------------------------------------------------
# Swing simulation (daily bars, multi-day holding)
# ---------------------------------------------------------------------------

def simulate_swing(
    trading_days: list[str],
    daily_bars_by_ticker: dict[str, list[dict]],
    initial_capital: float,
    strategy_module: Any,
    quiet: bool = False,
) -> tuple[list[float], list[Trade]]:
    """
    Simulate swing trading over `trading_days` using daily bars.

    Key differences from intraday:
    - One bar per ticker per day (daily OHLCV)
    - Positions carry overnight / multi-day
    - No flatten at 3:45 PM
    - Signals generated from history of daily bars up to each day

    Returns (daily_portfolio_values, all_trades).
    """
    # Build per-ticker daily bar lookup for fast access
    bars_by_ticker_by_date: dict[str, dict[str, dict]] = {}
    for ticker, bars in daily_bars_by_ticker.items():
        bars_by_ticker_by_date[ticker] = {b["t"][:10]: b for b in bars}

    positions: dict[str, Position] = {}
    cash = initial_capital
    daily_values: list[float] = [initial_capital]
    all_trades: list[Trade] = []

    for day in trading_days:
        # Current bar for each ticker on this day
        current_bars: dict[str, dict] = {}
        for ticker in daily_bars_by_ticker:
            bar = bars_by_ticker_by_date.get(ticker, {}).get(day)
            if bar:
                current_bars[ticker] = bar

        if not current_bars:
            # No data for this day — carry equity forward
            equity = cash + sum(
                p.cost_basis + _calc_pnl(p, p.entry_price)  # rough: value at entry
                for p in positions.values()
            )
            daily_values.append(equity)
            continue

        # --- 1. Check existing positions for stops/targets ---
        for ticker in list(positions.keys()):
            if ticker not in current_bars:
                continue
            pos = positions[ticker]
            bar = current_bars[ticker]

            fill_price, exit_reason = _check_bracket_fill(pos, bar)
            if fill_price is not None and exit_reason is not None:
                pnl = _calc_pnl(pos, fill_price)
                pnl_pct = pnl / pos.cost_basis * 100.0
                all_trades.append(Trade(
                    ticker=ticker,
                    direction=pos.direction,
                    shares=pos.shares,
                    entry_price=pos.entry_price,
                    exit_price=fill_price,
                    entry_time=pos.entry_time,
                    exit_time=day,
                    exit_reason=exit_reason,
                    pnl=pnl,
                    pnl_pct=pnl_pct,
                ))
                cash += pos.cost_basis + pnl
                del positions[ticker]

        # --- 2. Calculate current equity ---
        current_equity = cash + sum(
            p.cost_basis + _calc_pnl(p, float(current_bars[t]["c"]))
            for t, p in positions.items()
            if t in current_bars
        )

        # --- 3. Build bars history up to (and including) this day per ticker ---
        bars_history: dict[str, list[dict]] = {}
        for ticker, bars in daily_bars_by_ticker.items():
            hist = [b for b in bars if b["t"][:10] <= day]
            if hist:
                bars_history[ticker] = hist

        # --- 4. Compute SPY daily change for market context ---
        spy_daily_chg = 0.0
        spy_hist = bars_history.get("SPY", [])
        if len(spy_hist) >= 2:
            prev_close = float(spy_hist[-2]["c"])
            today_close = float(spy_hist[-1]["c"])
            spy_daily_chg = (today_close - prev_close) / prev_close * 100.0 if prev_close else 0.0

        # Build avg_volume_20d context
        market_context: dict = {"mode": "swing", "spy_daily_change_pct": spy_daily_chg}
        for ticker, bars in daily_bars_by_ticker.items():
            prev_bars = [b for b in bars if b["t"][:10] < day]
            if len(prev_bars) >= 2:
                recent_20 = prev_bars[-20:]
                market_context[f"{ticker}_avg_volume_20d"] = sum(
                    float(b.get("v", 0)) for b in recent_20
                ) / len(recent_20)

        # --- 5. Generate swing signals (exclude SPY from tradeable universe) ---
        tradeable_bars = {t: b for t, b in bars_history.items() if t != "SPY"}
        try:
            signals = strategy_module.generate_signals(tradeable_bars, market_context)
        except Exception:
            signals = []

        # --- 6. Process new signals ---
        for sig in sorted(signals, key=lambda s: s.confidence, reverse=True):
            ticker = sig.ticker

            # Skip if already in a position for this ticker
            if ticker in positions:
                continue

            # Cap concurrent positions (same as day trading)
            if len(positions) >= MAX_CONCURRENT_POSITIONS:
                break

            position_value = min(current_equity * 0.15, cash * 0.95)
            if position_value < 100:
                continue

            entry_fill = _fill_price(sig.entry_price, sig.direction, "entry")
            shares = max(1, int(position_value / entry_fill))
            cost = shares * entry_fill

            if cost > cash:
                shares = max(1, int(cash * 0.95 / entry_fill))
                cost = shares * entry_fill
                if cost > cash or shares < 1:
                    continue

            positions[ticker] = Position(
                ticker=ticker,
                direction=sig.direction,
                shares=shares,
                entry_price=entry_fill,
                stop_price=sig.stop_price,
                target_price=sig.target_price,
                entry_time=day,
                cost_basis=cost,
            )
            cash -= cost

        # --- 7. Record end-of-day equity ---
        eod_equity = cash + sum(
            p.cost_basis + _calc_pnl(p, float(current_bars[t]["c"]))
            for t, p in positions.items()
            if t in current_bars
        )
        daily_values.append(eod_equity)

        if not quiet:
            day_pnl = eod_equity - daily_values[-2]
            print(
                f"  {day}  P&L: ${day_pnl:+,.0f}  "
                f"positions: {len(positions)}  equity: ${eod_equity:,.0f}",
                file=sys.stderr,
            )

    # --- Close any remaining open positions at last available price ---
    for ticker, pos in list(positions.items()):
        bars = daily_bars_by_ticker.get(ticker, [])
        if bars:
            exit_price = _fill_price(float(bars[-1]["c"]), pos.direction, "exit")
        else:
            exit_price = pos.entry_price
        pnl = _calc_pnl(pos, exit_price)
        pnl_pct = pnl / pos.cost_basis * 100.0
        all_trades.append(Trade(
            ticker=ticker,
            direction=pos.direction,
            shares=pos.shares,
            entry_price=pos.entry_price,
            exit_price=exit_price,
            entry_time=pos.entry_time,
            exit_time="EOP",
            exit_reason="end_of_period",
            pnl=pnl,
            pnl_pct=pnl_pct,
        ))
        cash += pos.cost_basis + pnl

    return daily_values, all_trades


# ---------------------------------------------------------------------------
# Metrics calculation
# ---------------------------------------------------------------------------

def compute_metrics(
    daily_values: list[float],
    all_trades: list[Trade],
    initial_capital: float,
    mode: str = "day",
) -> dict:
    """Compute all performance metrics and composite fitness score.

    mode: "day" uses Sharpe/win-rate-weighted fitness
          "swing" uses total-return/drawdown-weighted fitness
    """
    import math

    if not daily_values or len(daily_values) < 2:
        return _empty_metrics()

    final_value = daily_values[-1]
    total_return_pct = (final_value - initial_capital) / initial_capital * 100.0

    # Daily returns
    returns = [
        (daily_values[i] - daily_values[i - 1]) / daily_values[i - 1]
        for i in range(1, len(daily_values))
    ]

    # Sharpe (annualized, daily RF ≈ 0.0434/252)
    daily_rf = 0.0434 / 252.0
    excess = [r - daily_rf for r in returns]
    mean_excess = sum(excess) / len(excess) if excess else 0.0
    std_excess = _std(excess)
    sharpe = (mean_excess / std_excess) * math.sqrt(252.0) if std_excess > 1e-12 else 0.0

    # Sortino
    neg_excess = [e for e in excess if e < 0]
    if neg_excess:
        downside_std = _std(neg_excess)
        sortino = (mean_excess / downside_std) * math.sqrt(252.0) if downside_std > 1e-12 else (float("inf") if mean_excess > 0 else 0.0)
    else:
        sortino = float("inf") if mean_excess > 0 else 0.0

    # Cap sortino at reasonable value for fitness calc
    sortino_capped = min(sortino, 20.0) if not math.isinf(sortino) else 20.0

    # Max drawdown
    peak = daily_values[0]
    max_dd_pct = 0.0
    for v in daily_values:
        if v > peak:
            peak = v
        dd = (v - peak) / peak * 100.0
        if dd < max_dd_pct:
            max_dd_pct = dd

    # Trade metrics
    num_trades = len(all_trades)
    wins = [t for t in all_trades if t.pnl > 0]
    losses = [t for t in all_trades if t.pnl <= 0]
    win_rate = len(wins) / num_trades if num_trades > 0 else 0.0
    gross_profit = sum(t.pnl for t in wins)
    gross_loss = abs(sum(t.pnl for t in losses))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else (10.0 if gross_profit > 0 else 0.0)
    avg_win = gross_profit / len(wins) if wins else 0.0
    avg_loss = gross_loss / len(losses) if losses else 0.0

    # Composite fitness score — formula differs by mode
    if mode == "swing":
        fitness = _compute_fitness_swing(
            sharpe=sharpe,
            sortino=sortino_capped,
            total_return_pct=total_return_pct,
            win_rate=win_rate,
            profit_factor=profit_factor,
            max_drawdown_pct=abs(max_dd_pct),
            num_trades=num_trades,
        )
    else:
        fitness = _compute_fitness(
            sharpe=sharpe,
            sortino=sortino_capped,
            total_return_pct=total_return_pct,
            win_rate=win_rate,
            profit_factor=profit_factor,
            max_drawdown_pct=abs(max_dd_pct),
            num_trades=num_trades,
        )

    return {
        "total_return_pct": round(total_return_pct, 4),
        "sharpe_ratio": round(sharpe, 4),
        "sortino_ratio": round(sortino_capped, 4),
        "max_drawdown_pct": round(max_dd_pct, 4),
        "win_rate": round(win_rate, 4),
        "profit_factor": round(min(profit_factor, 10.0), 4),
        "num_trades": num_trades,
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "gross_profit": round(gross_profit, 2),
        "gross_loss": round(gross_loss, 2),
        "final_capital": round(final_value, 2),
        "initial_capital": round(initial_capital, 2),
        "fitness": round(fitness, 6),
    }


def _compute_fitness(
    sharpe: float,
    sortino: float,
    total_return_pct: float,
    win_rate: float,
    profit_factor: float,
    max_drawdown_pct: float,
    num_trades: int,
) -> float:
    """Composite fitness formula from program.md."""
    # Clamp extreme values
    sharpe = max(-5.0, min(10.0, sharpe))
    sortino = max(-5.0, min(20.0, sortino))
    profit_factor = min(10.0, profit_factor)

    fitness = (
        sharpe * 0.35
        + sortino * 0.25
        + total_return_pct * 0.20
        + win_rate * 0.10          # 0–1 scale
        + profit_factor * 0.10
    )

    # Penalties
    if max_drawdown_pct > 15.0:
        fitness -= 20.0
    if win_rate < 0.30:
        fitness -= 10.0
    if num_trades < 10:
        fitness -= 15.0
    if num_trades > 200:
        fitness -= 5.0

    return fitness


def _compute_fitness_swing(
    sharpe: float,
    sortino: float,
    total_return_pct: float,
    win_rate: float,
    profit_factor: float,
    max_drawdown_pct: float,
    num_trades: int,
) -> float:
    """Swing fitness formula — weights total return and drawdown control more heavily.

    Swing cares more about:
    - Total return (capturing multi-day trends)
    - Max drawdown (overnight gap risk amplifies losses)
    - Sortino (downside protection matters for swing holds)

    Day trading cares more about Sharpe and win rate (more trades, tighter feedback).
    """
    sharpe = max(-5.0, min(10.0, sharpe))
    sortino = max(-5.0, min(20.0, sortino))
    profit_factor = min(10.0, profit_factor)

    fitness = (
        total_return_pct * 0.35
        + sortino * 0.25
        + sharpe * 0.20
        + profit_factor * 0.12
        + win_rate * 0.08
    )

    # Penalties — stricter drawdown for swing (overnight gaps hurt more)
    if max_drawdown_pct > 20.0:
        fitness -= 25.0
    elif max_drawdown_pct > 15.0:
        fitness -= 10.0
    if win_rate < 0.25:
        fitness -= 10.0
    if num_trades < 3:
        fitness -= 15.0  # swing needs fewer trades — 3+ in 30 days is reasonable
    if num_trades > 100:
        fitness -= 5.0

    return fitness


def _empty_metrics() -> dict:
    return {
        "total_return_pct": 0.0,
        "sharpe_ratio": 0.0,
        "sortino_ratio": 0.0,
        "max_drawdown_pct": 0.0,
        "win_rate": 0.0,
        "profit_factor": 0.0,
        "num_trades": 0,
        "avg_win": 0.0,
        "avg_loss": 0.0,
        "gross_profit": 0.0,
        "gross_loss": 0.0,
        "final_capital": 0.0,
        "initial_capital": 0.0,
        "fitness": -50.0,
    }


def _std(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
    import math
    return math.sqrt(variance)


# ---------------------------------------------------------------------------
# Strategy loader
# ---------------------------------------------------------------------------

def load_strategy(strategy_path: Path | None = None):
    """Dynamically load strategy.py as a module."""
    if strategy_path is None:
        strategy_path = AUTORESEARCH_DIR / "strategy.py"
    spec = importlib.util.spec_from_file_location("strategy", strategy_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load strategy from {strategy_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["strategy"] = module  # Required for Python 3.14 dataclass resolution
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


# ---------------------------------------------------------------------------
# Main backtest runner
# ---------------------------------------------------------------------------

def run_backtest(
    tickers: list[str],
    trading_days: list[str],
    initial_capital: float,
    strategy_module: Any,
    quiet: bool = False,
) -> dict:
    """
    Run the full backtest over `trading_days`.
    Returns the metrics dict.
    """
    if not ALPACA_HEADERS["APCA-API-KEY-ID"]:
        print("ERROR: ALPACA_API_KEY not set in environment", file=sys.stderr)
        sys.exit(1)

    # Prefetch daily bars for prev_close and avg_volume (30 days before start)
    first_day = trading_days[0]
    lookback_start = (datetime.strptime(first_day, "%Y-%m-%d") - timedelta(days=35)).strftime("%Y-%m-%d")
    last_day = trading_days[-1]

    if not quiet:
        print(f"[backtest] Fetching daily bars for {len(tickers)} tickers...", file=sys.stderr)

    daily_bars_by_ticker: dict[str, list[dict]] = {}
    for ticker in tickers:
        try:
            daily_bars_by_ticker[ticker] = fetch_daily_bars(ticker, lookback_start, last_day)
        except Exception as e:
            if not quiet:
                print(f"  [WARN] {ticker} daily bars: {e}", file=sys.stderr)
            daily_bars_by_ticker[ticker] = []

    # Prefetch all 5-min bars (uses cache after first run)
    if not quiet:
        print(f"[backtest] Fetching 5-min bars for {len(trading_days)} days...", file=sys.stderr)

    intraday_by_date: dict[str, dict[str, list[dict]]] = {}
    for day in trading_days:
        day_bars: dict[str, list[dict]] = {}
        for ticker in tickers:
            try:
                bars = fetch_5min_bars(ticker, day)
                if bars:
                    day_bars[ticker] = bars
            except Exception as e:
                if not quiet:
                    print(f"  [WARN] {ticker} {day}: {e}", file=sys.stderr)
        intraday_by_date[day] = day_bars

    # Simulate day-by-day
    capital = initial_capital
    daily_values: list[float] = [initial_capital]
    all_trades: list[Trade] = []

    for day in trading_days:
        bars_by_ticker = intraday_by_date.get(day, {})
        if not bars_by_ticker:
            if not quiet:
                print(f"  [SKIP] {day}: no bar data", file=sys.stderr)
            daily_values.append(capital)
            continue

        # Build daily context: prev_close and avg_volume_20d per ticker
        daily_context: dict = {}
        for ticker in tickers:
            daily_bars = daily_bars_by_ticker.get(ticker, [])
            # Find bars before this trading day
            prev_bars = [b for b in daily_bars if b["t"][:10] < day]
            if prev_bars:
                daily_context[f"{ticker}_prev_close"] = float(prev_bars[-1]["c"])
            if len(prev_bars) >= 2:
                recent_20 = prev_bars[-20:]
                daily_context[f"{ticker}_avg_volume_20d"] = sum(
                    float(b.get("v", 0)) for b in recent_20
                ) / len(recent_20)

        start_capital = capital
        end_capital, day_trades = simulate_day(
            date_str=day,
            bars_by_ticker=bars_by_ticker,
            daily_context=daily_context,
            capital=capital,
            strategy_module=strategy_module,
        )
        capital = end_capital
        all_trades.extend(day_trades)
        daily_values.append(capital)

        day_pnl = capital - start_capital
        day_pnl_pct = day_pnl / start_capital * 100.0
        if not quiet:
            print(
                f"  {day}  P&L: ${day_pnl:+,.0f} ({day_pnl_pct:+.2f}%)  "
                f"trades: {len(day_trades)}  equity: ${capital:,.0f}",
                file=sys.stderr,
            )

    metrics = compute_metrics(daily_values, all_trades, initial_capital, mode="day")

    if not quiet:
        print(
            f"\n[backtest] Done. {len(trading_days)} days, {metrics['num_trades']} trades, "
            f"return={metrics['total_return_pct']:+.2f}%, "
            f"sharpe={metrics['sharpe_ratio']:.2f}, "
            f"fitness={metrics['fitness']:.4f}",
            file=sys.stderr,
        )

    return metrics


def run_swing_backtest(
    tickers: list[str],
    trading_days: list[str],
    initial_capital: float,
    strategy_module: Any,
    quiet: bool = False,
) -> dict:
    """
    Run a swing backtest over `trading_days` using daily bars.
    Returns the metrics dict.
    """
    if not ALPACA_HEADERS["APCA-API-KEY-ID"]:
        print("ERROR: ALPACA_API_KEY not set in environment", file=sys.stderr)
        sys.exit(1)

    # Fetch daily bars with lookback for SMA_SLOW (50-day needs ~70 cal days before start)
    first_day = trading_days[0]
    lookback_start = (datetime.strptime(first_day, "%Y-%m-%d") - timedelta(days=90)).strftime("%Y-%m-%d")
    last_day = trading_days[-1]

    if not quiet:
        print(f"[swing] Fetching daily bars for {len(tickers)} tickers...", file=sys.stderr)

    daily_bars_by_ticker: dict[str, list[dict]] = {}
    for ticker in tickers:
        try:
            daily_bars_by_ticker[ticker] = fetch_daily_bars(ticker, lookback_start, last_day)
        except Exception as e:
            if not quiet:
                print(f"  [WARN] {ticker} daily bars: {e}", file=sys.stderr)
            daily_bars_by_ticker[ticker] = []

    if not quiet:
        print(f"[swing] Simulating {len(trading_days)} trading days...", file=sys.stderr)

    daily_values, all_trades = simulate_swing(
        trading_days=trading_days,
        daily_bars_by_ticker=daily_bars_by_ticker,
        initial_capital=initial_capital,
        strategy_module=strategy_module,
        quiet=quiet,
    )

    metrics = compute_metrics(daily_values, all_trades, initial_capital, mode="swing")

    if not quiet:
        print(
            f"\n[swing] Done. {len(trading_days)} days, {metrics['num_trades']} trades, "
            f"return={metrics['total_return_pct']:+.2f}%, "
            f"sharpe={metrics['sharpe_ratio']:.2f}, "
            f"fitness={metrics['fitness']:.4f}",
            file=sys.stderr,
        )

    return metrics


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fast deterministic backtester for autoresearch/strategy.py"
    )
    parser.add_argument(
        "--mode", type=str, default="day", choices=["day", "swing"],
        help="Trading mode: day (5-min intraday, default) or swing (daily bars, multi-day hold)",
    )
    parser.add_argument(
        "--days", type=int, default=None,
        help="Number of trading days to backtest (default: 10 for day, 30 for swing)",
    )
    parser.add_argument(
        "--capital", type=float, default=100_000.0,
        help="Starting capital (default: 100000)",
    )
    parser.add_argument(
        "--ticker-filter", type=str, default="",
        help="Comma-separated subset of tickers to trade (default: universe for selected mode)",
    )
    parser.add_argument(
        "--strategy", type=str, default="",
        help="Path to strategy.py (default: autoresearch/strategy.py)",
    )
    parser.add_argument(
        "--quiet", action="store_true",
        help="Suppress progress output to stderr",
    )
    args = parser.parse_args()

    # Default days depends on mode
    if args.days is None:
        args.days = 30 if args.mode == "swing" else 10

    # Resolve tickers
    if args.ticker_filter:
        tickers = [t.strip().upper() for t in args.ticker_filter.split(",") if t.strip()]
        if args.mode == "day":
            for anchor in ("SPY", "QQQ"):
                if anchor not in tickers:
                    tickers.append(anchor)
        else:
            if "SPY" not in tickers:
                tickers.append("SPY")
    elif args.mode == "swing":
        tickers = list(SWING_TICKERS)
    else:
        tickers = list(DAY_TRADE_TICKERS)

    # Resolve strategy path
    strategy_path = Path(args.strategy) if args.strategy else None

    # Load strategy
    try:
        strategy_module = load_strategy(strategy_path)
    except Exception as e:
        result = {**_empty_metrics(), "error": f"Failed to load strategy: {e}"}
        print(json.dumps(result))
        return 1

    # Get trading days
    trading_days = get_trading_days(args.days)

    if not args.quiet:
        tradeable = [t for t in tickers if t not in ("SPY", "QQQ")]
        print(f"[backtest] Mode: {args.mode}", file=sys.stderr)
        print(f"[backtest] Tickers: {', '.join(tradeable)}", file=sys.stderr)
        print(f"[backtest] Days: {trading_days[0]} → {trading_days[-1]}", file=sys.stderr)
        print(f"[backtest] Capital: ${args.capital:,.0f}", file=sys.stderr)

    if args.mode == "swing":
        metrics = run_swing_backtest(
            tickers=tickers,
            trading_days=trading_days,
            initial_capital=args.capital,
            strategy_module=strategy_module,
            quiet=args.quiet,
        )
    else:
        metrics = run_backtest(
            tickers=tickers,
            trading_days=trading_days,
            initial_capital=args.capital,
            strategy_module=strategy_module,
            quiet=args.quiet,
        )

    # Output JSON to stdout (for evolve.py to parse)
    print(json.dumps(metrics, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
