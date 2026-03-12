#!/usr/bin/env python3
"""
Market Data Gatherer — Collects all data Cassius needs for analysis.

Outputs a JSON payload with portfolio state, financial metrics, technicals,
news, and insider activity for all tickers. Cassius (Opus 4.6) does the thinking.

Modes:
  --mode swing (default) — fundamentals + news + insider trades (existing behavior)
  --mode day             — intraday technicals: VWAP, RSI, 5-min bars, volume ratio

Usage:
  poetry run python gather_data.py                        # All holdings, swing mode
  poetry run python gather_data.py --mode day             # Day trading intraday data
  poetry run python gather_data.py --tickers NVDA,AVGO    # Specific tickers
  poetry run python gather_data.py --include-universe     # Holdings + full universe
  poetry run python gather_data.py --top 8               # Top 8 by value
"""

import argparse
import json
import math
import os
import sys
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

load_dotenv()

import requests

from src.config import (
    UNIVERSE_SIMPLE as UNIVERSE,
    ALL_UNIVERSE_TICKERS as ALL_UNIVERSE,
    ALL_DAY_TRADE_TICKERS,
    DAY_TRADE_UNIVERSE,
)

# Alpaca trading API
API_BASE = "https://paper-api.alpaca.markets/v2"
# Alpaca market data API (separate base URL)
DATA_BASE = "https://data.alpaca.markets/v2"

HEADERS = {
    "APCA-API-KEY-ID": os.environ.get("ALPACA_API_KEY", ""),
    "APCA-API-SECRET-KEY": os.environ.get("ALPACA_API_SECRET", ""),
}


def alpaca_get(endpoint, base=API_BASE, params=None):
    r = requests.get(f"{base}/{endpoint}", headers=HEADERS, params=params, timeout=15)
    r.raise_for_status()
    return r.json()


# ---------------------------------------------------------------------------
# Portfolio state (shared between modes)
# ---------------------------------------------------------------------------

def get_portfolio_state():
    """Get account + positions from Alpaca."""
    account = alpaca_get("account")
    positions = alpaca_get("positions")

    holdings = []
    for p in sorted(positions, key=lambda x: abs(float(x.get("market_value", 0))), reverse=True):
        symbol = p["symbol"]
        holdings.append({
            "symbol": symbol,
            "qty": float(p.get("qty", 0)),
            "market_value": float(p.get("market_value", 0)),
            "avg_entry_price": float(p.get("avg_entry_price", 0)),
            "current_price": float(p.get("current_price", 0)),
            "unrealized_pl": float(p.get("unrealized_pl", 0)),
            "unrealized_plpc": float(p.get("unrealized_plpc", 0)),
            "in_universe": symbol in ALL_UNIVERSE,
            "category": next((cat for cat, tickers in UNIVERSE.items() if symbol in tickers), "legacy"),
        })

    equity = float(account.get("equity", 0))
    last_equity = float(account.get("last_equity", equity))
    return {
        "equity": equity,
        "cash": float(account.get("cash", 0)),
        "buying_power": float(account.get("buying_power", 0)),
        "portfolio_value": float(account.get("portfolio_value", 0)),
        "daily_pl": equity - last_equity,
        "daily_pl_pct": (equity - last_equity) / last_equity * 100 if last_equity > 0 else 0,
        "positions": holdings,
        "position_count": len(holdings),
    }


# ---------------------------------------------------------------------------
# Swing mode — fundamentals, news, insider trades
# ---------------------------------------------------------------------------

