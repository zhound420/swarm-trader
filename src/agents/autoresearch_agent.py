"""AutoResearch Agent — deterministic signals from the evolved strategy.

This agent wraps autoresearch/strategy.py as a first-class analyst in the
multi-agent committee.  Unlike every other analyst, it makes ZERO LLM calls.
Pure Python, deterministic, instant.

The evolved strategy is imported at runtime from autoresearch/strategy.py,
so any changes from the nightly evolution loop are picked up automatically —
no deployment step needed.
"""

import json
import sys
from pathlib import Path

from langchain_core.messages import HumanMessage

from src.graph.state import AgentState, show_agent_reasoning
from src.utils.progress import progress

# ---------------------------------------------------------------------------
# Import the evolved strategy
# ---------------------------------------------------------------------------

AUTORESEARCH_DIR = Path(__file__).resolve().parents[2] / "autoresearch"

# Add to path so we can import strategy module
if str(AUTORESEARCH_DIR) not in sys.path:
    sys.path.insert(0, str(AUTORESEARCH_DIR))

# Lazy import — strategy.py may not exist in all environments
_strategy_module = None


def _get_strategy():
    """Lazy-load strategy module (avoids import errors at graph build time)."""
    global _strategy_module
    if _strategy_module is None:
        try:
            import strategy as _strat
            _strategy_module = _strat
        except ImportError as e:
            raise ImportError(
                f"Cannot import autoresearch/strategy.py: {e}. "
                f"Looked in: {AUTORESEARCH_DIR}"
            )
    return _strategy_module


# ---------------------------------------------------------------------------
# Map live ticker_data to strategy.py's expected bar format
# ---------------------------------------------------------------------------

def _build_bars_df(tickers: list[str], ticker_data_map: dict) -> dict[str, list[dict]]:
    """
    Convert live ticker_data_map to the {ticker: [bar_dicts]} format
    that strategy.generate_signals() expects.

    Each bar dict has keys: t, o, h, l, c, v
    """
    bars_df = {}
    for ticker in tickers:
        td = ticker_data_map.get(ticker, {})
        intraday = td.get("intraday", {})
        bars = intraday.get("bars_5min", [])

        if not bars:
            continue

        # Normalize bar keys — Alpaca bars come as {t, o, h, l, c, v}
        # strategy.py expects the same format, so pass through.
        # But some bars might use different key names, so normalize:
        normalized = []
        for b in bars:
            normalized.append({
                "t": b.get("t", ""),
                "o": float(b.get("o", 0)),
                "h": float(b.get("h", 0)),
                "l": float(b.get("l", 0)),
                "c": float(b.get("c", 0)),
                "v": float(b.get("v", 0)),
            })

        if normalized:
            bars_df[ticker] = normalized

    return bars_df


def _build_market_context(
    market_regime: dict,
    bars_df: dict | None = None,
    ticker_data_map: dict | None = None,
) -> dict:
    """
    Build market_context dict for strategy.generate_signals().

    strategy.py reads:
      - regime: str (trending_up, trending_down, range_bound, volatile, unknown)
      - mode: str (day or swing)
      - current_bar_time: str (HH:MM in ET for time-of-day filters)
      - {ticker}_avg_volume_20d: float (per-ticker 20d avg volume for volume ratio calc)
      - spy_change_pct: float (SPY daily change % for alignment bonus)
      - qqq_change_pct: float (QQQ daily change % for alignment bonus)
    """
    from datetime import datetime, timezone, timedelta

    # Strategy expects Eastern Time for time-of-day filters
    # Use the timestamp of the last bar if available, otherwise compute ET now
    current_bar_time = ""
    if bars_df:
        # Get last bar timestamp from any ticker
        for bars in bars_df.values():
            if bars:
                last_t = bars[-1].get("t", "")
                if last_t:
                    # Alpaca timestamps are ISO format, extract time
                    try:
                        from datetime import datetime as dt
                        parsed = dt.fromisoformat(last_t.replace("Z", "+00:00"))
                        # Convert to ET (UTC-4 during EDT, UTC-5 during EST)
                        # Approximate: use -4 during March-Nov
                        et = parsed - timedelta(hours=4)
                        current_bar_time = et.strftime("%H:%M")
                    except Exception:
                        pass
                break

    if not current_bar_time:
        # Fallback: compute ET from current UTC time
        now_utc = datetime.now(timezone.utc)
        et = now_utc - timedelta(hours=4)  # EDT approximation
        current_bar_time = et.strftime("%H:%M")

    ctx = {
        "regime": market_regime.get("regime", "unknown"),
        "mode": "day",  # autoresearch currently only evolves day strategies
        "current_bar_time": current_bar_time,
        "spy_change_pct": market_regime.get("spy_change_pct", 0.0),
        "qqq_change_pct": market_regime.get("qqq_change_pct", 0.0),
    }

    # Inject per-ticker avg_volume_20d so strategy.py can compute volume ratios
    if ticker_data_map:
        for ticker, td in ticker_data_map.items():
            intraday = td.get("intraday", {})
            avg_vol = intraday.get("avg_volume_20d")
            if avg_vol:
                ctx[f"{ticker}_avg_volume_20d"] = float(avg_vol)

    return ctx


