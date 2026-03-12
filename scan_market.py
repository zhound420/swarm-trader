#!/usr/bin/env python3
"""
Market Scanner — Discovers today's tradeable opportunities dynamically.

Pulls top movers, most active stocks, and filters for day-trading quality:
- Min price $10 (no penny stocks)
- Min trade count (liquidity)
- No warrants, units, or rights (strips W, U, R suffixes)
- Merges with core watchlist for a final ticker set

Output: comma-separated ticker list (stdout) or JSON (--json)

Usage:
  poetry run python scan_market.py                    # Print tickers
  poetry run python scan_market.py --json             # Full JSON with metadata
  poetry run python scan_market.py --max 20           # Limit to 20 tickers
  poetry run python scan_market.py --min-price 15     # Higher price floor
  poetry run python scan_market.py --no-core          # Skip core watchlist

Pipeline:
  TICKERS=$(poetry run python scan_market.py)
  poetry run python gather_data.py --mode day --tickers $TICKERS --output /tmp/data.json
"""

import argparse
import json
import os
import sys
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

import requests

# Alpaca endpoints
SCREENER_BASE = "https://data.alpaca.markets/v1beta1/screener"
DATA_BASE = "https://data.alpaca.markets/v2"

HEADERS = {
    "APCA-API-KEY-ID": os.environ.get("ALPACA_API_KEY", ""),
    "APCA-API-SECRET-KEY": os.environ.get("ALPACA_API_SECRET", ""),
}

# Core watchlist — always included regardless of scanner results.
# These are high-liquidity names Cassius should always have eyes on.
CORE_WATCHLIST = [
    "NVDA", "AVGO", "TSM", "AMD", "MSFT", "AAPL", "META", "GOOGL", "AMZN",  # mega-cap tech
    "SPY", "QQQ",  # market direction
]

# Symbols to always exclude (ETNs, leveraged products we don't want, etc.)
EXCLUDE = {
    "TQQQ", "SQQQ", "SOXL", "SOXS", "UPRO", "SPXU", "TZA", "TNA",  # leveraged ETFs
    "UVXY", "VXX", "SVXY",  # VIX products
    "SPDN", "GOVT",  # bond/inverse ETFs
}

# Filter thresholds
DEFAULT_MIN_PRICE = 10.0
DEFAULT_MIN_TRADES = 5000
DEFAULT_MAX_TICKERS = 25


def get_movers(top: int = 20) -> dict:
    """Get top gainers and losers."""
    try:
        r = requests.get(
            f"{SCREENER_BASE}/stocks/movers",
            headers=HEADERS,
            params={"top": top},
            timeout=10,
        )
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"⚠️  Movers API failed: {e}", file=sys.stderr)
        return {"gainers": [], "losers": []}


def get_most_active(top: int = 50) -> list[dict]:
    """Get most active stocks by trade count."""
    try:
        r = requests.get(
            f"{SCREENER_BASE}/stocks/most-actives",
            headers=HEADERS,
            params={"top": top},
            timeout=10,
        )
        r.raise_for_status()
        return r.json().get("most_actives", [])
    except Exception as e:
        print(f"⚠️  Most actives API failed: {e}", file=sys.stderr)
        return []


def get_snapshots(symbols: list[str]) -> dict:
    """Get current price snapshots for a list of symbols."""
    if not symbols:
        return {}
    try:
        r = requests.get(
            f"{DATA_BASE}/stocks/snapshots",
            headers=HEADERS,
            params={"symbols": ",".join(symbols[:50]), "feed": "iex"},
            timeout=15,
        )
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"⚠️  Snapshots API failed: {e}", file=sys.stderr)
        return {}


def is_tradeable_symbol(symbol: str) -> bool:
    """Filter out warrants, units, rights, and other non-tradeable symbols."""
    if not symbol or len(symbol) > 5:
        return False
    # Warrants end in W, units in U, rights in R (when appended to base)
    if symbol.endswith("W") and len(symbol) > 3:
        return False
    if symbol.endswith("U") and len(symbol) > 3:
        return False
    if symbol.endswith("R") and len(symbol) > 3:
        return False
    if symbol in EXCLUDE:
        return False
    return True