def get_ticker_data_swing(ticker):
    """Get fundamental + news + insider data for swing trading analysis."""
    try:
        from src.tools.api_free import (
            get_prices,
            get_financial_metrics,
            get_company_news,
            get_insider_trades,
        )
    except ImportError:
        from src.tools.api import (
            get_prices,
            get_financial_metrics,
            get_company_news,
            get_insider_trades,
        )

    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")

    data = {"ticker": ticker}

    try:
        prices = get_prices(ticker, start_date, end_date)
        if prices:
            recent = prices[-10:] if len(prices) > 10 else prices
            data["prices"] = {
                "current": prices[-1].close if prices else None,
                "change_1d": ((prices[-1].close - prices[-2].close) / prices[-2].close * 100) if len(prices) >= 2 else None,
                "change_5d": ((prices[-1].close - prices[-5].close) / prices[-5].close * 100) if len(prices) >= 5 else None,
                "change_30d": ((prices[-1].close - prices[-30].close) / prices[-30].close * 100) if len(prices) >= 30 else None,
                "high_90d": max(p.high for p in prices),
                "low_90d": min(p.low for p in prices),
                "avg_volume_10d": sum(p.volume for p in prices[-10:]) / min(10, len(prices)),
                "recent_prices": [{"date": p.time, "close": p.close, "volume": p.volume} for p in recent],
            }
        else:
            data["prices"] = None
    except Exception as e:
        data["prices"] = {"error": str(e)}

    try:
        metrics = get_financial_metrics(ticker, end_date, period="annual", limit=1)
        if metrics:
            raw = metrics[0].model_dump()
            data["fundamentals"] = {k: v for k, v in raw.items() if v is not None}
        else:
            data["fundamentals"] = None
    except Exception as e:
        data["fundamentals"] = {"error": str(e)}

    try:
        news = get_company_news(ticker, end_date, limit=5)
        if news:
            data["news"] = [{"title": n.title, "date": n.date, "source": n.source, "sentiment": n.sentiment} for n in news]
        else:
            data["news"] = []
    except Exception as e:
        data["news"] = [{"error": str(e)}]

    try:
        insiders = get_insider_trades(ticker, end_date, limit=5)
        if insiders:
            data["insider_trades"] = [{
                "name": t.owner_name,
                "type": t.transaction_type,
                "shares": t.transaction_shares,
                "price": t.transaction_price_per_share,
                "date": t.transaction_date,
            } for t in insiders]
        else:
            data["insider_trades"] = []
    except Exception as e:
        data["insider_trades"] = [{"error": str(e)}]

    return data


# ---------------------------------------------------------------------------
# Day mode — intraday technicals from Alpaca data API
# ---------------------------------------------------------------------------

def _calc_rsi(closes: list[float], period: int = 14) -> float | None:
    """Calculate RSI from a list of closing prices."""
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        delta = closes[i] - closes[i - 1]
        gains.append(max(delta, 0))
        losses.append(max(-delta, 0))
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)


def _calc_vwap(bars: list[dict]) -> float | None:
    """Calculate VWAP from 5-min bars (each bar: {o, h, l, c, v, t})."""
    total_pv = 0.0
    total_v = 0.0
    for bar in bars:
        typical = (float(bar["h"]) + float(bar["l"]) + float(bar["c"])) / 3
        vol = float(bar.get("v", 0))
        total_pv += typical * vol
        total_v += vol
    if total_v == 0:
        return None
    return round(total_pv / total_v, 4)


def get_intraday_bars(ticker: str, today: str, limit: int = 100) -> list[dict]:
    """Fetch 5-min intraday bars from Alpaca data API."""
    try:
        resp = requests.get(
            f"{DATA_BASE}/stocks/{ticker}/bars",
            headers=HEADERS,
            params={
                "timeframe": "5Min",
                "start": f"{today}T09:30:00-04:00",
                "limit": limit,
                "feed": "iex",
            },
            timeout=15,
        )
        if resp.status_code == 200:
            return resp.json().get("bars", [])
    except Exception:
        pass
    return []


