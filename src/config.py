"""Shared configuration for the AI Hedge Fund / Swarm Trader platform."""

# ---------------------------------------------------------------------------
# Swing Trading Universe — buy-and-hold, fundamentals-focused
# ---------------------------------------------------------------------------
SWING_UNIVERSE = {
    "ai_infra": {
        "label": "AI Infrastructure",
        "tickers": ["NVDA", "AVGO", "SMCI", "TSM"],
        "target_pct": 0.40,
    },
    "leveraged_etfs": {
        "label": "Leveraged ETFs",
        "tickers": ["TQQQ", "SOXL", "UPRO"],
        "target_pct": 0.25,
    },
    "momentum": {
        "label": "Momentum Plays",
        "tickers": ["PLTR", "MSTR", "COIN", "RKLB"],
        "target_pct": 0.20,
    },
    "moonshots": {
        "label": "Moonshots",
        "tickers": ["IONQ", "RGTI", "SOUN", "LUNR"],
        "target_pct": 0.15,
    },
}

# Backward-compat alias — existing scripts import UNIVERSE
UNIVERSE = SWING_UNIVERSE

# Flat list / simple category map for swing universe
ALL_UNIVERSE_TICKERS = [t for cat in SWING_UNIVERSE.values() for t in cat["tickers"]]
UNIVERSE_SIMPLE = {key: cat["tickers"] for key, cat in SWING_UNIVERSE.items()}


# ---------------------------------------------------------------------------
# Day Trading Universe — liquid, intraday only, no leveraged ETFs overnight
#
# Excluded from swing universe:
#   IONQ, RGTI, SOUN, LUNR  — too illiquid for day trading (wide spreads)
#   TQQQ, SOXL, UPRO        — 3x leverage = dangerous overnight gap risk
# ---------------------------------------------------------------------------
DAY_TRADE_UNIVERSE = {
    "mega_cap_tech": {
        "label": "Mega-Cap Tech",
        "tickers": ["NVDA", "AVGO", "TSM", "AMD", "MSFT", "AAPL", "META", "GOOGL", "AMZN"],
        "target_pct": 0.55,
    },
    "momentum": {
        "label": "Momentum",
        "tickers": ["PLTR", "COIN", "MSTR", "RKLB"],
        "target_pct": 0.30,
    },
    "etf_direction": {
        "label": "ETF Direction / Hedge",
        "tickers": ["SPY", "QQQ"],
        "target_pct": 0.15,
    },
}

ALL_DAY_TRADE_TICKERS = [t for cat in DAY_TRADE_UNIVERSE.values() for t in cat["tickers"]]


# ---------------------------------------------------------------------------
# Risk parameters — day trading mode
# ---------------------------------------------------------------------------
MAX_RISK_PER_TRADE        = 0.02   # Risk 2% of portfolio per trade
MAX_PORTFOLIO_HEAT        = 0.10   # Max 10% of portfolio at risk at any time
MAX_POSITION_SIZE         = 0.15   # No single position > 15% of portfolio
DEFAULT_STOP_PCT          = 0.02   # 2% stop loss default (applied when agent omits stop)
DEFAULT_TARGET_MULTIPLIER = 2.0    # 2:1 reward:risk — target = entry ± (stop_dist * 2)
FLATTEN_BY                = "15:45"  # Flatten speculative positions by 3:45 PM ET