# ---------------------------------------------------------------------------
# Signal → analyst output conversion
# ---------------------------------------------------------------------------

def _signal_to_analyst_output(signal) -> dict:
    """Convert a strategy.Signal to the standard analyst output format."""
    # Map strategy directions to analyst signal format
    direction_map = {"long": "bullish", "short": "bearish"}

    return {
        "signal": direction_map.get(signal.direction, "neutral"),
        "confidence": signal.confidence,
        "entry_type": "market",
        "stop_price": signal.stop_price,
        "target_price": signal.target_price,
        "reasoning": f"[AutoResearch] {signal.reasoning}",
    }


# ---------------------------------------------------------------------------
# Agent entry point
# ---------------------------------------------------------------------------

def autoresearch_agent(state: AgentState, agent_id: str = "autoresearch_agent"):
    """
    Run the evolved strategy against live intraday data.

    Produces deterministic signals with zero LLM calls.
    Fitness of the current strategy is included in output for PM awareness.
    """

    data = state["data"]
    tickers = data["tickers"]
    market_regime = data.get("market_regime", {})
    ticker_data_map = data.get("ticker_data", {})

    strategy = _get_strategy()

    # Build inputs in strategy.py's expected format
    bars_df = _build_bars_df(tickers, ticker_data_map)
    market_context = _build_market_context(market_regime, bars_df, ticker_data_map)

    # Run the evolved strategy
    try:
        signals = strategy.generate_signals(bars_df, market_context)
    except Exception as e:
        # Strategy crashed — emit neutral for everything, don't break the pipeline
        progress.update_status(agent_id, None, f"Strategy error: {e}")
        signals = []

    # Build per-ticker analysis
    # Index signals by ticker for easy lookup
    signals_by_ticker = {}
    for sig in signals:
        # If multiple signals for same ticker, keep highest confidence
        if sig.ticker not in signals_by_ticker or sig.confidence > signals_by_ticker[sig.ticker].confidence:
            signals_by_ticker[sig.ticker] = sig

    # Get strategy metadata for PM context
    strategy_fitness = getattr(strategy, "EXPERIMENT_NAME", "unknown")
    strategy_hypothesis = getattr(strategy, "EXPERIMENT_HYPOTHESIS", "")

    analysis = {}
    for ticker in tickers:
        progress.update_status(agent_id, ticker, "Running evolved strategy")

        if ticker in signals_by_ticker:
            analysis[ticker] = _signal_to_analyst_output(signals_by_ticker[ticker])
        else:
            analysis[ticker] = {
                "signal": "neutral",
                "confidence": 0,
                "entry_type": "wait",
                "stop_price": None,
                "target_price": None,
                "reasoning": "[AutoResearch] No signal — below confidence threshold or insufficient data",
            }

        progress.update_status(agent_id, ticker, "Done")

    # Log strategy metadata
    message_content = {
        "strategy_experiment": strategy_fitness,
        "signals": analysis,
    }

    message = HumanMessage(content=json.dumps(analysis), name=agent_id)

    if state["metadata"].get("show_reasoning"):
        show_agent_reasoning(analysis, agent_id)

    state["data"]["analyst_signals"][agent_id] = analysis
    progress.update_status(agent_id, None, "Done")

    return {"messages": [message], "data": state["data"]}
