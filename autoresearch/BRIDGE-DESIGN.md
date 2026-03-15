# AutoResearch → Cassius Bridge: Design Doc

*Connecting the lab to the trading floor.*

---

## Problem Statement

Two systems exist that don't talk to each other:

1. **AutoResearch** — Evolves a pure-Python `strategy.py` through backtesting. Discovers parameter combinations (RSI tiers, regime multipliers, confidence thresholds) that maximize a fitness score. No LLM calls at runtime. Deterministic.

2. **Cassius/Apex** — Runs live portfolio management via a multi-agent LLM committee (fundamentals, technicals, sentiment, risk manager, portfolio manager). Each agent makes LLM calls. The Apex agent is the intraday day-trader, but its signals come from an LLM interpreting raw data — not from the evolved strategy logic.

AutoResearch discovers things (RSI bridge tiers, range-bound bonuses) that Cassius never sees. Cassius trades live but uses none of the evolved intelligence.

---

## Architecture Overview

```
┌──────────────────────┐         ┌──────────────────────────┐
│   AutoResearch       │         │   Cassius (Live)         │
│                      │         │                          │
│  strategy.py         │         │  apex.py (LLM agent)     │
│  backtest_fast.py    │──bridge─▶  portfolio_manager.py    │
│  evolve.py           │         │  run_hedge_fund.py       │
│  experiments/log.jsonl│        │  execute_trades.py       │
│                      │         │                          │
│  Output: parameters, │         │  Input: analyst signals  │
│  rules, fitness      │         │  + risk limits           │
└──────────────────────┘         └──────────────────────────┘
```

---

## Options Evaluated

### Option A: Parameter Export (Simple Injection)

Export evolved constants from `strategy.py` into Apex's LLM prompt as "research-backed parameters."

**How it works:**
1. After evolution run, extract current best parameters into `strategy_params.json`
2. Apex agent prompt gets a new section: "Research-backed signal parameters" with the evolved values
3. LLM uses these as guidance (not hard rules) when generating signals

**Pros:**
- Minimal code changes (just prompt injection)
- Doesn't break existing multi-agent flow
- LLM retains discretion — can override if context warrants

**Cons:**
- LLM might ignore the parameters
- No guarantee the evolved logic (bridge tiers, regime bonuses) translates through natural language
- Prompt bloat

**Verdict:** Low effort, low confidence it actually changes behavior.

---

### Option B: Deterministic Pre-Filter (Recommended)

Run `strategy.py` as a pre-filter *before* the LLM agents. Its signals become an additional analyst in the committee.

**How it works:**
1. New agent: `autoresearch_agent` — wraps `strategy.py` as a LangChain-compatible analyst
2. `strategy.py` runs on live intraday data (same data Apex sees) and produces deterministic signals
3. Its output (signal, confidence, stop, target) feeds into `portfolio_manager.py` as another analyst signal alongside Apex, fundamentals, technicals, etc.
4. Portfolio manager sees it as one more vote in the committee

**Implementation:**

```python
# src/agents/autoresearch_agent.py

"""AutoResearch Agent — deterministic signals from evolved strategy."""

import json
import sys
from pathlib import Path
from langchain_core.messages import HumanMessage
from src.graph.state import AgentState, show_agent_reasoning
from src.utils.progress import progress

# Add autoresearch to path
AUTORESEARCH_DIR = Path(__file__).parent.parent.parent / "autoresearch"
sys.path.insert(0, str(AUTORESEARCH_DIR))

from strategy import generate_signals, MarketContext, StrategyConfig


def autoresearch_agent(state: AgentState, agent_id: str = "autoresearch_agent"):
    """Run the evolved strategy against live intraday data."""
    
    data = state["data"]
    tickers = data["tickers"]
    market_regime = data.get("market_regime", {})
    ticker_data_map = data.get("ticker_data", {})
    
    analysis = {}
    
    for ticker in tickers:
        progress.update_status(agent_id, ticker, "Running evolved strategy")
        
        ticker_data = ticker_data_map.get(ticker, {})
        intraday = ticker_data.get("intraday", {})
        prices = ticker_data.get("prices", {})
        
        # Build MarketContext from live data
        ctx = MarketContext(
            ticker=ticker,
            current_price=prices.get("current", 0.0),
            rsi_14=intraday.get("rsi_14"),
            vwap=intraday.get("vwap"),
            price_vs_vwap_pct=intraday.get("price_vs_vwap_pct"),
            volume_ratio=intraday.get("volume_ratio"),
            macd_histogram=intraday.get("macd_histogram"),
            macd_signal_line=intraday.get("macd_signal"),
            regime=market_regime.get("regime", "unknown"),
            # ... map remaining fields
        )
        
        signals = generate_signals(ctx)
        
        if signals:
            sig = signals[0]  # Best signal
            analysis[ticker] = {
                "signal": sig.direction,       # "bullish" or "bearish"
                "confidence": sig.confidence,
                "stop_price": sig.stop_price,
                "target_price": sig.target_price,
                "reasoning": f"AutoResearch evolved strategy | {sig.entry_reason}",
            }
        else:
            analysis[ticker] = {
                "signal": "neutral",
                "confidence": 0,
                "reasoning": "No signal from evolved strategy",
            }
        
        progress.update_status(agent_id, ticker, "Done")
    
    message = HumanMessage(content=json.dumps(analysis), name=agent_id)
    
    if state["metadata"].get("show_reasoning"):
        show_agent_reasoning(analysis, agent_id)
    
    state["data"]["analyst_signals"][agent_id] = analysis
    progress.update_status(agent_id, None, "Done")
    
    return {"messages": [message], "data": state["data"]}
```

