"""Apex Agent — Intraday day trading analyst.

Trading Philosophy:
- Day trading on liquid mega-cap tech and momentum names
- Technical-first: price action, VWAP, volume, key levels drive every decision
- Market context is everything: SPY/QQQ direction determines the bias
- Trade in the direction of the regime — don't fight the tape
- ALWAYS define a stop before entering. No stop = no trade.
- Entry quality over frequency: wait for pullbacks, don't chase extended moves
- Cut losers at stop price immediately. No hope trades, no averaging down.
- Let winners run to target. Don't exit early because it "feels" extended.
"""

import json

from langchain_core.messages import HumanMessage
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel
from typing_extensions import Literal

from src.config import DAY_TRADE_UNIVERSE, DEFAULT_STOP_PCT, DEFAULT_TARGET_MULTIPLIER
from src.graph.state import AgentState, show_agent_reasoning
from src.utils.llm import call_llm
from src.utils.progress import progress


# ---------------------------------------------------------------------------
# Pydantic output model
# ---------------------------------------------------------------------------

class ApexSignal(BaseModel):
    signal: Literal["bullish", "bearish", "neutral"]
    confidence: float
    entry_type: Literal["market", "limit", "wait"] = "market"
    stop_price: float | None = None
    target_price: float | None = None
    reasoning: str


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _get_ticker_category(ticker: str) -> tuple[str | None, dict | None]:
    """Return (category_key, category_dict) for a ticker in the day trade universe."""
    for cat_key, cat_data in DAY_TRADE_UNIVERSE.items():
        if ticker in cat_data["tickers"]:
            return cat_key, cat_data
    return None, None


def _extract_intraday_context(ticker_data: dict, ticker: str) -> dict:
    """Pull intraday technical data from the pre-gathered data payload."""
    intraday = ticker_data.get("intraday", {})
    prices = ticker_data.get("prices", {})
    return {
        "ticker": ticker,
        "current_price": prices.get("current") if prices else None,
        "change_1d_pct": prices.get("change_1d") if prices else None,
        "vwap": intraday.get("vwap"),
        "rsi_14": intraday.get("rsi_14"),
        "price_vs_vwap_pct": intraday.get("price_vs_vwap_pct"),
        "todays_high": intraday.get("high"),
        "todays_low": intraday.get("low"),
        "todays_open": intraday.get("open"),
        "prev_close": intraday.get("prev_close"),
        "premarket_high": intraday.get("premarket_high"),
        "premarket_low": intraday.get("premarket_low"),
        "volume_today": intraday.get("volume"),
        "avg_volume_20d": intraday.get("avg_volume_20d"),
        "volume_ratio": intraday.get("volume_ratio"),  # today / 20d avg
        "macd_signal": intraday.get("macd_signal"),
        "bars_5min_last_30m": intraday.get("bars_5min", [])[-6:],  # last 30 min
    }


# ---------------------------------------------------------------------------
# Main agent
# ---------------------------------------------------------------------------

def apex_agent(state: AgentState, agent_id: str = "apex_agent"):
    """Apex — intraday day trading analyst. Technical-first, risk-defined entries."""

    data = state["data"]
    tickers = data["tickers"]
    market_regime = data.get("market_regime", {})
    ticker_data_map = data.get("ticker_data", {})

    apex_analysis: dict = {}

    for ticker in tickers:
        progress.update_status(agent_id, ticker, "Reading intraday technicals")

        cat_key, cat_data = _get_ticker_category(ticker)
        ticker_data = ticker_data_map.get(ticker, {})
        intraday_ctx = _extract_intraday_context(ticker_data, ticker)

        analysis_context = {
            **intraday_ctx,
            "in_day_trade_universe": cat_key is not None,
            "category": cat_data["label"] if cat_data else "Not in day trade universe",
            "market_regime": market_regime.get("regime", "unknown"),
            "spy_direction": market_regime.get("spy_direction", "unknown"),
            "regime_strategy_bias": market_regime.get("strategy_bias", "no regime data"),
            "news_headlines": [
                n.get("title") for n in (ticker_data.get("news") or [])[:3]
                if isinstance(n, dict)
            ],
        }

        progress.update_status(agent_id, ticker, "Generating Apex signal")
        output = _generate_apex_signal(
            ticker=ticker,
            analysis_context=analysis_context,
            state=state,
            agent_id=agent_id,
        )

        apex_analysis[ticker] = {
            "signal": output.signal,
            "confidence": output.confidence,
            "entry_type": output.entry_type,
            "stop_price": output.stop_price,
            "target_price": output.target_price,
            "reasoning": output.reasoning,
        }
        progress.update_status(agent_id, ticker, "Done", analysis=output.reasoning)

    message = HumanMessage(content=json.dumps(apex_analysis), name=agent_id)

    if state["metadata"].get("show_reasoning"):
        show_agent_reasoning(apex_analysis, agent_id)

    state["data"]["analyst_signals"][agent_id] = apex_analysis
    progress.update_status(agent_id, None, "Done")

    return {"messages": [message], "data": state["data"]}