def get_ticker_data_day(ticker: str, today: str, prev_date: str) -> dict:
    """Get intraday technical data for day trading analysis."""
    data: dict = {"ticker": ticker}

    # 5-min intraday bars for today
    bars = get_intraday_bars(ticker, today)
    closes = [float(b["c"]) for b in bars]
    volumes = [float(b.get("v", 0)) for b in bars]

    if bars:
        intraday: dict = {}
        intraday["bars_5min"] = bars
        intraday["open"] = float(bars[0]["o"])
        intraday["high"] = max(float(b["h"]) for b in bars)
        intraday["low"] = min(float(b["l"]) for b in bars)
        intraday["close"] = float(bars[-1]["c"])  # latest price
        intraday["volume"] = sum(volumes)
        intraday["vwap"] = _calc_vwap(bars)
        intraday["rsi_14"] = _calc_rsi(closes)
        if intraday["vwap"] and intraday["close"]:
            intraday["price_vs_vwap_pct"] = round(
                (intraday["close"] - intraday["vwap"]) / intraday["vwap"] * 100, 3
            )
        data["intraday"] = intraday
        data["prices"] = {
            "current": intraday["close"],
            "change_1d": None,  # filled in below from prev_close
        }
    else:
        data["intraday"] = {}
        data["prices"] = {}

    # Previous close from daily bars (last 22 trading days for 20d avg volume)
    try:
        resp = requests.get(
            f"{DATA_BASE}/stocks/{ticker}/bars",
            headers=HEADERS,
            params={
                "timeframe": "1Day",
                "start": (datetime.strptime(prev_date, "%Y-%m-%d") - timedelta(days=30)).strftime("%Y-%m-%d"),
                "end": prev_date,
                "limit": 22,
                "feed": "iex",
            },
            timeout=15,
        )
        if resp.status_code == 200:
            daily_bars = resp.json().get("bars", [])
            if daily_bars:
                prev_close = float(daily_bars[-1]["c"])
                data["intraday"]["prev_close"] = prev_close
                if data["intraday"].get("close"):
                    data["prices"]["change_1d"] = round(
                        (data["intraday"]["close"] - prev_close) / prev_close * 100, 3
                    )
                # 20-day average volume
                if len(daily_bars) >= 2:
                    avg_vol_20d = sum(float(b.get("v", 0)) for b in daily_bars[:-1]) / (len(daily_bars) - 1)
                    data["intraday"]["avg_volume_20d"] = avg_vol_20d
                    today_vol = data["intraday"].get("volume", 0)
                    if avg_vol_20d > 0:
                        data["intraday"]["volume_ratio"] = round(today_vol / avg_vol_20d, 2)
    except Exception as e:
        data["intraday"]["prev_close_error"] = str(e)

    # Premarket data (4:00–9:30 AM)
    try:
        pre_resp = requests.get(
            f"{DATA_BASE}/stocks/{ticker}/bars",
            headers=HEADERS,
            params={
                "timeframe": "5Min",
                "start": f"{today}T04:00:00-04:00",
                "end": f"{today}T09:30:00-04:00",
                "limit": 30,
                "feed": "iex",
            },
            timeout=15,
        )
        if pre_resp.status_code == 200:
            pre_bars = pre_resp.json().get("bars", [])
            if pre_bars:
                data["intraday"]["premarket_high"] = max(float(b["h"]) for b in pre_bars)
                data["intraday"]["premarket_low"] = min(float(b["l"]) for b in pre_bars)
                data["intraday"]["premarket_volume"] = sum(float(b.get("v", 0)) for b in pre_bars)
    except Exception:
        pass

    # Light news (top 3 headlines) — useful even in day mode for catalyst awareness
    try:
        from src.tools.api_free import get_company_news
        news = get_company_news(ticker, today, limit=3)
        if news:
            data["news"] = [{"title": n.title, "date": n.date, "sentiment": n.sentiment} for n in news]
        else:
            data["news"] = []
    except Exception:
        data["news"] = []

    return data


# ---------------------------------------------------------------------------
# SPY benchmark (swing mode)
# ---------------------------------------------------------------------------

