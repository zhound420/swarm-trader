#!/usr/bin/env python3
"""
Trade Journal — Persistent log of every Cassius trade decision and outcome.

Append mode:
  poetry run python trade_journal.py --log decisions.json    # Log from executor output
  echo '{"results":[...]}' | poetry run python trade_journal.py --log -

Query mode:
  poetry run python trade_journal.py --show                   # Last 10 trades
  poetry run python trade_journal.py --show --limit 50        # Last 50
  poetry run python trade_journal.py --show --ticker NVDA     # Filter by ticker
  poetry run python trade_journal.py --stats                  # Win/loss stats
  poetry run python trade_journal.py --stats --days 7         # Stats for last 7 days
  poetry run python trade_journal.py --telegram               # Summary for Telegram
"""

import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

JOURNAL_PATH = Path(__file__).parent / "data" / "trade_journal.jsonl"


def ensure_dir():
    JOURNAL_PATH.parent.mkdir(parents=True, exist_ok=True)


def append_trades(execution_result: dict):
    """Append executed trades from execute_trades.py output."""
    ensure_dir()
    results = execution_result.get("results", [])
    timestamp = execution_result.get("timestamp", datetime.now().isoformat())
    mode = execution_result.get("mode", "unknown")

    logged = 0
    with open(JOURNAL_PATH, "a") as f:
        for trade in results:
            if trade.get("status") in ("skipped",):
                continue
            entry = {
                "timestamp": timestamp,
                "mode": mode,
                "ticker": trade.get("ticker"),
                "action": trade.get("action"),
                "qty": trade.get("qty", 0),
                "status": trade.get("status"),
                "reasoning": trade.get("reasoning", ""),
                "order_id": trade.get("order_id"),
                "error": trade.get("error"),
            }
            f.write(json.dumps(entry) + "\n")
            logged += 1

    return logged


def read_journal(limit=None, ticker=None, days=None):
    """Read journal entries with optional filters."""
    if not JOURNAL_PATH.exists():
        return []

    entries = []
    cutoff = None
    if days:
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()

    with open(JOURNAL_PATH) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            if ticker and entry.get("ticker") != ticker.upper():
                continue
            if cutoff and entry.get("timestamp", "") < cutoff:
                continue
            entries.append(entry)

    if limit:
        entries = entries[-limit:]
    return entries


def compute_stats(entries):
    """Compute win/loss stats from journal entries."""
    executed = [e for e in entries if e.get("status") == "executed"]
    blocked = [e for e in entries if e.get("status") == "blocked"]
    failed = [e for e in entries if e.get("status") == "failed"]

    buys = [e for e in executed if e.get("action") in ("buy", "cover")]
    sells = [e for e in executed if e.get("action") in ("sell", "short")]

    # Ticker frequency
    ticker_counts = {}
    for e in executed:
        t = e.get("ticker", "?")
        ticker_counts[t] = ticker_counts.get(t, 0) + 1

    # Action frequency by day
    daily = {}
    for e in executed:
        day = e.get("timestamp", "")[:10]
        if day not in daily:
            daily[day] = {"buys": 0, "sells": 0, "total": 0}
        daily[day]["total"] += 1
        if e.get("action") in ("buy", "cover"):
            daily[day]["buys"] += 1
        else:
            daily[day]["sells"] += 1

    return {
        "total_entries": len(entries),
        "executed": len(executed),
        "blocked": len(blocked),
        "failed": len(failed),
        "buys": len(buys),
        "sells": len(sells),
        "unique_tickers": len(ticker_counts),
        "most_traded": sorted(ticker_counts.items(), key=lambda x: -x[1])[:5],
        "active_days": len(daily),
        "daily_breakdown": dict(sorted(daily.items(), reverse=True)[:10]),
    }


def show_trades(entries):
    """Display trades in a readable format."""
    if not entries:
        print("No trades in journal.")
        return

    print(f"{'Timestamp':<22} {'Ticker':<7} {'Action':<6} {'Qty':>6} {'Status':<10} {'Reasoning'}")
    print("-" * 100)
    for e in entries:
        ts = e.get("timestamp", "")[:19]
        reasoning = (e.get("reasoning") or "")[:40]
        print(f"{ts:<22} {e.get('ticker','?'):<7} {e.get('action','?'):<6} {e.get('qty',0):>6} {e.get('status','?'):<10} {reasoning}")


def show_stats(stats):
    """Display stats in terminal."""
    print(f"{'='*50}")
    print(f"  CASSIUS TRADE JOURNAL — STATS")
    print(f"{'='*50}")
    print(f"  Total entries:   {stats['total_entries']}")
    print(f"  Executed:        {stats['executed']}")
    print(f"  Blocked:         {stats['blocked']}")
    print(f"  Failed:          {stats['failed']}")
    print(f"  Buys:            {stats['buys']}")
    print(f"  Sells:           {stats['sells']}")
    print(f"  Unique tickers:  {stats['unique_tickers']}")
    print(f"  Active days:     {stats['active_days']}")

    if stats["most_traded"]:
        print(f"\n  Most Traded:")
        for ticker, count in stats["most_traded"]:
            print(f"    {ticker}: {count} trades")

    if stats["daily_breakdown"]:
        print(f"\n  Recent Daily Activity:")
        for day, d in stats["daily_breakdown"].items():
            print(f"    {day}: {d['total']} trades ({d['buys']}B / {d['sells']}S)")
    print()


def telegram_summary(stats, entries):
    """Telegram-formatted summary."""
    last_5 = entries[-5:] if entries else []

    print("📓 Cassius Trade Journal")
    print(f"📊 {stats['executed']} executed | {stats['blocked']} blocked | {stats['failed']} failed")
    print(f"📈 {stats['buys']} buys | 📉 {stats['sells']} sells | 🎯 {stats['unique_tickers']} tickers")
    print(f"📅 {stats['active_days']} active trading days")

    if stats["most_traded"]:
        top = ", ".join(f"{t} ({c})" for t, c in stats["most_traded"][:3])
        print(f"🔥 Most traded: {top}")

    if last_5:
        print(f"\n🕐 Last {len(last_5)} trades:")
        for e in last_5:
            emoji = "🟢" if e.get("action") in ("buy", "cover") else "🔴"
            print(f"  {emoji} {e.get('ticker')} — {e.get('action')} {e.get('qty',0)} ({e.get('status','?')})")


def main():
    parser = argparse.ArgumentParser(description="Cassius Trade Journal")
    parser.add_argument("--log", type=str, help="Log trades from executor JSON output (file or - for stdin)")
    parser.add_argument("--show", action="store_true", help="Show recent trades")
    parser.add_argument("--stats", action="store_true", help="Show trade statistics")
    parser.add_argument("--telegram", action="store_true", help="Telegram-formatted summary")
    parser.add_argument("--limit", type=int, default=10, help="Number of trades to show")
    parser.add_argument("--ticker", type=str, help="Filter by ticker")
    parser.add_argument("--days", type=int, help="Filter to last N days")
    args = parser.parse_args()

    if args.log:
        if args.log == "-":
            data = json.load(sys.stdin)
        else:
            with open(args.log) as f:
                data = json.load(f)
        logged = append_trades(data)
        print(f"✅ Logged {logged} trades to journal")
        return

    entries = read_journal(
        limit=args.limit if args.show else None,
        ticker=args.ticker,
        days=args.days,
    )

    if args.telegram:
        stats = compute_stats(entries)
        telegram_summary(stats, entries)
    elif args.stats:
        stats = compute_stats(entries)
        show_stats(stats)
    elif args.show:
        show_trades(entries)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
