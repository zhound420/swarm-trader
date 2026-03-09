"""Mordecai Agent — Aggressive growth analyst, AI infrastructure heavy.

Investment Philosophy:
- Target Allocations:
    40% AI Infrastructure (NVDA, AVGO, SMCI, TSM)
    25% Leveraged ETFs    (TQQQ, SOXL, UPRO)
    20% Momentum          (PLTR, MSTR, COIN, RKLB)
    15% Moonshots         (IONQ, RGTI, SOUN, LUNR)
- Contrarian on consensus — if everyone agrees, be suspicious
- Size positions by conviction, not equal weight
- Cut losers fast, let winners run
- Watch allocation drift from targets
- Avoid crowded trades
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
# Target universe
# ---------------------------------------------------------------------------

UNIVERSE: dict[str, dict] = {
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


# ---------------------------------------------------------------------------
# Pydantic output model
# ---------------------------------------------------------------------------

class MordecaiSignal(BaseModel):
    signal: Literal["bullish", "bearish", "neutral"]
    confidence: float
    reasoning: str


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _get_ticker_info(ticker: str) -> tuple[str | None, dict | None]:
    """Return (category_key, category_dict) for a ticker, or (None, None)."""
    for cat_key, cat_data in UNIVERSE.items():
        if ticker in cat_data["tickers"]:
            return cat_key, cat_data
    return None, None


def _calculate_portfolio_weights(portfolio: dict) -> dict[str, float]:
    """Estimate portfolio weight per ticker using cost basis * shares."""
    positions = portfolio.get("positions", {})
    values: dict[str, float] = {}
    for ticker, pos in positions.items():
        long_shares = pos.get("long", 0) or 0
        long_cost = pos.get("long_cost_basis", 0) or 0
        values[ticker] = float(long_shares) * float(long_cost)

    total = sum(values.values()) + float(portfolio.get("cash", 0))
    if total <= 0:
        return {}
    return {t: v / total for t, v in values.items()}


def _category_weights(portfolio_weights: dict[str, float]) -> dict[str, float]:
    """Aggregate portfolio weights by Mordecai category."""
    result: dict[str, float] = {}
    for cat_key, cat_data in UNIVERSE.items():
        result[cat_key] = sum(portfolio_weights.get(t, 0.0) for t in cat_data["tickers"])
    return result


# ---------------------------------------------------------------------------
# Main agent
# ---------------------------------------------------------------------------

def mordecai_agent(state: AgentState, agent_id: str = "mordecai_agent"):
    """Mordecai — aggressive growth analyst with AI infra conviction and contrarian edge."""

    data = state["data"]
    tickers = data["tickers"]
    portfolio = data["portfolio"]

    # Pre-compute allocation context once for all tickers
    portfolio_weights = _calculate_portfolio_weights(portfolio)
    cat_weights = _category_weights(portfolio_weights)

    mordecai_analysis: dict = {}

    for ticker in tickers:
        progress.update_status(agent_id, ticker, "Analyzing allocation & conviction")

        cat_key, cat_data = _get_ticker_info(ticker)

        # Per-ticker allocation math
        current_weight = portfolio_weights.get(ticker, 0.0)

        if cat_key and cat_data:
            cat_target = cat_data["target_pct"]
            cat_current = cat_weights.get(cat_key, 0.0)
            cat_drift = cat_current - cat_target
            n_tickers_in_cat = len(cat_data["tickers"])
            ticker_target = cat_target / n_tickers_in_cat
            ticker_drift = current_weight - ticker_target
            category_label = cat_data["label"]
        else:
            cat_target = 0.0
            cat_current = 0.0
            cat_drift = 0.0
            ticker_target = 0.0
            ticker_drift = current_weight
            category_label = "Outside Mordecai Universe"

        # Allocation snapshot for the LLM
        allocation_snapshot = {
            cat: {
                "current_pct": round(cat_weights.get(cat, 0.0) * 100, 1),
                "target_pct": round(UNIVERSE[cat]["target_pct"] * 100, 1),
                "drift_pct": round((cat_weights.get(cat, 0.0) - UNIVERSE[cat]["target_pct"]) * 100, 1),
            }
            for cat in UNIVERSE
        }

        analysis_context = {
            "ticker": ticker,
            "in_mordecai_universe": cat_key is not None,
            "category": category_label,
            "category_key": cat_key,
            "ticker_current_weight_pct": round(current_weight * 100, 2),
            "ticker_target_weight_pct": round(ticker_target * 100, 2),
            "ticker_drift_pct": round(ticker_drift * 100, 2),
            "category_current_pct": round(cat_current * 100, 2),
            "category_target_pct": round(cat_target * 100, 2),
            "category_drift_pct": round(cat_drift * 100, 2),
            "full_portfolio_allocation": allocation_snapshot,
            "all_tickers_being_analyzed": tickers,
        }

        progress.update_status(agent_id, ticker, "Generating Mordecai signal")
        output = _generate_mordecai_signal(
            ticker=ticker,
            analysis_context=analysis_context,
            state=state,
            agent_id=agent_id,
        )

        mordecai_analysis[ticker] = {
            "signal": output.signal,
            "confidence": output.confidence,
            "reasoning": output.reasoning,
        }
        progress.update_status(agent_id, ticker, "Done", analysis=output.reasoning)

    message = HumanMessage(content=json.dumps(mordecai_analysis), name=agent_id)

    if state["metadata"].get("show_reasoning"):
        show_agent_reasoning(mordecai_analysis, agent_id)

    state["data"]["analyst_signals"][agent_id] = mordecai_analysis
    progress.update_status(agent_id, None, "Done")

    return {"messages": [message], "data": state["data"]}


# ---------------------------------------------------------------------------
# LLM signal generation
# ---------------------------------------------------------------------------

def _generate_mordecai_signal(
    ticker: str,
    analysis_context: dict,
    state: AgentState,
    agent_id: str = "mordecai_agent",
) -> MordecaiSignal:
    """Generate Mordecai's investment signal via LLM."""

    template = ChatPromptTemplate.from_messages([
        (
            "system",
            """You are Mordecai, an aggressive growth investor with deep conviction in AI infrastructure.

INVESTMENT PHILOSOPHY:
- Target Universe: 40% AI Infra (NVDA, AVGO, SMCI, TSM) | 25% Leveraged ETFs (TQQQ, SOXL, UPRO) | 20% Momentum (PLTR, MSTR, COIN, RKLB) | 15% Moonshots (IONQ, RGTI, SOUN, LUNR)
- CONTRARIAN: High consensus = red flag. Think independently.
- CONVICTION SIZING: Big bets on highest conviction ideas. No equal weighting.
- CUT LOSERS: Don't hold losers hoping for recovery. Exit fast.
- LET WINNERS RUN: Don't trim winners just to "de-risk". Ride the trend.
- CROWDING: When retail floods a trade, it's late. Look for next move.
- DRIFT: If a category is underweight vs target, that's your buy signal. If overweight, trim the weakest.

SIGNAL RULES:
- bullish: Category underweight OR ticker is the strongest in its bucket AND trend intact
- bearish: Not in universe AND losing momentum, OR category severely overweight, OR crowded/consensus trade
- neutral: Fairly allocated, no strong catalyst either way

Be direct. Mordecai has opinions. Short reasoning (max 150 chars)."""
        ),
        (
            "human",
            """Analyze ticker: {ticker}

Portfolio allocation context:
{analysis_context}

Assessment questions:
1. Is {ticker} in Mordecai's target universe? If yes, which category?
2. Is this category over/under its target allocation? By how much?
3. Is {ticker} a conviction hold/add or should it be cut/reduced?
4. Is this a crowded/consensus trade or a contrarian opportunity?
5. Based on Mordecai's philosophy, what action makes sense?

Return JSON only:
{{
  "signal": "bullish|bearish|neutral",
  "confidence": <float 0-100>,
  "reasoning": "<direct Mordecai-style assessment, max 150 chars>"
}}"""
        ),
    ])

    prompt = template.invoke({
        "ticker": ticker,
        "analysis_context": json.dumps(analysis_context, indent=2),
    })

    def default_signal():
        return MordecaiSignal(
            signal="neutral",
            confidence=40.0,
            reasoning="Analysis error — defaulting to neutral hold",
        )

    return call_llm(
        prompt=prompt,
        pydantic_model=MordecaiSignal,
        agent_name=agent_id,
        state=state,
        default_factory=default_signal,
    )
