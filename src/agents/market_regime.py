"""Market Regime Agent — Classifies the current intraday market regime.

Regime types:
  trending_up    SPY making higher highs on volume, price > VWAP, RSI 50-70
                 → Buy dips to VWAP, ride momentum names long
  trending_down  SPY making lower lows, price < VWAP, RSI 30-50
                 → Sell rips, short weakness, avoid longs
  range_bound    SPY oscillating, no clear direction, RSI 40-60
                 → Fade extremes, take quick profits, tight stops
  volatile       Large % swings, VIX elevated, conviction-less tape
                 → Reduce size dramatically or sit out entirely

This agent runs once (analyzes SPY), then broadcasts the regime to all tickers
so Apex and other intraday agents can adapt their strategy accordingly.

The regime is stored in state["data"]["market_regime"] for inter-agent access.
"""

import json

from langchain_core.messages import HumanMessage
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel
from typing_extensions import Literal

from src.graph.state import AgentState, show_agent_reasoning
from src.utils.llm import call_llm
from src.utils.progress import progress


# ---------------------------------------------------------------------------
# Pydantic output model
# ---------------------------------------------------------------------------

class RegimeSignal(BaseModel):
    regime: Literal["trending_up", "trending_down", "range_bound", "volatile"]
    spy_direction: Literal["up", "down", "flat"]
    confidence: float
    vix_level: str | None = None  # "low" | "elevated" | "high" | "extreme" | null
    strategy_bias: str            # one-line strategy implication (max 100 chars)
    reasoning: str                # max 150 chars


# ---------------------------------------------------------------------------
# Main agent
# ---------------------------------------------------------------------------

def market_regime_agent(state: AgentState, agent_id: str = "market_regime_agent"):
    """Classify the current intraday market regime using SPY and QQQ intraday data."""

    data = state["data"]
    tickers = data["tickers"]
    ticker_data_map = data.get("ticker_data", {})

    # Extract SPY intraday context — this is the primary regime signal
    spy_data = ticker_data_map.get("SPY", {})
    qqq_data = ticker_data_map.get("QQQ", {})

    spy_context = {
        "spy_current_price": (spy_data.get("prices") or {}).get("current"),
        "spy_change_1d_pct": (spy_data.get("prices") or {}).get("change_1d"),
        "spy_change_5d_pct": (spy_data.get("prices") or {}).get("change_5d"),
        "spy_vwap": (spy_data.get("intraday") or {}).get("vwap"),
        "spy_rsi_14": (spy_data.get("intraday") or {}).get("rsi_14"),
        "spy_price_vs_vwap_pct": (spy_data.get("intraday") or {}).get("price_vs_vwap_pct"),
        "spy_todays_high": (spy_data.get("intraday") or {}).get("high"),
        "spy_todays_low": (spy_data.get("intraday") or {}).get("low"),
        "spy_volume_ratio": (spy_data.get("intraday") or {}).get("volume_ratio"),
        "spy_macd_signal": (spy_data.get("intraday") or {}).get("macd_signal"),
        "spy_bars_5min_last_30m": (spy_data.get("intraday") or {}).get("bars_5min", [])[-6:],
        "qqq_change_1d_pct": (qqq_data.get("prices") or {}).get("change_1d"),
        "qqq_price_vs_vwap_pct": (qqq_data.get("intraday") or {}).get("price_vs_vwap_pct"),
        "qqq_rsi_14": (qqq_data.get("intraday") or {}).get("rsi_14"),
    }

    progress.update_status(agent_id, "SPY", "Classifying market regime")
    regime = _classify_regime(spy_context=spy_context, state=state, agent_id=agent_id)

    # Store regime globally so Apex can read it during the same run
    state["data"]["market_regime"] = {
        "regime": regime.regime,
        "spy_direction": regime.spy_direction,
        "confidence": regime.confidence,
        "vix_level": regime.vix_level,
        "strategy_bias": regime.strategy_bias,
        "reasoning": regime.reasoning,
    }

    # Emit per-ticker signals — same regime applies to all tickers (market-wide)
    # Maps regime → bullish/bearish/neutral so the portfolio manager can weight it
    regime_analysis: dict = {}
    for ticker in tickers:
        if regime.regime == "trending_up":
            signal = "bullish"
        elif regime.regime == "trending_down":
            signal = "bearish"
        else:
            # range_bound or volatile: be selective, treat as neutral
            signal = "neutral"

        regime_analysis[ticker] = {
            "signal": signal,
            "confidence": regime.confidence,
            "reasoning": f"[{regime.regime}] {regime.strategy_bias}",
        }
        progress.update_status(agent_id, ticker, "Done", analysis=regime.strategy_bias)

    message = HumanMessage(content=json.dumps(regime_analysis), name=agent_id)

    if state["metadata"].get("show_reasoning"):
        show_agent_reasoning(
            {"regime": state["data"]["market_regime"], "per_ticker": regime_analysis},
            agent_id,
        )

    state["data"]["analyst_signals"][agent_id] = regime_analysis
    progress.update_status(agent_id, None, "Done")

    return {"messages": [message], "data": state["data"]}


