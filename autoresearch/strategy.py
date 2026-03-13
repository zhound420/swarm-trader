# EXPERIMENT: rsi_weight_boost
# HYPOTHESIS: MACD is a trend-following indicator that often contradicts RSI extremes — when RSI is oversold (<30), MACD is typically still bearish, adding 12 points to bear_score and partially cancelling the RSI bull signal. Since our strategy is RSI mean-reversion, the RSI component should dominate. Shifting 5% weight from MACD to RSI makes extremes score higher, boosting confidence on quality signals and improving win rate / Sharpe.
# CHANGE: CONF_WEIGHT_RSI from 0.35 to 0.40, CONF_WEIGHT_MACD from 0.15 to 0.10 (sum unchanged at 1.0)

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
EXPERIMENT_NAME = "rsi_weight_boost"
EXPERIMENT_HYPOTHESIS = "MACD is a trend-following indicator that often contradicts RSI extremes — when RSI is oversold (<30), MACD is typically still bearish, adding 12 points to bear_score and partially cancelling the RSI bull signal. Since our strategy is RSI mean-reversion, the RSI component should dominate. Shifting 5% weight from MACD to RSI makes extremes score higher, boosting confidence on quality signals and improving win rate / Sharpe."
EXPERIMENT_CHANGE = "CONF_WEIGHT_RSI from 0.35 to 0.40, CONF_WEIGHT_MACD from 0.15 to 0.10 (sum unchanged at 1.0)"

# ---------------------------------------------------------------------------
# Tunable parameters — agent may change any of these
# ---------------------------------------------------------------------------

# RSI thresholds
RSI_PERIOD = 14
RSI_OVERSOLD = 30           # Buy signal below this
RSI_OVERBOUGHT = 70         # Sell signal above this
RSI_NEUTRAL_LOW = 45        # Weak bull zone lower bound
RSI_NEUTRAL_HIGH = 55       # Weak bear zone upper bound

# VWAP deviation bands (%)
VWAP_NEAR_BAND_PCT = 0.50       # Within 0.50% = "at VWAP", no strong signal
VWAP_EXTENDED_PCT = 1.50        # > 1.5% from VWAP = extended, caution

# Volume ratio thresholds (today cumulative / 20d avg daily)
VOLUME_CONFIRM_RATIO = 1.50     # >= 1.5x to confirm signal
VOLUME_STRONG_RATIO = 2.50      # >= 2.5x = strong conviction

# Risk / sizing
STOP_PCT = 0.008                # Default stop = 0.8% from entry
TARGET_MULTIPLIER = 2.2         # R:R ratio (target = entry ± stop_dist * 2.2)
MAX_POSITION_SIZE_PCT = 0.15    # Max 15% of portfolio per position

# Minimum confidence to emit a signal (0–100)
MIN_CONFIDENCE = 58.0

# Confidence component weights (must sum to 1.0)
CONF_WEIGHT_RSI = 0.40
CONF_WEIGHT_VWAP = 0.30
CONF_WEIGHT_VOLUME = 0.20
CONF_WEIGHT_MACD = 0.10

# MACD parameters
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL_PERIOD = 9

# Regime multipliers — scale confidence based on market conditions
REGIME_MULTIPLIER: dict[str, float] = {
    "trending_up": 1.00,
    "trending_down": 1.00,
    "range_bound": 1.00,
    "volatile": 0.55,
    "unknown": 0.70,
}

# Time-of-day filters (ET)
NO_TRADE_OPEN_MINUTES = 5       # Skip first 5 min (9:30–9:35)
MARKET_CLOSE_CUTOFF = time(15, 45)  # No new entries after 3:45 PM ET

# ---------------------------------------------------------------------------
# Mode: "day" or "swing" — controls which signal logic path runs
# ---------------------------------------------------------------------------
MODE = "day"

# ---------------------------------------------------------------------------
# Swing trading parameters (used when MODE = "swing")
# ---------------------------------------------------------------------------
SMA_FAST = 20               # Fast simple moving average period
SMA_SLOW = 50               # Slow simple moving average period
SWING_RSI_PERIOD = 14
SWING_RSI_OVERSOLD = 35     # Buy on pullbacks below this
SWING_RSI_OVERBOUGHT = 70   # Sell/short above this
SWING_STOP_PCT = 0.03       # 3% stop for swing (wider than day)
SWING_TARGET_MULTIPLIER = 2.5  # 2.5:1 R:R for swing
SWING_MIN_CONFIDENCE = 50.0
SWING_TREND_STRENGTH_MIN = 0.5  # Min SMA spread (%) to confirm trend
SWING_VOLUME_CONFIRM = 1.2  # Volume ratio to confirm breakout