**Graph wiring:**
- Add `autoresearch_agent` to the analyst list in `run_hedge_fund.py` `--analysts` default
- Runs in parallel with Apex, fundamentals, technicals, etc.
- Zero additional LLM calls — deterministic, instant
- Portfolio manager sees it as another signal source

**Pros:**
- Clean separation — evolved strategy is a first-class agent
- No LLM cost for the autoresearch signal (pure Python)
- Portfolio manager naturally weights it against other signals
- Easy to A/B test: add/remove from analyst list
- Respects existing architecture

**Cons:**
- Need to map `strategy.py`'s `MarketContext` to live data format
- `strategy.py` was designed for backtesting — may need minor refactor for live data shapes
- Adds one more signal the PM has to process

**Verdict:** Best balance of integration depth, architecture respect, and testability.

---

### Option C: Shadow Portfolio (Full Independence)

Run autoresearch strategy as a completely separate execution path, compare results with Cassius, blend over time.

**How it works:**
1. Separate cron job runs `strategy.py` against live data, produces signals
2. Track what it *would have* traded vs what Cassius *actually* traded
3. After N days of shadow tracking, if autoresearch outperforms, increase its weight
4. Eventually could replace Apex entirely

**Pros:**
- Zero risk to live portfolio during validation
- Clean performance comparison
- Could evolve into full replacement

**Cons:**
- Delayed value — weeks before any impact
- Needs shadow portfolio tracking infrastructure
- More complexity, more crons, more state

**Verdict:** Best for risk management, worst for time-to-value.

---

## Recommendation: Option B (Deterministic Pre-Filter)

### Implementation Plan

**Phase 1: Wire the agent (1-2 hours)**
1. Create `src/agents/autoresearch_agent.py` (skeleton above)
2. Map `strategy.py`'s `MarketContext` fields to live data from `ticker_data_map`
3. Add to default analyst list in `run_hedge_fund.py`
4. Test with `--show-reasoning` in dry run

**Phase 2: Data compatibility (1-2 hours)**
1. `strategy.py` currently expects 5-min bar arrays — live data comes as current snapshots
2. Options: (a) have `gather_data.py` fetch intraday bars for strategy.py, or (b) adapt strategy.py to work with single-point data
3. Prefer (a) — richer data, strategy.py stays unchanged

**Phase 3: Validation (1 week)**
1. Run with autoresearch_agent included but in "observe" mode (log signals, don't act)
2. Compare autoresearch signals vs Apex signals vs actual outcomes
3. Tune portfolio_manager prompt if needed to properly weight the new signal

**Phase 4: Continuous evolution feed**
1. After each autoresearch evolution run, the new `strategy.py` is automatically picked up
2. No deployment step — agent imports strategy.py at runtime
3. Add fitness score to agent output so PM can weight by strategy quality

### Data Flow (Post-Implementation)

```
5 PM: autoresearch evolves strategy.py (Flash cron)
         ↓
8 PM: autoresearch report (what changed, fitness delta)
         ↓
Next trading day 9:30 AM:
  gather_data.py fetches live data
         ↓
  autoresearch_agent runs strategy.py on live data → deterministic signals
  apex_agent runs LLM on live data → LLM signals  
  fundamentals_agent runs LLM → fundamental signals
  technicals_agent runs LLM → technical signals
         ↓
  portfolio_manager weighs all signals → trading decisions
         ↓
  execute_trades.py → Alpaca
```

### Key Design Decisions

1. **Strategy.py stays in autoresearch/, not copied.** Runtime import means evolution changes are live immediately.
2. **No LLM calls in autoresearch_agent.** It's the only zero-cost analyst. Fast, deterministic, reproducible.
3. **Portfolio manager retains final authority.** AutoResearch is one voice, not the dictator.
4. **Fitness score is visible.** PM prompt includes current strategy fitness so it can calibrate trust.
5. **Kill switch.** Remove `autoresearch_agent` from `--analysts` to disable instantly.

---

## Open Questions

1. **Intraday bars vs snapshot:** Does `gather_data.py` already fetch 5-min bars? If not, adding that is the biggest lift.
2. **Day vs swing mode:** AutoResearch currently evolves day trading strategy. Cassius does both. Should we evolve swing separately?
3. **Weight calibration:** Should PM treat autoresearch_agent as equal to Apex, or should we hint at weighting? ("This agent has been backtested to 8.78 fitness, 76.5% win rate" in the prompt.)
4. **Conflict resolution:** When autoresearch says bullish and Apex says bearish, what wins? Currently PM decides — is that sufficient?

---

*Author: Mordecai | Created: 2026-03-14 | Status: Design phase*