# ---------------------------------------------------------------------------
# LLM regime classification
# ---------------------------------------------------------------------------

def _classify_regime(
    spy_context: dict,
    state: AgentState,
    agent_id: str = "market_regime_agent",
) -> RegimeSignal:
    """Use LLM to classify the current intraday market regime from SPY data."""

    template = ChatPromptTemplate.from_messages([
        (
            "system",
            """You are a market regime classifier for intraday day trading. One job: read the tape and tell traders WHAT THE MARKET IS DOING and HOW TO TRADE IT.

REGIME DEFINITIONS:
- trending_up:   SPY making higher highs on volume, price > VWAP, RSI 50-70, QQQ confirming. Bias: buy dips to VWAP, hold winners.
- trending_down: SPY making lower lows, price < VWAP, RSI 30-50. Bias: sell rips, short strength.
- range_bound:   SPY oscillating between levels, RSI 40-60, no directional conviction. Bias: fade extremes, quick profits, tight stops.
- volatile:      Intraday swings >1%, volume spike, VIX elevated, conviction-less. Bias: cut size by 50%+ or sit out.

SPY DIRECTION (use daily % change vs VWAP as primary signal):
- up:   SPY > +0.3% AND price above VWAP
- down: SPY < -0.3% AND price below VWAP
- flat: Everything else

Be decisive. A day trader needs a clear answer in the next 30 seconds, not a balanced essay.
Max reasoning: 150 chars. Max strategy_bias: 100 chars."""
        ),
        (
            "human",
            """Classify today's intraday market regime using this SPY/QQQ data:

{spy_context}

Work through this quickly:
1. Is SPY above or below VWAP? (Key intraday bias indicator)
2. What is the trend direction based on 5-min bars and daily % change?
3. What does volume ratio tell you? >1.2 = unusual activity (vol or trending), <0.8 = dead tape
4. What is RSI signaling? >55 = momentum up, <45 = momentum down, 45-55 = chopping
5. Are SPY and QQQ confirming each other? Divergence = caution.
6. What should a day trader do: buy dips, sell rips, fade extremes, or sit out?

Return JSON only:
{{
  "regime": "trending_up|trending_down|range_bound|volatile",
  "spy_direction": "up|down|flat",
  "confidence": <float 0-100>,
  "vix_level": "low|elevated|high|extreme|null",
  "strategy_bias": "<one-line trading implication, max 100 chars>",
  "reasoning": "<max 150 chars>"
}}"""
        ),
    ])

    prompt = template.invoke({
        "spy_context": json.dumps(spy_context, indent=2),
    })

    def default_regime():
        return RegimeSignal(
            regime="range_bound",
            spy_direction="flat",
            confidence=35.0,
            vix_level=None,
            strategy_bias="No intraday data — assume range-bound, reduce size",
            reasoning="Insufficient SPY intraday data for regime classification",
        )

    return call_llm(
        prompt=prompt,
        pydantic_model=RegimeSignal,
        agent_name=agent_id,
        state=state,
        default_factory=default_regime,
    )
