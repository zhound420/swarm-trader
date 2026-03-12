#!/usr/bin/env python3
"""
Market Intel Exchange — Cassius ↔ DROX
Shares research signals via A2A, never positions or decisions.
"""

import json
import os
import sys
import argparse
import urllib.request
import urllib.error
from datetime import datetime, timezone

# Glorft/DROX A2A config
GLORFT_URL = "http://100.106.81.19:9092/a2a/tasks/send"
GLORFT_TOKEN = "An9U8IkH28WoeVDTs3zFrhEzBlrGo1IM"

# Mordecai A2A config (for receiving)
MORDECAI_URL = "http://localhost:9092/a2a/tasks/send"
MORDECAI_TOKEN = "6OFUpWpl5sQZ8KUxDIJ0Kzxykoj4I"


def build_intel_packet(data_file: str, packet_type: str = "daily-brief") -> dict:
    """Build an intel packet from gather_data.py output. Strips all position/portfolio data."""
    
    with open(data_file) as f:
        data = json.load(f)
    
    signals = []
    tickers_watching = []
    anomalies = []
    
    tickers = data.get("ticker_data", data.get("tickers", {}))
    for ticker, info in tickers.items():
        tickers_watching.append(ticker)
        
        prices = info.get("prices", {})
        financials = info.get("fundamentals", info.get("financials", {}))
        news = info.get("news", [])
        insider = info.get("insider_trades", [])
        
        # Determine signal from available data
        # Handle both pct and raw change fields
        change_1d = prices.get("change_1d_pct", prices.get("change_1d", 0)) or 0
        change_5d = prices.get("change_5d_pct", prices.get("change_5d", 0)) or 0
        change_30d = prices.get("change_30d_pct", prices.get("change_30d", 0)) or 0
        
        # Simple signal logic
        if change_5d > 5 and change_30d > 10:
            signal = "bullish"
            confidence = min(0.9, 0.5 + abs(change_5d) / 50)
        elif change_5d < -5 and change_30d < -10:
            signal = "bearish"
            confidence = min(0.9, 0.5 + abs(change_5d) / 50)
        else:
            signal = "neutral"
            confidence = 0.5
        
        # Determine catalyst
        catalyst = "momentum"
        if news:
            catalyst = "news"
        if insider:
            recent_buys = sum(1 for t in insider if t.get("transaction_type") == "buy")
            recent_sells = sum(1 for t in insider if t.get("transaction_type") == "sell")
            if recent_buys > recent_sells:
                catalyst = "insider"
        
        # Build reason
        reasons = []
        if abs(change_1d) > 3:
            reasons.append(f"{'up' if change_1d > 0 else 'down'} {abs(change_1d):.1f}% today")
        if abs(change_5d) > 5:
            reasons.append(f"{'up' if change_5d > 0 else 'down'} {abs(change_5d):.1f}% this week")
        
        pe = financials.get("pe_ratio")
        rev_growth = financials.get("revenue_growth")
        if rev_growth and rev_growth > 0.2:
            reasons.append(f"rev growth {rev_growth*100:.0f}%")
        if pe and pe < 15:
            reasons.append(f"PE {pe:.1f}")
        
        reason = "; ".join(reasons) if reasons else "steady"
        
        signals.append({
            "ticker": ticker,
            "signal": signal,
            "confidence": round(confidence, 2),
            "reason": reason,
            "catalyst": catalyst
        })
        
        # Anomaly detection
        volume = prices.get("volume", prices.get("avg_volume_10d"))
        avg_volume = prices.get("avg_volume", prices.get("avg_volume_10d"))
        if volume and avg_volume and avg_volume > 0:
            vol_ratio = volume / avg_volume
            if vol_ratio > 2.0:
                anomalies.append(f"Unusual volume on {ticker} ({vol_ratio:.1f}x avg)")
        
        if abs(change_1d) > 8:
            anomalies.append(f"{ticker} moved {change_1d:+.1f}% today")
    
    # SPY/macro data
    spy = data.get("spy_benchmark", {})
    spy_change = spy.get("change_1d_pct", 0) or 0
    
    macro_sentiment = "mixed"
    if spy_change > 1:
        macro_sentiment = "risk-on"
    elif spy_change < -1:
        macro_sentiment = "risk-off"
    
    packet = {
        "from": "cassius",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "type": packet_type,
        "tickers_watching": tickers_watching,
        "signals": signals,
        "macro": {
            "sentiment": macro_sentiment,
            "spy_change_1d": round(spy_change, 2) if spy_change else None,
            "key_events": [],  # Could be enriched with economic calendar
            "sector_rotation": ""
        },
        "anomalies": anomalies
    }
    
    return packet


