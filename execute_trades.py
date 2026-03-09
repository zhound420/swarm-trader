#!/usr/bin/env python3
"""
Trade Executor — Takes Cassius's JSON decisions and executes via Alpaca.

Input: JSON on stdin or --file with format:
{
  "trades": [
    {"ticker": "NVDA", "action": "buy", "qty": 50, "reasoning": "..."},
    {"ticker": "XLE", "action": "sell", "qty": 100, "reasoning": "..."}
  ]
}

Usage:
  echo '{"trades":[...]}' | poetry run python execute_trades.py
  poetry run python execute_trades.py --file decisions.json
  poetry run python execute_trades.py --file decisions.json --dry-run
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
    "Content-Type": "application/json",
}

# Safety rails
MAX_TRADE_PCT = 0.10
MAX_TRADES = 8
MIN_KEEP_PCT = 0.05


def get_account():
    r = requests.get(f"{API_BASE}/account", headers=HEADERS, timeout=10)
    r.raise_for_status()
    return r.json()


def get_positions():
    r = requests.get(f"{API_BASE}/positions", headers=HEADERS, timeout=10)
    r.raise_for_status()
    return {p["symbol"]: p for p in r.json()}


def place_order(ticker, action, qty):
    """Place a market order."""
    side = "buy" if action in ("buy", "cover") else "sell"
    order = {
        "symbol": ticker,
        "qty": str(int(qty)),
        "side": side,
        "type": "market",
        "time_in_force": "day",
    }
    r = requests.post(f"{API_BASE}/orders", headers=HEADERS, json=order, timeout=10)
    if r.status_code in (200, 201):
        data = r.json()
        return {"success": True, "order_id": data.get("id"), "status": data.get("status")}
    return {"success": False, "error": f"HTTP {r.status_code}: {r.text[:200]}"}


def validate_trade(ticker, action, qty, positions, portfolio_value):
    """Validate against safety rails."""
    pos = positions.get(ticker, {})
    current_price = float(pos.get("current_price", 0))
    current_shares = float(pos.get("qty", 0))

    if action == "sell" and current_shares <= 0:
        return False, f"No position in {ticker} to sell"

    if action == "sell" and current_shares > 0:
        min_keep = max(1, int(current_shares * MIN_KEEP_PCT))
        max_sell = int(current_shares) - min_keep
        if qty > max_sell:
            return False, f"Would exceed sell limit. Max: {max_sell} (keeping {min_keep})"

    if current_price > 0:
        trade_value = qty * current_price
        max_value = portfolio_value * MAX_TRADE_PCT
        if trade_value > max_value:
            return False, f"Trade value ${trade_value:,.0f} exceeds max ${max_value:,.0f} (10% of portfolio)"

    return True, ""


def main():
    parser = argparse.ArgumentParser(description="Execute Cassius's trade decisions")
    parser.add_argument("--file", type=str, help="JSON file with trade decisions")
    parser.add_argument("--dry-run", action="store_true", help="Validate but don't execute")
    args = parser.parse_args()

    # Read decisions
    if args.file:
        with open(args.file) as f:
            decisions = json.load(f)
    else:
        decisions = json.load(sys.stdin)

    trades = decisions.get("trades", [])
    if not trades:
        print(json.dumps({"status": "no_trades", "message": "No trades to execute"}))
        return 0

    # Get current state
    account = get_account()
    positions = get_positions()
    portfolio_value = float(account.get("equity", 0))

    results = []
    executed = 0

    for trade in trades[:MAX_TRADES]:
        ticker = trade["ticker"]
        action = trade["action"].lower()
        qty = int(trade.get("qty", 0))
        reasoning = trade.get("reasoning", "")

        if action == "hold" or qty <= 0:
            results.append({"ticker": ticker, "action": action, "status": "skipped", "reason": "Hold or zero qty"})
            continue

        # Validate
        valid, reason = validate_trade(ticker, action, qty, positions, portfolio_value)
        if not valid:
            results.append({"ticker": ticker, "action": action, "qty": qty, "status": "blocked", "reason": reason})
            continue

        if args.dry_run:
            results.append({"ticker": ticker, "action": action, "qty": qty, "status": "would_execute", "reasoning": reasoning})
            executed += 1
        else:
            result = place_order(ticker, action, qty)
            status = "executed" if result["success"] else "failed"
            results.append({
                "ticker": ticker,
                "action": action,
                "qty": qty,
                "status": status,
                "reasoning": reasoning,
                **result,
            })
            if result["success"]:
                executed += 1

    output = {
        "timestamp": datetime.now().isoformat(),
        "mode": "dry_run" if args.dry_run else "live",
        "total_trades": len(trades),
        "executed": executed,
        "blocked": len([r for r in results if r.get("status") == "blocked"]),
        "results": results,
    }

    print(json.dumps(output, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
