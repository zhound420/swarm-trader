#!/usr/bin/env python3
"""
Performance Tracker — Daily equity snapshots with SPY benchmark comparison.

Records daily snapshots and computes cumulative performance vs SPY.

Record:
  poetry run python performance_tracker.py --snapshot          # Record today's snapshot
  poetry run python performance_tracker.py --snapshot --force  # Overwrite today's entry

Query:
  poetry run python performance_tracker.py --report            # Terminal report
  poetry run python performance_tracker.py --telegram          # Telegram-formatted
  poetry run python performance_tracker.py --json              # JSON output
  poetry run python performance_tracker.py --days 30           # Last 30 days only
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

DATA_PATH = Path(__file__).parent / "data" / "performance.json"
API_BASE = "https://paper-api.alpaca.markets/v2"
HEADERS = {
    "APCA-API-KEY-ID": os.environ.get("ALPACA_API_KEY", ""),
    "APCA-API-SECRET-KEY": os.environ.get("ALPACA_API_SECRET", ""),
}


def ensure_dir():
    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)


def load_data():
    ensure_dir()
    if DATA_PATH.exists():
        with open(DATA_PATH) as f:
            return json.load(f)
    return {"snapshots": [], "baseline": None}


def save_data(data):
    ensure_dir()
    with open(DATA_PATH, "w") as f:
        json.dump(data, f, indent=2)


def get_spy_price():
    """Get current SPY price via yfinance."""
    try:
        sys.path.insert(0, str(Path(__file__).parent))
        from src.tools.api_free import get_prices
        end = datetime.now().strftime("%Y-%m-%d")
        start = (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d")
        prices = get_prices("SPY", start, end)
        if prices:
            return prices[-1].close
    except Exception as e:
        print(f"⚠️ SPY fetch failed: {e}", file=sys.stderr)
    return None


def get_portfolio():
    """Get current portfolio state from Alpaca."""
    try:
        r = requests.get(f"{API_BASE}/account", headers=HEADERS, timeout=10)
        r.raise_for_status()
        account = r.json()

        r2 = requests.get(f"{API_BASE}/positions", headers=HEADERS, timeout=10)
        r2.raise_for_status()
        positions = r2.json()

        return {
            "equity": float(account["equity"]),
            "cash": float(account["cash"]),
            "position_count": len(positions),
            "invested": float(account["equity"]) - float(account["cash"]),
        }
    except Exception as e:
        print(f"⚠️ Portfolio fetch failed: {e}", file=sys.stderr)
        return None


def take_snapshot(force=False):
    """Record today's equity + SPY snapshot."""
    data = load_data()
    today = datetime.now().strftime("%Y-%m-%d")

    # Check if already recorded today
    existing = [s for s in data["snapshots"] if s["date"] == today]
    if existing and not force:
        print(f"Already have snapshot for {today}. Use --force to overwrite.")
        return None

    portfolio = get_portfolio()
    if not portfolio:
        print("Failed to get portfolio data.")
        return None

    spy_price = get_spy_price()

    snapshot = {
        "date": today,
        "timestamp": datetime.now().isoformat(),
        "equity": portfolio["equity"],
        "cash": portfolio["cash"],
        "invested": portfolio["invested"],
        "position_count": portfolio["position_count"],
        "spy_price": spy_price,
    }

    # Set baseline on first snapshot
    if not data["baseline"]:
        data["baseline"] = {
            "date": today,
            "equity": portfolio["equity"],
            "spy_price": spy_price,
        }
        print(f"📌 Baseline set: ${portfolio['equity']:,.2f} equity, SPY ${spy_price:,.2f}")

    # Remove existing if force
    if existing and force:
        data["snapshots"] = [s for s in data["snapshots"] if s["date"] != today]

    data["snapshots"].append(snapshot)
    data["snapshots"].sort(key=lambda x: x["date"])
    save_data(data)

    print(f"✅ Snapshot recorded: ${portfolio['equity']:,.2f} equity, SPY ${spy_price:,.2f}")
    return snapshot