def scan(
    min_price: float = DEFAULT_MIN_PRICE,
    min_trades: int = DEFAULT_MIN_TRADES,
    max_tickers: int = DEFAULT_MAX_TICKERS,
    include_core: bool = True,
) -> dict:
    """
    Run the full market scan and return filtered results.

    Returns:
        {
            "timestamp": "...",
            "core_watchlist": ["NVDA", ...],
            "discovered": [{"symbol": "TSLA", "source": "gainer", ...}, ...],
            "tickers": ["NVDA", "TSLA", ...],  # final combined list
        }
    """
    discovered = {}  # symbol -> metadata

    # 1. Pull movers (gainers + losers)
    movers = get_movers(top=20)

    for g in movers.get("gainers", []):
        sym = g.get("symbol", "")
        if not is_tradeable_symbol(sym):
            continue
        price = float(g.get("price", 0))
        pct = float(g.get("percent_change", 0))
        if price >= min_price and abs(pct) < 40:  # skip pump-and-dumps
            discovered[sym] = {
                "symbol": sym,
                "source": "gainer",
                "price": price,
                "change_pct": round(pct, 2),
            }

    for l in movers.get("losers", []):
        sym = l.get("symbol", "")
        if not is_tradeable_symbol(sym):
            continue
        price = float(l.get("price", 0))
        pct = float(l.get("percent_change", 0))
        if price >= min_price and abs(pct) < 40:
            discovered[sym] = {
                "symbol": sym,
                "source": "loser",
                "price": price,
                "change_pct": round(pct, 2),
            }

    # 2. Pull most active by trade count
    actives = get_most_active(top=50)

    for a in actives:
        sym = a.get("symbol", "")
        if not is_tradeable_symbol(sym):
            continue
        trade_count = int(a.get("trade_count", 0))
        volume = int(a.get("volume", 0))
        if trade_count >= min_trades:
            if sym in discovered:
                discovered[sym]["trade_count"] = trade_count
                discovered[sym]["volume"] = volume
                discovered[sym]["source"] += "+active"
            else:
                discovered[sym] = {
                    "symbol": sym,
                    "source": "active",
                    "trade_count": trade_count,
                    "volume": volume,
                }

    # 3. Get price snapshots for actives that don't have prices yet
    need_prices = [s for s, d in discovered.items() if "price" not in d]
    if need_prices:
        snapshots = get_snapshots(need_prices)
        for sym, snap in snapshots.items():
            if sym in discovered:
                latest = snap.get("latestTrade", snap.get("dailyBar", {}))
                price = float(latest.get("p", latest.get("c", 0)))
                discovered[sym]["price"] = price

    # 4. Filter by price
    discovered = {
        s: d for s, d in discovered.items()
        if d.get("price", 0) >= min_price
    }

    # 5. Remove core watchlist from discovered (they'll be added separately)
    core_set = set(CORE_WATCHLIST) if include_core else set()
    for sym in core_set:
        discovered.pop(sym, None)

    # 6. Rank discovered by relevance (movers+active > just active > just mover)
    def rank(item):
        score = 0
        if "+" in item.get("source", ""):
            score += 100  # appears in multiple signals
        if "gainer" in item.get("source", "") or "loser" in item.get("source", ""):
            score += 50  # price is moving
        score += min(item.get("trade_count", 0) / 10000, 50)  # volume bonus
        return score

    ranked = sorted(discovered.values(), key=rank, reverse=True)

    # 7. Trim to max
    slots_for_discovered = max_tickers - (len(CORE_WATCHLIST) if include_core else 0)
    ranked = ranked[:max(slots_for_discovered, 5)]

    # 8. Build final ticker list
    tickers = list(CORE_WATCHLIST) if include_core else []
    for item in ranked:
        if item["symbol"] not in tickers:
            tickers.append(item["symbol"])

    return {
        "timestamp": datetime.now().isoformat(),
        "core_watchlist": list(CORE_WATCHLIST) if include_core else [],
        "discovered": ranked,
        "discovered_count": len(ranked),
        "total_tickers": len(tickers),
        "tickers": tickers,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Scan market for today's tradeable opportunities",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scan_market.py                          # Print comma-separated tickers
  python scan_market.py --json                   # Full JSON output
  python scan_market.py --max 30 --min-price 15  # Custom filters
  
Pipeline:
  TICKERS=$(poetry run python scan_market.py)
  poetry run python gather_data.py --mode day --tickers $TICKERS --output /tmp/data.json
        """,
    )
    parser.add_argument("--json", action="store_true", help="Output full JSON with metadata")
    parser.add_argument("--max", type=int, default=DEFAULT_MAX_TICKERS, help=f"Max total tickers (default: {DEFAULT_MAX_TICKERS})")
    parser.add_argument("--min-price", type=float, default=DEFAULT_MIN_PRICE, help=f"Min stock price (default: ${DEFAULT_MIN_PRICE})")
    parser.add_argument("--min-trades", type=int, default=DEFAULT_MIN_TRADES, help=f"Min trade count for 'most active' (default: {DEFAULT_MIN_TRADES})")
    parser.add_argument("--no-core", action="store_true", help="Skip core watchlist, only show discovered")

    args = parser.parse_args()

    result = scan(
        min_price=args.min_price,
        min_trades=args.min_trades,
        max_tickers=args.max,
        include_core=not args.no_core,
    )

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        # Just print comma-separated tickers for piping
        print(",".join(result["tickers"]))


if __name__ == "__main__":
    main()