# Swing confidence weights
SWING_CONF_WEIGHT_TREND = 0.35    # SMA crossover / alignment
SWING_CONF_WEIGHT_RSI = 0.25      # RSI mean reversion
SWING_CONF_WEIGHT_MOMENTUM = 0.25 # Price momentum (rate of change)
SWING_CONF_WEIGHT_VOLUME = 0.15   # Volume confirmation


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

    # QQQ alignment — independent tech-market confirmation bonus
    qqq_chg = float(market_context.get("qqq_change_pct") or 0.0)
    if qqq_chg > 0.3 and bull_score > bear_score:
        bull_score *= 1.05
    elif qqq_chg < -0.3 and bear_score > bull_score:
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
# Swing signal helpers
# ---------------------------------------------------------------------------

def _calc_sma(closes: list[float], period: int) -> float | None:
    """Simple moving average of the last `period` closes."""
    if len(closes) < period:
        return None
    return sum(closes[-period:]) / period


def _calc_roc(closes: list[float], period: int = 10) -> float | None:
    """Rate of change (%) over `period` bars."""
    if len(closes) < period + 1:
        return None
    return (closes[-1] - closes[-period - 1]) / closes[-period - 1] * 100.0


def _swing_ticker_signal(
    ticker: str,
    bars: list[dict],
    market_context: dict,
) -> Signal | None:
    """Generate a swing trading signal for a single ticker using daily bars."""
    if not bars or len(bars) < SMA_SLOW + 5:
        return None

    closes = [float(b["c"]) for b in bars]
    highs = [float(b["h"]) for b in bars]
    lows = [float(b["l"]) for b in bars]
    volumes = [float(b.get("v", 0)) for b in bars]

    current_price = closes[-1]

    # --- Indicators ---
    sma_fast = _calc_sma(closes, SMA_FAST)
    sma_slow = _calc_sma(closes, SMA_SLOW)
    rsi = _calc_rsi(closes, SWING_RSI_PERIOD)
    roc = _calc_roc(closes, 10)

    # Volume ratio: last 5 days avg vs 20-day avg
    avg_vol_5 = sum(volumes[-5:]) / 5 if len(volumes) >= 5 else None
    avg_vol_20 = sum(volumes[-20:]) / 20 if len(volumes) >= 20 else None
    volume_ratio = avg_vol_5 / avg_vol_20 if avg_vol_5 and avg_vol_20 and avg_vol_20 > 0 else None

    if sma_fast is None or sma_slow is None:
        return None

    # --- Trend scoring ---
    sma_spread_pct = (sma_fast - sma_slow) / sma_slow * 100.0
    price_vs_sma_fast_pct = (current_price - sma_fast) / sma_fast * 100.0

    bull_score = 0.0
    bear_score = 0.0

    # Trend component (SMA alignment)
    if sma_spread_pct > SWING_TREND_STRENGTH_MIN:
        # Uptrend: SMA20 > SMA50 by meaningful amount
        trend_score = min(100.0, 50.0 + abs(sma_spread_pct) * 15.0)
        bull_score += SWING_CONF_WEIGHT_TREND * trend_score
    elif sma_spread_pct < -SWING_TREND_STRENGTH_MIN:
        # Downtrend
        trend_score = min(100.0, 50.0 + abs(sma_spread_pct) * 15.0)
        bear_score += SWING_CONF_WEIGHT_TREND * trend_score

    # RSI component — mean reversion within trend
    if rsi is not None:
        if rsi < SWING_RSI_OVERSOLD:
            # Oversold — strong buy signal
            bull_score += SWING_CONF_WEIGHT_RSI * 90.0
        elif rsi > SWING_RSI_OVERBOUGHT:
            # Overbought — strong sell/short signal
            bear_score += SWING_CONF_WEIGHT_RSI * 90.0
        elif rsi < 45:
            bull_score += SWING_CONF_WEIGHT_RSI * 50.0
        elif rsi > 55:
            bear_score += SWING_CONF_WEIGHT_RSI * 50.0

    # Momentum component (ROC)
    if roc is not None:
        if roc > 2.0:
            bull_score += SWING_CONF_WEIGHT_MOMENTUM * min(100.0, 40.0 + roc * 8.0)
        elif roc < -2.0:
            bear_score += SWING_CONF_WEIGHT_MOMENTUM * min(100.0, 40.0 + abs(roc) * 8.0)

    # Volume component — confirms direction
    if volume_ratio is not None:
        if volume_ratio >= SWING_VOLUME_CONFIRM:
            vol_score = min(100.0, 50.0 + (volume_ratio - 1.0) * 40.0)
        else:
            vol_score = 30.0
        if bull_score >= bear_score:
            bull_score += SWING_CONF_WEIGHT_VOLUME * vol_score
        else:
            bear_score += SWING_CONF_WEIGHT_VOLUME * vol_score

    # Pullback bonus: price near SMA in uptrend = good entry
    if sma_spread_pct > SWING_TREND_STRENGTH_MIN and -1.5 < price_vs_sma_fast_pct < 0.5:
        bull_score *= 1.15  # Buying the dip in an uptrend
    elif sma_spread_pct < -SWING_TREND_STRENGTH_MIN and -0.5 < price_vs_sma_fast_pct < 1.5:
        bear_score *= 1.15  # Shorting the rip in a downtrend

    # --- Pick direction ---
    if bull_score > bear_score:
        direction: Literal["long", "short"] = "long"
        raw_confidence = bull_score
    elif bear_score > bull_score:
        direction = "short"
        raw_confidence = bear_score
    else:
        return None

    confidence = min(raw_confidence, 95.0)
    if confidence < SWING_MIN_CONFIDENCE:
        return None

    # --- Stop and target ---
    recent_low = min(lows[-10:])
    recent_high = max(highs[-10:])

    if direction == "long":
        default_stop = round(current_price * (1.0 - SWING_STOP_PCT), 4)
        natural_stop = round(recent_low * 0.995, 4)
        stop_price = max(default_stop, natural_stop)
        stop_dist = current_price - stop_price
        if stop_dist <= 0:
            stop_price = default_stop
            stop_dist = current_price - stop_price
        target_price = round(current_price + stop_dist * SWING_TARGET_MULTIPLIER, 4)
    else:
        default_stop = round(current_price * (1.0 + SWING_STOP_PCT), 4)
        natural_stop = round(recent_high * 1.005, 4)
        stop_price = min(default_stop, natural_stop)
        stop_dist = stop_price - current_price
        if stop_dist <= 0:
            stop_price = default_stop
            stop_dist = stop_price - current_price
        target_price = round(current_price - stop_dist * SWING_TARGET_MULTIPLIER, 4)

    if direction == "long" and target_price <= current_price:
        return None
    if direction == "short" and target_price >= current_price:
        return None

    # Reasoning
    sma_s = f"SMA{SMA_FAST}={sma_fast:.2f}/SMA{SMA_SLOW}={sma_slow:.2f}"
    rsi_s = f"RSI={rsi:.1f}" if rsi is not None else "RSI=n/a"
    roc_s = f"ROC={roc:+.1f}%" if roc is not None else "ROC=n/a"
    vol_s = f"vol={volume_ratio:.1f}x" if volume_ratio is not None else "vol=n/a"
    reasoning = (
        f"SWING {direction.upper()} | {sma_s} spread={sma_spread_pct:+.1f}% | "
        f"{rsi_s} {roc_s} {vol_s} | conf={confidence:.0f}"
    )

    return Signal(
        ticker=ticker,
        direction=direction,
        confidence=confidence,
        entry_price=current_price,
        stop_price=stop_price,
        target_price=target_price,
        reasoning=reasoning,
        indicators={
            "sma_fast": sma_fast,
            "sma_slow": sma_slow,
            "sma_spread_pct": sma_spread_pct,
            "rsi": rsi,
            "roc_10": roc,
            "volume_ratio": volume_ratio,
            "price_vs_sma_fast_pct": price_vs_sma_fast_pct,
            "recent_high": recent_high,
            "recent_low": recent_low,
            "current_price": current_price,
        },
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_signals(bars_df: dict[str, list[dict]], market_context: dict) -> list[Signal]:
    """
    Generate trading signals for all tickers.

    Routes to day trading or swing logic based on MODE constant or
    market_context["mode"] override.

    Args:
        bars_df:        Dict of {ticker: [bar_dicts]} with keys {t, o, h, l, c, v}.
        market_context: Dict with regime info, timestamps, and optional "mode" key.

    Returns:
        List of Signal objects. May be empty.
    """
    if not isinstance(bars_df, dict):
        return []

    mode = market_context.get("mode", MODE)

    if mode == "swing":
        return _generate_swing_signals(bars_df, market_context)
    else:
        return _generate_day_signals(bars_df, market_context)


def _generate_swing_signals(bars_df: dict[str, list[dict]], market_context: dict) -> list[Signal]:
    """Swing mode: daily bars, trend following, multi-day holds."""
    signals: list[Signal] = []
    for ticker, bars in bars_df.items():
        try:
            sig = _swing_ticker_signal(ticker, bars, market_context)
            if sig is not None:
                signals.append(sig)
        except Exception:
            continue
    return signals


def _generate_day_signals(bars_df: dict[str, list[dict]], market_context: dict) -> list[Signal]:
    """Day mode: 5-min bars, intraday technicals, flatten at close."""
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