def get_spy_benchmark():
    """Get SPY performance for benchmark comparison (swing mode)."""
    try:
        from src.tools.api_free import get_prices
        end_date = datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")
        prices = get_prices("SPY", start_date, end_date)
        if prices and len(prices) >= 2:
            return {
                "current": prices[-1].close,
                "change_1d": ((prices[-1].close - prices[-2].close) / prices[-2].close * 100) if len(prices) >= 2 else None,
                "change_5d": ((prices[-1].close - prices[-5].close) / prices[-5].close * 100) if len(prices) >= 5 else None,
                "change_30d": ((prices[-1].close - prices[-30].close) / prices[-30].close * 100) if len(prices) >= 30 else None,
                "change_90d": ((prices[-1].close - prices[0].close) / prices[0].close * 100),
            }
    except Exception as e:
        return {"error": str(e)}
    return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Gather market data for Cassius analysis")
    parser.add_argument("--tickers", type=str, help="Comma-separated tickers")
    parser.add_argument("--top", type=int, default=0, help="Top N positions by value (0=all)")
    parser.add_argument("--include-universe", action="store_true", help="Include full universe even if not held")
    parser.add_argument("--output", type=str, help="Output file path (default: stdout)")
    parser.add_argument(
        "--mode",
        type=str,
        default="swing",
        choices=["swing", "day"],
        help="Data mode: 'swing' (fundamentals+news, default) or 'day' (intraday technicals)",
    )
    args = parser.parse_args()

    today = datetime.now().strftime("%Y-%m-%d")
    # Previous trading day (rough — weekends handled by Alpaca returning last available bar)
    prev_date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    payload = {
        "generated_at": datetime.now().isoformat(),
        "mode": args.mode,
        "targets": {
            "phase_1": {"amount": 155000, "deadline": "2026-03-22", "description": "Liquidate legacy, deploy into universe"},
            "phase_2": {"amount": 200000, "deadline": "2026-04-30"},
            "phase_3": {"amount": 250000, "deadline": "2026-06-30"},
            "benchmark": "Beat SPY by 15%+",
        },
        "safety_rails": {
            "max_trade_pct": 0.15,
            "max_trades_per_run": 20,
            "min_confidence": 55,
            "max_loss_per_day": 0.03,
        } if args.mode == "day" else {
            "max_trade_pct": 0.10,
            "max_trades_per_run": 8,
            "min_confidence": 60,
            "min_keep_pct": 0.05,
        },
    }

    # Portfolio state
    print("📡 Fetching portfolio state...", file=sys.stderr)
    payload["portfolio"] = get_portfolio_state()

    # Determine tickers
    if args.tickers:
        tickers = [t.strip() for t in args.tickers.split(",")]
    else:
        held = [p["symbol"] for p in payload["portfolio"]["positions"]]
        if args.top > 0:
            held = held[:args.top]
        tickers = held

    if args.include_universe:
        universe_tickers = ALL_DAY_TRADE_TICKERS if args.mode == "day" else ALL_UNIVERSE
        for t in universe_tickers:
            if t not in tickers:
                tickers.append(t)

    # In day mode, always include SPY and QQQ for regime classification
    if args.mode == "day":
        for anchor in ("SPY", "QQQ"):
            if anchor not in tickers:
                tickers.append(anchor)

    # Gather data
    print(f"🔍 Gathering {args.mode} data for {len(tickers)} tickers...", file=sys.stderr)
    payload["ticker_data"] = {}
    for ticker in tickers:
        print(f"  → {ticker}", file=sys.stderr)
        if args.mode == "day":
            payload["ticker_data"][ticker] = get_ticker_data_day(ticker, today, prev_date)
        else:
            payload["ticker_data"][ticker] = get_ticker_data_swing(ticker)

    # SPY benchmark (swing mode only — day mode embeds SPY intraday above)
    if args.mode == "swing":
        print("📊 Fetching SPY benchmark...", file=sys.stderr)
        payload["spy_benchmark"] = get_spy_benchmark()

    # Output
    output = json.dumps(payload, indent=2, default=str)
    if args.output:
        with open(args.output, "w") as f:
            f.write(output)
        print(f"✅ Data written to {args.output}", file=sys.stderr)
    else:
        print(output)

    return 0


if __name__ == "__main__":
    sys.exit(main())
