#!/usr/bin/env python3
"""
Trade Alerts — Monitors for anomalies in Cassius's trading behavior.

Run after each trading session or during heartbeats.

Usage:
  poetry run python trade_alerts.py --check              # Check all alerts
  poetry run python trade_alerts.py --check --telegram   # Output for Telegram
  poetry run python trade_alerts.py --audit decisions.json  # Audit before execution

Alerts:
  1. Position concentration: any single position >25% of portfolio
  2. Sector concentration: any category >50% of portfolio
  3. Rapid trading: >15 trades in a single day
  4. Cash depletion: cash <10% of equity
  5. Drawdown: equity down >10% from peak
  6. Trade size: individual trade >10% of portfolio (pre-execution check)
"""

import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

import requests

ALERTS_LOG = Path(__file__).parent / "data" / "alerts.jsonl"
PERF_DATA = Path(__file__).parent / "data" / "performance.json"
JOURNAL_PATH = Path(__file__).parent / "data" / "trade_journal.jsonl"

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

# Alert thresholds
MAX_POSITION_PCT = 25.0      # Single position max % of portfolio
MAX_CATEGORY_PCT = 50.0      # Category max % of portfolio
MAX_DAILY_TRADES = 15        # Max trades per day
MIN_CASH_PCT = 10.0          # Minimum cash % of equity
MAX_DRAWDOWN_PCT = 10.0      # Max drawdown from peak before alert
MAX_TRADE_SIZE_PCT = 10.0    # Max single trade size


def get_portfolio():
    try:
        account = requests.get(f"{API_BASE}/account", headers=HEADERS, timeout=10).json()
        positions = requests.get(f"{API_BASE}/positions", headers=HEADERS, timeout=10).json()
        return account, positions
    except Exception as e:
        return None, None


def check_concentration(account, positions):
    """Check position and sector concentration."""
    alerts = []
    equity = float(account.get("equity", 0))
    if equity <= 0:
        return alerts

    # Position concentration
    for p in positions:
        sym = p["symbol"]
        mv = float(p.get("market_value", 0))
        pct = mv / equity * 100

        if pct > MAX_POSITION_PCT:
            alerts.append({
                "level": "warning",
                "type": "position_concentration",
                "message": f"🚨 {sym} is {pct:.1f}% of portfolio (max {MAX_POSITION_PCT}%)",
                "ticker": sym,
                "value": pct,
                "threshold": MAX_POSITION_PCT,
            })

    # Category concentration
    categories = {}
    for p in positions:
        sym = p["symbol"]
        cat = next((k for k, v in UNIVERSE.items() if sym in v), "other")
        categories[cat] = categories.get(cat, 0) + float(p.get("market_value", 0))

    for cat, value in categories.items():
        pct = value / equity * 100
        if pct > MAX_CATEGORY_PCT:
            alerts.append({
                "level": "warning",
                "type": "sector_concentration",
                "message": f"⚠️ {cat} category is {pct:.1f}% of portfolio (max {MAX_CATEGORY_PCT}%)",
                "category": cat,
                "value": pct,
                "threshold": MAX_CATEGORY_PCT,
            })

    return alerts


def check_cash(account):
    """Check cash levels."""
    equity = float(account.get("equity", 0))
    cash = float(account.get("cash", 0))
    if equity <= 0:
        return []

    cash_pct = cash / equity * 100
    if cash_pct < MIN_CASH_PCT:
        return [{
            "level": "warning",
            "type": "low_cash",
            "message": f"💸 Cash at {cash_pct:.1f}% (${cash:,.2f}) — below {MIN_CASH_PCT}% threshold",
            "value": cash_pct,
            "threshold": MIN_CASH_PCT,
        }]
    return []


def check_drawdown():
    """Check drawdown from peak equity."""
    if not PERF_DATA.exists():
        return []

    with open(PERF_DATA) as f:
        data = json.load(f)

    snapshots = data.get("snapshots", [])
    if len(snapshots) < 2:
        return []

    peak = max(s["equity"] for s in snapshots)
    current = snapshots[-1]["equity"]
    drawdown = (peak - current) / peak * 100

    if drawdown > MAX_DRAWDOWN_PCT:
        return [{
            "level": "critical",
            "type": "drawdown",
            "message": f"🔥 Portfolio down {drawdown:.1f}% from peak ${peak:,.2f} → ${current:,.2f}",
            "value": drawdown,
            "peak": peak,
            "current": current,
            "threshold": MAX_DRAWDOWN_PCT,
        }]
    return []