def compute_performance(data, days=None):
    """Compute performance metrics from snapshots."""
    snapshots = data["snapshots"]
    baseline = data.get("baseline")

    if not snapshots or not baseline:
        return None

    if days:
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        snapshots = [s for s in snapshots if s["date"] >= cutoff]
        if snapshots:
            baseline_for_period = snapshots[0]
        else:
            return None
    else:
        baseline_for_period = baseline

    latest = snapshots[-1]
    base_equity = baseline_for_period.get("equity", 0)
    base_spy = baseline_for_period.get("spy_price", 0)
    current_equity = latest.get("equity", 0)
    current_spy = latest.get("spy_price", 0)

    portfolio_return = ((current_equity - base_equity) / base_equity * 100) if base_equity else 0
    spy_return = ((current_spy - base_spy) / base_spy * 100) if base_spy else 0
    alpha = portfolio_return - spy_return

    # Daily changes
    daily_changes = []
    for i in range(1, len(snapshots)):
        prev = snapshots[i - 1]["equity"]
        curr = snapshots[i]["equity"]
        pct = ((curr - prev) / prev * 100) if prev else 0
        daily_changes.append({
            "date": snapshots[i]["date"],
            "equity": curr,
            "change": curr - prev,
            "change_pct": pct,
        })

    # Streak
    win_streak = 0
    lose_streak = 0
    for dc in reversed(daily_changes):
        if dc["change_pct"] >= 0:
            win_streak += 1
            if lose_streak > 0:
                break
        else:
            lose_streak += 1
            if win_streak > 0:
                break

    # Best/worst days
    if daily_changes:
        best = max(daily_changes, key=lambda x: x["change_pct"])
        worst = min(daily_changes, key=lambda x: x["change_pct"])
    else:
        best = worst = None

    return {
        "period_start": baseline_for_period.get("date"),
        "period_end": latest["date"],
        "snapshots_count": len(snapshots),
        "starting_equity": base_equity,
        "current_equity": current_equity,
        "portfolio_return_pct": portfolio_return,
        "spy_return_pct": spy_return,
        "alpha": alpha,
        "beating_spy": alpha > 0,
        "cash": latest.get("cash", 0),
        "cash_pct": (latest.get("cash", 0) / current_equity * 100) if current_equity else 0,
        "win_days": len([d for d in daily_changes if d["change_pct"] >= 0]),
        "lose_days": len([d for d in daily_changes if d["change_pct"] < 0]),
        "current_streak": f"+{win_streak}" if win_streak > 0 else f"-{lose_streak}",
        "best_day": best,
        "worst_day": worst,
        "daily_changes": daily_changes[-10:],  # Last 10
    }


def terminal_report(perf):
    if not perf:
        print("No performance data yet. Run --snapshot first.")
        return

    alpha_emoji = "✅" if perf["beating_spy"] else "❌"
    print(f"{'='*60}")
    print(f"  CASSIUS PERFORMANCE TRACKER")
    print(f"  {perf['period_start']} → {perf['period_end']} ({perf['snapshots_count']} snapshots)")
    print(f"{'='*60}")
    print(f"  Starting Equity:  ${perf['starting_equity']:>12,.2f}")
    print(f"  Current Equity:   ${perf['current_equity']:>12,.2f}")
    print(f"  Portfolio Return:  {perf['portfolio_return_pct']:>+11.2f}%")
    print(f"  SPY Return:        {perf['spy_return_pct']:>+11.2f}%")
    print(f"  Alpha:             {perf['alpha']:>+11.2f}%  {alpha_emoji}")
    print(f"  Cash:             ${perf['cash']:>12,.2f}  ({perf['cash_pct']:.1f}%)")
    print(f"  Win/Lose Days:     {perf['win_days']}W / {perf['lose_days']}L")
    print(f"  Current Streak:    {perf['current_streak']}")

    if perf["best_day"]:
        print(f"  Best Day:          {perf['best_day']['date']} ({perf['best_day']['change_pct']:+.2f}%)")
    if perf["worst_day"]:
        print(f"  Worst Day:         {perf['worst_day']['date']} ({perf['worst_day']['change_pct']:+.2f}%)")

    if perf["daily_changes"]:
        print(f"\n  Recent Daily P/L:")
        for dc in perf["daily_changes"]:
            emoji = "🟢" if dc["change_pct"] >= 0 else "🔴"
            print(f"    {emoji} {dc['date']}: ${dc['change']:+,.2f} ({dc['change_pct']:+.2f}%) → ${dc['equity']:,.2f}")
    print()


def telegram_report(perf):
    if not perf:
        print("📊 No performance data yet.")
        return

    alpha_emoji = "🏆" if perf["beating_spy"] else "😤"
    direction = "📈" if perf["portfolio_return_pct"] >= 0 else "📉"

    print(f"📊 Cassius Performance Report")
    print(f"📅 {perf['period_start']} → {perf['period_end']}")
    print()
    print(f"{direction} Portfolio: {perf['portfolio_return_pct']:+.2f}%")
    print(f"📈 SPY: {perf['spy_return_pct']:+.2f}%")
    print(f"{alpha_emoji} Alpha: {perf['alpha']:+.2f}%")
    print()
    print(f"💰 Equity: ${perf['current_equity']:,.2f}")
    print(f"💵 Cash: ${perf['cash']:,.2f} ({perf['cash_pct']:.1f}%)")
    print(f"📅 {perf['win_days']}W / {perf['lose_days']}L | Streak: {perf['current_streak']}")

    if perf["best_day"]:
        print(f"🏅 Best: {perf['best_day']['date']} ({perf['best_day']['change_pct']:+.2f}%)")
    if perf["worst_day"]:
        print(f"💀 Worst: {perf['worst_day']['date']} ({perf['worst_day']['change_pct']:+.2f}%)")


def main():
    parser = argparse.ArgumentParser(description="Cassius Performance Tracker")
    parser.add_argument("--snapshot", action="store_true", help="Record today's equity snapshot")
    parser.add_argument("--force", action="store_true", help="Overwrite today's snapshot")
    parser.add_argument("--report", action="store_true", help="Show performance report")
    parser.add_argument("--telegram", action="store_true", help="Telegram-formatted report")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--days", type=int, help="Limit to last N days")
    args = parser.parse_args()

    if args.snapshot:
        take_snapshot(force=args.force)
        return

    data = load_data()
    perf = compute_performance(data, days=args.days)

    if args.json:
        print(json.dumps(perf, indent=2, default=str))
    elif args.telegram:
        telegram_report(perf)
    elif args.report:
        terminal_report(perf)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
