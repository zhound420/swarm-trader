# EXPERIMENT: tighten_stop_pct
# HYPOTHESIS: Reducing STOP_PCT from 1.5% to 1.0% shrinks per-trade losses (left tail), reducing return variance and boosting Sharpe/Sortino without affecting trade count or win rate. Same 2:1 R:R is preserved; we just risk less per trade.
# CHANGE: STOP_PCT decreased from 0.015 to 0.010

"""
Pure-Python intraday day trading strategy — NO LLM calls.

This is the ONLY file the evolution agent modifies.
All tunable parameters are constants at the top.
The strategy is deterministic and produces identical output for identical input.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import time
from typing import Literal

# ---------------------------------------------------------------------------
# Experiment metadata (updated by the evolution agent each iteration)
# ---------------------------------------------------------------------------
EXPERIMENT_NAME = "tighten_stop_pct"
EXPERIMENT_HYPOTHESIS = "Tighter STOP_PCT=1.0% reduces per-trade loss magnitude, shrinking return variance and improving Sharpe/Sortino; R:R ratio preserved at 2:1"
EXPERIMENT_CHANGE = "STOP_PCT decreased from 0.015 to 0.010"

# ---------------------------------------------------------------------------
# Tunable parameters — agent may change any of these
# ---------------------------------------------------------------------------

# RSI thresholds
RSI_PERIOD = 14
RSI_OVERSOLD = 35           # Buy signal below this
RSI_OVERBOUGHT = 65         # Sell signal above this
RSI_NEUTRAL_LOW = 45        # Weak bull zone lower bound
RSI_NEUTRAL_HIGH = 55       # Weak bear zone upper bound

# VWAP deviation bands (%)
VWAP_NEAR_BAND_PCT = 0.50       # Within 0.5% = "at VWAP", no strong signal
VWAP_EXTENDED_PCT = 1.50        # > 1.5% from VWAP = extended, caution

# Volume ratio thresholds (today cumulative / 20d avg daily)
VOLUME_CONFIRM_RATIO = 1.50     # >= 1.5x to confirm signal
VOLUME_STRONG_RATIO = 2.50      # >= 2.5x = strong conviction

# Risk / sizing
STOP_PCT = 0.010                # Default stop = 1.0% from entry
TARGET_MULTIPLIER = 2.0         # R:R ratio (target = entry ± stop_dist * 2.0)
MAX_POSITION_SIZE_PCT = 0.15    # Max 15% of portfolio per position

# Minimum confidence to emit a signal (0–100)
MIN_CONFIDENCE = 58.0

# Confidence component weights (must sum to 1.0)
CONF_WEIGHT_RSI = 0.30
CONF_WEIGHT_VWAP = 0.30
CONF_WEIGHT_VOLUME = 0.20
CONF_WEIGHT_MACD = 0.20

# MACD parameters
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL_PERIOD = 9

# Regime multipliers — scale confidence based on market conditions
REGIME_MULTIPLIER: dict[str, float] = {
    "trending_up": 1.00,
    "trending_down": 1.00,
    "range_bound": 0.85,
    "volatile": 0.55,
    "unknown": 0.70,
}

# Time-of-day filters (ET)
NO_TRADE_OPEN_MINUTES = 5       # Skip first 5 min (9:30–9:35)
MARKET_CLOSE_CUTOFF = time(15, 45)  # No new entries after 3:45 PM ET


# ---------------------------------------------------------------------------
# Signal dataclass
# ---------------------------------------------------------------------------

@dataclass
class Signal:
    ticker: str
    direction: Literal["long", "short"]
    confidence: float           # 0–100
    entry_price: float
    stop_price: float
    target_price: float
    reasoning: str
    indicators: dict = field(default_factory=dict)  # for debugging / logging


# ---------------------------------------------------------------------------
# Indicator helpers
# ---------------------------------------------------------------------------

def _calc_rsi(closes: list[float], period: int = RSI_PERIOD) -> float | None:
    """Wilder RSI from a list of closing prices."""
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss < 1e-10:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100.0 - (100.0 / (1.0 + rs)), 2)


def _calc_vwap(bars: list[dict]) -> float | None:
    """VWAP from bar dicts with keys h, l, c, v."""
    total_pv = 0.0
    total_v = 0.0
    for b in bars:
        typical = (float(b["h"]) + float(b["l"]) + float(b["c"])) / 3.0
        vol = float(b.get("v", 0))
        total_pv += typical * vol
        total_v += vol
    if total_v < 1:
        return None
    return total_pv / total_v


def _calc_ema(values: list[float], period: int) -> list[float]:
    """Exponential moving average."""
    if not values:
        return []
    k = 2.0 / (period + 1)
    ema = [values[0]]
    for v in values[1:]:
        ema.append(v * k + ema[-1] * (1.0 - k))
    return ema


def _calc_macd(closes: list[float]) -> tuple[float | None, float | None, float | None]:
    """Returns (macd_line, signal_line, histogram). None if insufficient data."""
    min_len = MACD_SLOW + MACD_SIGNAL_PERIOD
    if len(closes) < min_len:
        return None, None, None
    fast_ema = _calc_ema(closes, MACD_FAST)
    slow_ema = _calc_ema(closes, MACD_SLOW)
    n = min(len(fast_ema), len(slow_ema))
    macd_line = [fast_ema[i] - slow_ema[i] for i in range(n)]
    if len(macd_line) < MACD_SIGNAL_PERIOD:
        return None, None, None
    sig_line = _calc_ema(macd_line, MACD_SIGNAL_PERIOD)
    macd_val = macd_line[-1]
    sig_val = sig_line[-1]
    return macd_val, sig_val, macd_val - sig_val


def _parse_bar_time_et(bar_time: str) -> time | None:
    """Parse ISO timestamp and convert UTC → ET (EST = UTC-5, EDT = UTC-4).

    Alpaca bars use UTC timestamps. We assume EDT (UTC-4) for US market hours
    during DST (Mar–Nov). Outside DST it's EST (UTC-5), but since we only
    care about filtering within market hours (9:30–16:00 ET), a 1-hour
    discrepancy at DST boundaries is acceptable for backtesting.
    """
    try:
        t_str = bar_time[11:16]  # 'HH:MM' in UTC
        h, m = int(t_str[:2]), int(t_str[3:5])
        # Convert UTC → EDT (UTC - 4)
        h_et = (h - 4) % 24
        return time(h_et, m)
    except Exception:
        return None


def _is_tradeable_time(bar_time: str) -> bool:
    """Return True if the bar timestamp (UTC) falls within the tradeable ET window."""
    t = _parse_bar_time_et(bar_time)
    if t is None:
        return True  # can't parse → allow
    # Skip first N minutes after open
    open_cutoff = time(9, 30 + NO_TRADE_OPEN_MINUTES)
    if t < open_cutoff:
        return False
    # No new entries at or after 3:45 PM ET
    if t >= MARKET_CLOSE_CUTOFF:
        return False
    return True


# ---------------------------------------------------------------------------
# Per-ticker signal logic
# ---------------------------------------------------------------------------

def _ticker_signal(
    ticker: str,
    bars: list[dict],
    market_context: dict,
    regime_mult: float,
) -> Signal | None:
    """Generate a signal for a single ticker given its bar history."""
    if not bars or len(bars) < 5:
        return None

    closes = [float(b["c"]) for b in bars]
    highs = [float(b["h"]) for b in bars]
    lows = [float(b["l"]) for b in bars]
    volumes = [float(b.get("v", 0)) for b in bars]

    current_price = closes[-1]
    day_open = float(bars[0]["o"])
    day_high = max(highs)
    day_low = min(lows)

    # --- Indicators ---
    rsi = _calc_rsi(closes)
    vwap = _calc_vwap(bars)
    macd_val, macd_sig, macd_hist = _calc_macd(closes)

    avg_volume_20d = market_context.get(f"{ticker}_avg_volume_20d")
    today_cumvol = sum(volumes)
    volume_ratio = (today_cumvol / avg_volume_20d) if avg_volume_20d and avg_volume_20d > 0 else None

    price_vs_vwap_pct: float | None = None
    if vwap and vwap > 0:
        price_vs_vwap_pct = (current_price - vwap) / vwap * 100.0

    # --- Score bull vs bear ---
    bull_score = 0.0
    bear_score = 0.0

    # RSI component
    if rsi is not None:
        if rsi < RSI_OVERSOLD:
            bull_score += CONF_WEIGHT_RSI * 100.0
        elif rsi > RSI_OVERBOUGHT:
            bear_score += CONF_WEIGHT_RSI * 100.0
        elif rsi < RSI_NEUTRAL_LOW:
            bull_score += CONF_WEIGHT_RSI * 60.0
        elif rsi > RSI_NEUTRAL_HIGH:
            bear_score += CONF_WEIGHT_RSI * 60.0
        # neutral zone → no contribution

    # VWAP component
    if price_vs_vwap_pct is not None:
        dev = abs(price_vs_vwap_pct)
        if price_vs_vwap_pct > VWAP_NEAR_BAND_PCT:
            # Price above VWAP — bullish; more above = more conviction (up to extended)
            vwap_score = min(100.0, 60.0 + dev * 10.0)
            bull_score += CONF_WEIGHT_VWAP * vwap_score
        elif price_vs_vwap_pct < -VWAP_NEAR_BAND_PCT:
            vwap_score = min(100.0, 60.0 + dev * 10.0)
            bear_score += CONF_WEIGHT_VWAP * vwap_score
        # Near-VWAP zone → no strong directional signal

    # Volume component — amplifies the leading direction
    if volume_ratio is not None:
        if volume_ratio >= VOLUME_STRONG_RATIO:
            vol_score = 100.0
        elif volume_ratio >= VOLUME_CONFIRM_RATIO:
            vol_score = 70.0
        elif volume_ratio >= 0.80:
            vol_score = 40.0
        else:
            vol_score = 10.0  # low volume = weak conviction
        if bull_score >= bear_score:
            bull_score += CONF_WEIGHT_VOLUME * vol_score
        else:
            bear_score += CONF_WEIGHT_VOLUME * vol_score

    # MACD component
    if macd_hist is not None and macd_val is not None:
        if macd_hist > 0 and macd_val > 0:
            bull_score += CONF_WEIGHT_MACD * 80.0
        elif macd_hist > 0 and macd_val <= 0:
            bull_score += CONF_WEIGHT_MACD * 50.0   # improving but still negative
        elif macd_hist < 0 and macd_val < 0:
            bear_score += CONF_WEIGHT_MACD * 80.0
        elif macd_hist < 0 and macd_val >= 0:
            bear_score += CONF_WEIGHT_MACD * 50.0   # deteriorating

    # Regime alignment bonus
    regime = market_context.get("regime", "unknown")
    if regime == "trending_up" and bull_score > bear_score:
        bull_score *= 1.10
    elif regime == "trending_down" and bear_score > bull_score:
        bear_score *= 1.10
    elif regime == "range_bound":
        # Prefer mean-reversion at extremes in range-bound
        if rsi is not None and rsi < RSI_OVERSOLD:
            bull_score *= 1.15
        elif rsi is not None and rsi > RSI_OVERBOUGHT:
            bear_score *= 1.15

    # SPY alignment — small bonus if trade aligns with broad market direction
    spy_chg = float(market_context.get("spy_change_pct") or 0.0)
    if spy_chg > 0.3 and bull_score > bear_score:
        bull_score *= 1.05
    elif spy_chg < -0.3 and bear_score > bull_score:
        bear_score *= 1.05

    # --- Pick direction ---
    if bull_score > bear_score:
        direction: Literal["long", "short"] = "long"
        raw_confidence = bull_score
    elif bear_score > bull_score:
        direction = "short"
        raw_confidence = bear_score
    else:
        return None  # tie = no signal

    # Apply regime multiplier and cap
    confidence = min(raw_confidence * regime_mult, 95.0)

    if confidence < MIN_CONFIDENCE:
        return None

    # --- Calculate entry, stop, target ---
    entry_price = current_price

    if direction == "long":
        default_stop = round(entry_price * (1.0 - STOP_PCT), 4)
        # Use intraday low as natural stop if it gives a tighter level
        natural_stop = round(day_low * 0.999, 4)
        stop_price = max(default_stop, natural_stop)
        stop_dist = entry_price - stop_price
        if stop_dist <= 0:
            stop_price = default_stop
            stop_dist = entry_price - stop_price
        target_price = round(entry_price + stop_dist * TARGET_MULTIPLIER, 4)
    else:  # short
        default_stop = round(entry_price * (1.0 + STOP_PCT), 4)
        natural_stop = round(day_high * 1.001, 4)
        stop_price = min(default_stop, natural_stop)
        stop_dist = stop_price - entry_price
        if stop_dist <= 0:
            stop_price = default_stop
            stop_dist = stop_price - entry_price
        target_price = round(entry_price - stop_dist * TARGET_MULTIPLIER, 4)

    # Sanity check: target must make directional sense
    if direction == "long" and target_price <= entry_price:
        return None
    if direction == "short" and target_price >= entry_price:
        return None

    # --- Build reasoning string ---
    rsi_s = f"RSI={rsi:.1f}" if rsi is not None else "RSI=n/a"
    vwap_s = f"VWAP_dev={price_vs_vwap_pct:+.2f}%" if price_vs_vwap_pct is not None else "VWAP=n/a"
    vol_s = f"vol={volume_ratio:.1f}x" if volume_ratio is not None else "vol=n/a"
    macd_s = f"MACD_hist={macd_hist:+.4f}" if macd_hist is not None else "MACD=n/a"
    reasoning = (
        f"{direction.upper()} | {rsi_s} {vwap_s} {vol_s} {macd_s} | "
        f"regime={regime} conf={confidence:.0f}"
    )

    return Signal(
        ticker=ticker,
        direction=direction,
        confidence=confidence,
        entry_price=entry_price,
        stop_price=stop_price,
        target_price=target_price,
        reasoning=reasoning,
        indicators={
            "rsi": rsi,
            "vwap": vwap,
            "price_vs_vwap_pct": price_vs_vwap_pct,
            "volume_ratio": volume_ratio,
            "macd_val": macd_val,
            "macd_sig": macd_sig,
            "macd_hist": macd_hist,
            "day_high": day_high,
            "day_low": day_low,
            "day_open": day_open,
            "current_price": current_price,
        },
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_signals(bars_df: dict[str, list[dict]], market_context: dict) -> list[Signal]:
    """
    Generate trading signals for all tickers at the current bar.

    Args:
        bars_df:        Dict of {ticker: [bar_dicts]} where each bar_dict has
                        keys {t, o, h, l, c, v}. Contains bars from market open
                        up to and including the current bar.
        market_context: Dict with keys:
                          regime          — "trending_up" | "trending_down" |
                                            "range_bound" | "volatile" | "unknown"
                          current_bar_time — ISO timestamp of current bar
                          spy_change_pct  — SPY % change from open to now
                          qqq_change_pct  — QQQ % change from open to now
                          {ticker}_prev_close      — previous day's close per ticker
                          {ticker}_avg_volume_20d  — 20-day avg daily volume per ticker

    Returns:
        List of Signal objects. May be empty.
    """
    if not isinstance(bars_df, dict):
        return []

    # Time-of-day gate
    current_bar_time = market_context.get("current_bar_time", "")
    if current_bar_time and not _is_tradeable_time(current_bar_time):
        return []

    regime = market_context.get("regime", "unknown")
    regime_mult = REGIME_MULTIPLIER.get(regime, 0.70)

    signals: list[Signal] = []
    for ticker, bars in bars_df.items():
        try:
            sig = _ticker_signal(ticker, bars, market_context, regime_mult)
            if sig is not None:
                signals.append(sig)
        except Exception:
            continue

    return signals
