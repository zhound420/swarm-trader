#!/usr/bin/env python3
"""
Market Data Gatherer — Collects all data Cassius needs for analysis.

Outputs a JSON payload with portfolio state, financial metrics, technicals,
news, and insider activity for all tickers. Cassius (Opus 4.6) does the thinking.

Usage:
  poetry run python gather_data.py                     # All holdings
  poetry run python gather_data.py --tickers NVDA,AVGO # Specific tickers
  poetry run python gather_data.py --include-universe   # Holdings + full universe
  poetry run python gather_data.py --top 8             # Top 8 by value
"""

import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

import requests

# Alpaca config
API_BASE = "https://paper-api.alpaca.markets/v2"
HEADERS = {
    "APCA-API-KEY-ID": os.environ.get("ALPACA_API_KEY", ""),
    "APCA-API-SECRET-KEY": os.environ.get("ALPACA_API_SECRET", ""),
}

UNIVERSE = {
    "ai_infra": ["NVDA", "AVGO", "SMCI", "TSM"],
    "leveraged": ["TQQQ", "SOXL", "UPRO"],
    "momentum": ["PLTR", "MSTR", "COIN", "RKLB"],
    "moonshots": ["IONQ", "RGTI", "SOUN", "LUNR"],
}
ALL_UNIVERSE = [t for group in UNIVERSE.values() for t in group]


def alpaca_get(endpoint):
    r = requests.get(f"{API_BASE}/{endpoint}", headers=HEADERS, timeout=10)
    r.raise_for_status()
    return r.json()


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

    return {
        "equity": float(account.get("equity", 0)),
        "cash": float(account.get("cash", 0)),
        "buying_power": float(account.get("buying_power", 0)),
        "portfolio_value": float(account.get("portfolio_value", 0)),
        "daily_pl": float(account.get("equity", 0)) - float(account.get("last_equity", 0)),
        "positions": holdings,
        "position_count": len(holdings),
    }


def get_ticker_data(ticker):
    """Get financial data for a single ticker using the free data layer."""
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

    # Prices (last 90 days)
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

    # Financial metrics — dump all non-None fields
    try:
        metrics = get_financial_metrics(ticker, end_date, period="annual", limit=1)
        if metrics:
            raw = metrics[0].model_dump()
            data["fundamentals"] = {k: v for k, v in raw.items() if v is not None}
        else:
            data["fundamentals"] = None
    except Exception as e:
        data["fundamentals"] = {"error": str(e)}

    # News (last 5 items)
    try:
        news = get_company_news(ticker, end_date, limit=5)
        if news:
            data["news"] = [{"title": n.title, "date": n.date, "source": n.source, "sentiment": n.sentiment} for n in news]
        else:
            data["news"] = []
    except Exception as e:
        data["news"] = [{"error": str(e)}]

    # Insider trades (last 5)
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


def get_spy_benchmark():
    """Get SPY performance for benchmark comparison."""
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


def main():
    parser = argparse.ArgumentParser(description="Gather market data for Cassius analysis")
    parser.add_argument("--tickers", type=str, help="Comma-separated tickers")
    parser.add_argument("--top", type=int, default=0, help="Top N positions by value (0=all)")
    parser.add_argument("--include-universe", action="store_true", help="Include full universe even if not held")
    parser.add_argument("--output", type=str, help="Output file path (default: stdout)")
    args = parser.parse_args()

    payload = {
        "generated_at": datetime.now().isoformat(),
        "targets": {
            "phase_1": {"amount": 155000, "deadline": "2026-03-22", "description": "Liquidate legacy, deploy into universe"},
            "phase_2": {"amount": 200000, "deadline": "2026-04-30"},
            "phase_3": {"amount": 250000, "deadline": "2026-06-30"},
            "benchmark": "Beat SPY by 15%+",
        },
        "safety_rails": {
            "max_trade_pct": 0.10,
            "max_trades_per_run": 8,
            "min_confidence": 60,
            "min_keep_pct": 0.05,
        },
    }

    # Portfolio state
    print("📡 Fetching portfolio state...", file=sys.stderr)
    payload["portfolio"] = get_portfolio_state()

    # Determine tickers to analyze
    if args.tickers:
        tickers = [t.strip() for t in args.tickers.split(",")]
    else:
        held = [p["symbol"] for p in payload["portfolio"]["positions"]]
        if args.top > 0:
            held = held[:args.top]
        tickers = held

    if args.include_universe:
        for t in ALL_UNIVERSE:
            if t not in tickers:
                tickers.append(t)

    # Gather data for each ticker
    print(f"🔍 Gathering data for {len(tickers)} tickers...", file=sys.stderr)
    payload["ticker_data"] = {}
    for ticker in tickers:
        print(f"  → {ticker}", file=sys.stderr)
        payload["ticker_data"][ticker] = get_ticker_data(ticker)

    # SPY benchmark
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