# ---------------------------------------------------------------------------
# LLM signal generation
# ---------------------------------------------------------------------------

def _generate_apex_signal(
    ticker: str,
    analysis_context: dict,
    state: AgentState,
    agent_id: str = "apex_agent",
) -> ApexSignal:
    """Generate Apex's intraday day trading signal via LLM."""

    template = ChatPromptTemplate.from_messages([
        (
            "system",
            f"""You are Apex, an intraday day trader. You trade liquid mega-cap tech and momentum names on a 1-2 hour timeframe.

TRADING PHILOSOPHY:
- Technical-first: price action, VWAP, volume, and key levels drive every decision
- Market context is everything: SPY/QQQ direction tells you which way to lean
- Trade WITH the regime: trending_up = buy dips to VWAP; trending_down = sell rips; range_bound = fade extremes; volatile = sit out or reduce size
- ALWAYS define stop and target. No stop = no trade, period.
- Entry quality over quantity: wait for the right setup, don't force trades
- Cut losses instantly at stop. No hope. No averaging down.

DEFAULT RISK PARAMS (use when no specific level is clearer):
- Stop: {DEFAULT_STOP_PCT * 100:.0f}% from entry
- Target: {DEFAULT_TARGET_MULTIPLIER}x the stop distance ({DEFAULT_TARGET_MULTIPLIER:.0f}:1 R:R minimum)

SIGNAL RULES:
- bullish + market: Price above VWAP, volume confirming, pullback to support, SPY up. Enter now.
- bullish + limit: Strong setup but price extended above VWAP. Place limit at VWAP retest or support.
- bullish + wait: Setup exists but needs a trigger (breakout confirmation, volume surge). Monitor.
- bearish + market/limit/wait: Same logic, opposite direction.
- neutral: No clear setup, conflicting signals, or regime says sit out. Do not trade.

FOR bullish/bearish: ALWAYS provide specific stop_price and target_price based on intraday key levels (VWAP, day high/low, prev close, premarket levels).

Reasoning format: "setup | entry | stop | target" — max 200 chars."""
        ),
        (
            "human",
            """Analyze for a day trade entry on {ticker}.

Intraday Technical Context:
{analysis_context}

Work through this:
1. What is the market regime and does it support trading {ticker} right now?
2. Where is price vs VWAP? Above = bull bias. Below = bear bias.
3. What does volume tell you? High ratio = conviction. Low = chop.
4. Identify the key intraday levels: today's high/low, VWAP, prev close, premarket levels.
5. Is there a valid setup? Is entry NOW (market), at a limit level, or should we wait for confirmation?
6. Where exactly is the stop loss? Where is the profit target?

Return JSON only:
{{
  "signal": "bullish|bearish|neutral",
  "confidence": <float 0-100>,
  "entry_type": "market|limit|wait",
  "stop_price": <float or null>,
  "target_price": <float or null>,
  "reasoning": "<setup | entry | stop | target, max 200 chars>"
}}"""
        ),
    ])

    prompt = template.invoke({
        "ticker": ticker,
        "analysis_context": json.dumps(analysis_context, indent=2),
    })

    def default_signal():
        return ApexSignal(
            signal="neutral",
            confidence=30.0,
            entry_type="wait",
            stop_price=None,
            target_price=None,
            reasoning="No intraday data available — skip this ticker",
        )

    return call_llm(
        prompt=prompt,
        pydantic_model=ApexSignal,
        agent_name=agent_id,
        state=state,
        default_factory=default_signal,
    )
