# Market Intel Exchange Protocol

## Overview
Lightweight research-sharing protocol between Cassius (Mordecai/Zo) and DROX (Glorft/Kenny). 
Share signals, not decisions. Each agent trades independently.

## Principles
1. **Research flows, decisions don't.** Share what you see, not what you're doing.
2. **No position data.** Never share portfolio holdings, sizes, or P&L.
3. **Async.** Intel drops happen on schedule, not blocking trade execution.
4. **Structured.** JSON format so receiving agent can parse and integrate.

## Intel Packet Format
```json
{
  "from": "cassius|drox",
  "timestamp": "ISO-8601",
  "type": "daily-brief|anomaly|sector-signal|earnings-alert",
  "tickers_watching": ["NVDA", "AVGO", "TSM"],
  "signals": [
    {
      "ticker": "NVDA",
      "signal": "bullish|bearish|neutral",
      "confidence": 0.85,
      "reason": "One-line thesis",
      "catalyst": "earnings|insider|momentum|macro|technical"
    }
  ],
  "macro": {
    "sentiment": "risk-on|risk-off|mixed",
    "key_events": ["Fed minutes Wednesday", "CPI Thursday"],
    "sector_rotation": "into tech, out of energy"
  },
  "anomalies": [
    "Unusual volume on SMCI (3x avg)",
    "SEC 13F filing: Bridgewater added PLTR"
  ]
}
```

## Exchange Schedule
- **Morning brief:** After each agent's morning analysis, share intel packet
- **Anomaly alerts:** Real-time when something unusual surfaces
- **Evening debrief:** End-of-day signals and macro read

## Boundaries (hard rules)
- ❌ No sharing: positions, quantities, P&L, account equity, trade orders
- ❌ No copying: receiving agent must form independent thesis
- ❌ No instructions: "you should buy X" is forbidden
- ✅ OK to share: tickers of interest, directional signals, macro reads, anomalies, research