def check_trading_frequency():
    """Check if too many trades in a single day."""
    if not JOURNAL_PATH.exists():
        return []

    today = datetime.now().strftime("%Y-%m-%d")
    count = 0
    with open(JOURNAL_PATH) as f:
        for line in f:
            try:
                entry = json.loads(line.strip())
                if entry.get("timestamp", "")[:10] == today and entry.get("status") == "executed":
                    count += 1
            except json.JSONDecodeError:
                continue

    if count > MAX_DAILY_TRADES:
        return [{
            "level": "warning",
            "type": "high_frequency",
            "message": f"⚡ {count} trades today — exceeds {MAX_DAILY_TRADES} daily threshold",
            "value": count,
            "threshold": MAX_DAILY_TRADES,
        }]
    return []


def audit_decisions(decisions_file):
    """Pre-execution audit of trade decisions."""
    if decisions_file == "-":
        decisions = json.load(sys.stdin)
    else:
        with open(decisions_file) as f:
            decisions = json.load(f)

    account, positions = get_portfolio()
    if not account:
        print("⚠️ Could not fetch portfolio for audit")
        return []

    equity = float(account.get("equity", 0))
    alerts = []
    trades = decisions.get("trades", [])

    for trade in trades:
        ticker = trade.get("ticker", "?")
        qty = int(trade.get("qty", 0))
        action = trade.get("action", "")

        # Estimate trade value
        pos = next((p for p in positions if p["symbol"] == ticker), None)
        price = float(pos.get("current_price", 0)) if pos else 0

        if price > 0 and equity > 0:
            trade_value = qty * price
            trade_pct = trade_value / equity * 100
            if trade_pct > MAX_TRADE_SIZE_PCT:
                alerts.append({
                    "level": "warning",
                    "type": "large_trade",
                    "message": f"⚠️ {action.upper()} {ticker} x{qty} = ${trade_value:,.0f} ({trade_pct:.1f}% of portfolio)",
                    "ticker": ticker,
                    "value": trade_pct,
                    "threshold": MAX_TRADE_SIZE_PCT,
                })

    return alerts


def log_alerts(alerts):
    """Append alerts to persistent log."""
    ALERTS_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(ALERTS_LOG, "a") as f:
        for alert in alerts:
            alert["timestamp"] = datetime.now().isoformat()
            f.write(json.dumps(alert) + "\n")


def main():
    parser = argparse.ArgumentParser(description="Cassius Trade Alerts")
    parser.add_argument("--check", action="store_true", help="Run all alert checks")
    parser.add_argument("--audit", type=str, help="Audit trade decisions before execution")
    parser.add_argument("--telegram", action="store_true", help="Telegram format")
    args = parser.parse_args()

    alerts = []

    if args.audit:
        alerts = audit_decisions(args.audit)
    elif args.check:
        account, positions = get_portfolio()
        if account and positions:
            alerts.extend(check_concentration(account, positions))
            alerts.extend(check_cash(account))
        alerts.extend(check_drawdown())
        alerts.extend(check_trading_frequency())
    else:
        parser.print_help()
        return

    if alerts:
        log_alerts(alerts)

        if args.telegram:
            critical = [a for a in alerts if a.get("level") == "critical"]
            warnings = [a for a in alerts if a.get("level") == "warning"]
            print(f"🚨 Cassius Alert — {len(alerts)} issue{'s' if len(alerts) != 1 else ''}")
            for a in critical:
                print(f"  {a['message']}")
            for a in warnings:
                print(f"  {a['message']}")
        else:
            print(f"{'='*50}")
            print(f"  CASSIUS ALERTS — {len(alerts)} issue{'s' if len(alerts) != 1 else ''}")
            print(f"{'='*50}")
            for a in alerts:
                level = "🔴 CRITICAL" if a["level"] == "critical" else "🟡 WARNING"
                print(f"  {level}: {a['message']}")
            print()
    else:
        if args.telegram:
            print("✅ No alerts — all within thresholds")
        else:
            print("✅ All clear — no alerts triggered")


if __name__ == "__main__":
    main()