def send_intel(packet: dict, target: str = "drox") -> dict:
    """Send intel packet to DROX via Glorft's A2A endpoint."""
    
    # Format as readable message for the receiving agent
    msg_lines = [
        f"📡 MARKET INTEL from Cassius ({packet['type']})",
        f"Timestamp: {packet['timestamp']}",
        f"Macro: {packet['macro']['sentiment']}" + (f" (SPY {packet['macro']['spy_change_1d']:+.1f}%)" if packet['macro'].get('spy_change_1d') else ""),
        "",
        "📊 SIGNALS:"
    ]
    
    for s in packet["signals"]:
        emoji = {"bullish": "🟢", "bearish": "🔴", "neutral": "⚪"}.get(s["signal"], "⚪")
        msg_lines.append(f"  {emoji} {s['ticker']}: {s['signal'].upper()} ({s['confidence']:.0%}) — {s['reason']} [{s['catalyst']}]")
    
    if packet["anomalies"]:
        msg_lines.extend(["", "⚠️ ANOMALIES:"])
        for a in packet["anomalies"]:
            msg_lines.append(f"  • {a}")
    
    msg_lines.extend([
        "",
        "---",
        "This is shared research only. No position data included. Form your own thesis."
    ])
    
    message_text = "\n".join(msg_lines)
    
    # Also attach raw JSON for programmatic consumption
    payload = {
        "jsonrpc": "2.0",
        "method": "message/send",
        "id": f"intel-{datetime.now().strftime('%Y%m%d-%H%M%S')}",
        "params": {
            "message": {
                "role": "user",
                "parts": [{"type": "text", "text": message_text}]
            },
            "metadata": {
                "skillId": "trading" if target == "drox" else "general",
                "intel_packet": True
            }
        }
    }
    
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        GLORFT_URL,
        data=data,
        headers={
            "Authorization": f"Bearer {GLORFT_TOKEN}",
            "Content-Type": "application/json"
        },
        method="POST"
    )
    
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read())
            return result
    except urllib.error.URLError as e:
        return {"error": str(e)}


def main():
    parser = argparse.ArgumentParser(description="Market Intel Exchange")
    parser.add_argument("--data", default="/tmp/cassius-market-data.json",
                       help="Path to gather_data.py output")
    parser.add_argument("--type", default="daily-brief",
                       choices=["daily-brief", "anomaly", "sector-signal", "evening-debrief"],
                       help="Intel packet type")
    parser.add_argument("--target", default="drox", choices=["drox", "glorft"],
                       help="Target agent")
    parser.add_argument("--dry-run", action="store_true",
                       help="Print packet without sending")
    parser.add_argument("--json", action="store_true",
                       help="Output raw JSON packet")
    
    args = parser.parse_args()
    
    if not os.path.exists(args.data):
        print(f"❌ Data file not found: {args.data}")
        print("Run gather_data.py first to generate market data.")
        sys.exit(1)
    
    packet = build_intel_packet(args.data, args.type)
    
    if args.json:
        print(json.dumps(packet, indent=2))
        return
    
    if args.dry_run:
        print("🔍 DRY RUN — Intel packet preview:\n")
        for s in packet["signals"]:
            emoji = {"bullish": "🟢", "bearish": "🔴", "neutral": "⚪"}.get(s["signal"], "⚪")
            print(f"  {emoji} {s['ticker']}: {s['signal'].upper()} ({s['confidence']:.0%}) — {s['reason']}")
        if packet["anomalies"]:
            print("\n⚠️ Anomalies:")
            for a in packet["anomalies"]:
                print(f"  • {a}")
        print(f"\nMacro: {packet['macro']['sentiment']}")
        print(f"Tickers: {len(packet['tickers_watching'])}")
        return
    
    print(f"📡 Sending {args.type} intel to {args.target}...")
    result = send_intel(packet, target=args.target)
    
    if "error" in result:
        print(f"❌ Failed: {result['error']}")
        sys.exit(1)
    
    # Extract response
    resp = result.get("result", {})
    status = resp.get("status", {})
    msg = status.get("message", {})
    parts = msg.get("parts", [])
    text = parts[0].get("text", "") if parts else ""
    
    print(f"✅ Delivered. {args.target.upper()} response:")
    print(text[:500])


if __name__ == "__main__":
    main()
