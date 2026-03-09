#!/usr/bin/env python3
"""
Fast Alpaca Portfolio Check — No LLM, just data.

Usage:
  poetry run python check_portfolio.py              # Summary
  poetry run python check_portfolio.py --telegram   # Telegram-formatted
  poetry run python check_portfolio.py --json       # JSON output
"""

import argparse
import json
import os
import sys
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

import requests

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


def api(endpoint):
    r = requests.get(f"{API_BASE}/{endpoint}", headers=HEADERS)
    r.raise_for_status()
    return r.json()


def main():
    parser = argparse.ArgumentParser(description="Fast Alpaca portfolio check")
    parser.add_argument("--telegram", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    account = api("account")
    positions = api("positions")

    equity = float(account["equity"])
    cash = float(account["cash"])
    last_equity = float(account.get("last_equity", equity))
    daily_pl = equity - last_equity
    daily_pl_pct = (daily_pl / last_equity * 100) if last_equity else 0

    # Process positions
    pos_data = []
    for p in positions:
        sym = p["symbol"]
        qty = float(p["qty"])
        market_value = float(p["market_value"])
        unrealized_pl = float(p["unrealized_pl"])
        unrealized_plpc = float(p["unrealized_plpc"]) * 100
        current_price = float(p["current_price"])
        avg_entry = float(p["avg_entry_price"])
        in_universe = sym in ALL_UNIVERSE
        category = next((k for k, v in UNIVERSE.items() if sym in v), "other")

        pos_data.append({
            "symbol": sym,
            "qty": qty,
            "market_value": market_value,
            "unrealized_pl": unrealized_pl,
            "unrealized_plpc": unrealized_plpc,
            "current_price": current_price,
            "avg_entry": avg_entry,
            "in_universe": in_universe,
            "category": category,
            "weight": (market_value / equity * 100) if equity else 0,
        })

    pos_data.sort(key=lambda x: x["market_value"], reverse=True)

    # Big movers (>5% swing)
    big_movers = [p for p in pos_data if abs(p["unrealized_plpc"]) > 5]

    # Out-of-universe positions
    out_of_universe = [p for p in pos_data if not p["in_universe"] and p["market_value"] > 100]

    if args.json:
        print(json.dumps({
            "equity": equity,
            "cash": cash,
            "daily_pl": daily_pl,
            "daily_pl_pct": daily_pl_pct,
            "positions": pos_data,
            "big_movers": [p["symbol"] for p in big_movers],
            "out_of_universe": [p["symbol"] for p in out_of_universe],
            "timestamp": datetime.now().isoformat(),
        }, indent=2))
        return

    if args.telegram:
        output_telegram(equity, cash, daily_pl, daily_pl_pct, pos_data, big_movers, out_of_universe)
    else:
        output_terminal(equity, cash, daily_pl, daily_pl_pct, pos_data, big_movers, out_of_universe)


def output_telegram(equity, cash, daily_pl, daily_pl_pct, positions, big_movers, out_of_universe):
    pl_emoji = "📈" if daily_pl >= 0 else "📉"
    print(f"💰 Mordecai Fund — Portfolio Check")
    print(f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M PST')}")
    print()
    print(f"Portfolio: ${equity:,.2f}")
    print(f"Cash: ${cash:,.2f} ({cash/equity*100:.1f}%)")
    print(f"{pl_emoji} Daily P/L: ${daily_pl:+,.2f} ({daily_pl_pct:+.2f}%)")
    print()

    # Top 5 by value
    print("📊 Top 5 Holdings:")
    for p in positions[:5]:
        pl_sign = "+" if p["unrealized_pl"] >= 0 else ""
        print(f"  • {p['symbol']}: ${p['market_value']:,.2f} ({p['weight']:.1f}%) — {pl_sign}${p['unrealized_pl']:,.2f}")
    print()

    # Category breakdown
    categories = {}
    for p in positions:
        cat = p["category"]
        categories[cat] = categories.get(cat, 0) + p["market_value"]
    
    print("🏷️ Allocation:")
    for cat in ["ai_infra", "leveraged", "momentum", "moonshots", "other"]:
        if cat in categories:
            pct = categories[cat] / equity * 100
            label = cat.replace("_", " ").title()
            print(f"  • {label}: ${categories[cat]:,.2f} ({pct:.1f}%)")
    print()

    if big_movers:
        print("🚨 Big Movers (>5% swing):")
        for p in big_movers:
            print(f"  • {p['symbol']}: {p['unrealized_plpc']:+.1f}%")
        print()

    if out_of_universe:
        print("⚠️ Out of Universe:")
        for p in out_of_universe:
            print(f"  • {p['symbol']}: ${p['market_value']:,.2f} ({p['weight']:.1f}%)")


def output_terminal(equity, cash, daily_pl, daily_pl_pct, positions, big_movers, out_of_universe):
    print(f"{'='*60}")
    print(f"  MORDECAI FUND — PORTFOLIO CHECK")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M PST')}")
    print(f"{'='*60}")
    print(f"  Equity:   ${equity:>12,.2f}")
    print(f"  Cash:     ${cash:>12,.2f}  ({cash/equity*100:.1f}%)")
    print(f"  Daily P/L: ${daily_pl:>+11,.2f}  ({daily_pl_pct:+.2f}%)")
    print(f"{'='*60}")
    print()

    print(f"  {'Symbol':<8} {'Qty':>6} {'Value':>12} {'Weight':>7} {'P/L':>10} {'P/L%':>7} {'Cat':<10}")
    print(f"  {'-'*62}")
    for p in positions:
        if p["market_value"] < 1:
            continue
        print(f"  {p['symbol']:<8} {p['qty']:>6.0f} ${p['market_value']:>10,.2f} {p['weight']:>6.1f}% ${p['unrealized_pl']:>+9,.2f} {p['unrealized_plpc']:>+6.1f}% {p['category']:<10}")

    if big_movers:
        print(f"\n  🚨 BIG MOVERS (>5% swing):")
        for p in big_movers:
            print(f"    {p['symbol']}: {p['unrealized_plpc']:+.1f}%")

    if out_of_universe:
        print(f"\n  ⚠️  OUT OF UNIVERSE:")
        for p in out_of_universe:
            print(f"    {p['symbol']}: ${p['market_value']:,.2f} ({p['weight']:.1f}%)")
    print()


if __name__ == "__main__":
    sys.exit(main() or 0)
