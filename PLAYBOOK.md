# AI Hedge Fund + Alpaca Trading — Complete Playbook

**Purpose:** Set up multi-agent AI trading with Alpaca paper execution, from scratch. Designed for AI agents and humans alike.

---

## Table of Contents

1. [Overview](#overview)
2. [Prerequisites](#prerequisites)
3. [Setup from Scratch](#setup-from-scratch)
4. [Alpaca Paper Trading](#alpaca-paper-trading)
5. [How the Multi-Agent System Works](#how-the-multi-agent-system-works)
6. [Custom Agent Creation](#custom-agent-creation)
7. [Running Analysis](#running-analysis)
8. [Executing Trades](#executing-trades)
9. [Rebalancing](#rebalancing)
10. [Safety Rails](#safety-rails)
11. [Automation (Cron)](#automation-cron)
12. [Telegram Integration](#telegram-integration)
13. [Known Limitations](#known-limitations)
14. [Troubleshooting](#troubleshooting)
15. [File Reference](#file-reference)

---

## Overview

This system combines:
- **zhound420/swarm-trader** — Open-source multi-agent hedge fund framework
- **Alpaca Markets** — Paper trading API for order execution
- **Ollama** — Local LLM inference (zero API cost)
- **Custom agents** — Your own investment philosophy as an analyst agent

Each analyst agent embodies a different investment philosophy (Warren Buffett, Michael Burry, Cathie Wood, etc.). They independently analyze stocks, then a Portfolio Manager agent weighs all signals and makes trading decisions. Those decisions feed through safety rails and execute via Alpaca.

**Flow:**
```
Alpaca positions → AI agents analyze → Portfolio Manager decides → Safety rails validate → Alpaca executes
```

---

## Prerequisites

| Requirement | Details |
|---|---|
| Python | 3.11+ (3.14 works but shows Pydantic warnings) |
| Poetry | Python dependency manager (`curl -sSL https://install.python-poetry.org \| python3 -`) |
| Ollama | Local LLM server (`ollama serve`) with at least one model pulled |
| Alpaca account | Free paper trading account at https://app.alpaca.markets |
| Git | For cloning the repo |

### Ollama Models

The system uses Ollama for LLM inference. The default model is inherited from `openclaw.json` when `--model` is not specified. You need at least one model available:

```bash
# Local models (run on your hardware)
ollama pull llama3:8b          # Fast, decent quality
ollama pull phi3:mini           # Lightweight

# Cloud models via Ollama (routed through Ollama's cloud, free tier available)
# These appear automatically if configured in your Ollama setup:
# qwen3.5:cloud, glm-4.7:cloud, kimi-k2.5:cloud
```

---

## Setup from Scratch

### Step 1: Clone the repo

```bash
cd ~/your-workspace/projects
git clone https://github.com/zhound420/swarm-trader.git
cd swarm-trader
```

### Step 2: Install dependencies

```bash
# Install Poetry if you don't have it
curl -sSL https://install.python-poetry.org | python3 -

# Add Poetry to PATH (add to your shell profile)
export PATH="$HOME/.local/bin:$PATH"

# Install project dependencies
poetry install
```

### Step 3: Configure environment

Create `.env` in the project root:

```bash
cat > .env << 'EOF'
# Financial data - free for AAPL, GOOGL, MSFT, NVDA, TSLA
# For other tickers, get a key from https://financialdatasets.ai/
FINANCIAL_DATASETS_API_KEY=

# LLM providers (only needed if NOT using Ollama)
OPENAI_API_KEY=
ANTHROPIC_API_KEY=
GROQ_API_KEY=
DEEPSEEK_API_KEY=
GOOGLE_API_KEY=
EOF
```

### Step 4: Configure Ollama models

Edit `src/llm/ollama_models.json` to match your available models:

```json
[
  {
    "display_name": "Llama 3 (8B Local)",
    "model_name": "llama3:8b",
    "provider": "Ollama"
  }
]
```

Add any models available in your Ollama instance. The `model_name` must match exactly what `ollama list` shows.

### Step 5: Verify setup

```bash
# Quick test — analyze NVDA with 3 agents
poetry run python src/main.py --tickers NVDA --ollama --model llama3:8b \
  --analysts warren_buffett,michael_burry,cathie_wood --show-reasoning
```

If you see agent analysis output and a portfolio summary, you're good.

---

## Alpaca Paper Trading

### Getting API Keys

1. Sign up at https://app.alpaca.markets (free)
2. Go to Paper Trading → API Keys
3. Generate a new key pair
4. You get: **API Key ID** and **API Secret Key**

### API Endpoints

| Endpoint | URL |
|---|---|
| Paper Trading | `https://paper-api.alpaca.markets/v2` |
| Live Trading | `https://api.alpaca.markets/v2` (⚠️ NEVER use for automated trading without extreme caution) |

### Test Your Keys

```bash
curl -s "https://paper-api.alpaca.markets/v2/account" \
  -H "APCA-API-KEY-ID: YOUR_KEY_HERE" \
  -H "APCA-API-SECRET-KEY: YOUR_SECRET_HERE" | python3 -m json.tool
```

You should see your account info with `status: ACTIVE`.

### Key API Calls

```bash
# Get account info
GET /v2/account

# Get all positions
GET /v2/positions

# Get open orders
GET /v2/orders?status=open

# Place a market order
POST /v2/orders
{
  "symbol": "NVDA",
  "qty": "10",
  "side": "buy",       # or "sell"
  "type": "market",
  "time_in_force": "day"
}

# Cancel all open orders
DELETE /v2/orders

# Cancel specific order
DELETE /v2/orders/{order_id}
```

### Setting Up the Integration

The file `src/alpaca_integration.py` reads credentials from environment variables. **Never hardcode keys in source files.**

Add your keys to the `.env` file in the project root:

```bash
# In .env (gitignored, never committed)
ALPACA_API_KEY=YOUR_KEY_HERE
ALPACA_API_SECRET=YOUR_SECRET_HERE
```

The `.env` file is automatically loaded by `python-dotenv` in `run_hedge_fund.py` and `rebalance.py`. The `alpaca_integration.py` module will raise an error if keys are missing.

Alternatively, export them as environment variables:

```bash
export ALPACA_API_KEY="YOUR_KEY_HERE"
export ALPACA_API_SECRET="YOUR_SECRET_HERE"
```

For OpenClaw agents, you can also use OpenClaw's SecretRef system:
```bash
openclaw secrets configure
```
This supports `env`, `file`, and `exec` providers for secret resolution.

---

## How the Multi-Agent System Works

### Architecture

```
┌─────────────────────────────────────────────────┐
│                   Input Layer                     │
│  Tickers + Date Range + Portfolio State           │
└──────────────────────┬──────────────────────────┘
                       │
        ┌──────────────┼──────────────┐
        ▼              ▼              ▼
┌──────────────┐ ┌───────────┐ ┌────────────┐
│ Data Agents  │ │ LLM Agents│ │ Risk Agent │
│              │ │           │ │            │
│ Fundamentals │ │ Buffett   │ │ Volatility │
│ Technical    │ │ Burry     │ │ Correlation│
│ Growth       │ │ Cathie    │ │ Position   │
│ Sentiment    │ │ Apex  │ │ Sizing     │
│ News         │ │ + 9 more  │ │            │
└──────┬───────┘ └─────┬─────┘ └─────┬──────┘
       │               │             │
       └───────────────┼─────────────┘
                       ▼
              ┌─────────────────┐
              │ Portfolio Manager│
              │                 │
              │ Weighs all      │
              │ signals, decides│
              │ buy/sell/hold   │
              └────────┬────────┘
                       ▼
              ┌─────────────────┐
              │  Safety Rails   │
              │ + Alpaca Exec   │
              └─────────────────┘
```

### Agent Types

**Data-driven agents** (no LLM, pure calculation):
- `fundamentals_analyst` — ROE, margins, growth rates, P/E, P/B, P/S
- `technical_analyst` — Trend following, mean reversion, momentum, volatility, statistical arbitrage
- `growth_analyst` — Revenue acceleration, R&D investment, operating leverage
- `news_sentiment` — Recent news sentiment analysis
- `sentiment_analyst` — Market sentiment indicators

**LLM personality agents** (each has a distinct investment philosophy):
- `warren_buffett` — Value investing, margin of safety, moats
- `michael_burry` — Contrarian deep value, FCF analysis
- `cathie_wood` — Disruptive innovation, exponential growth
- `charlie_munger` — Quality companies at fair prices
- `peter_lynch` — Growth at a reasonable price (GARP)
- `bill_ackman` — Activist investing, concentrated positions
- `stanley_druckenmiller` — Macro-driven, asymmetric bets
- `ben_graham` — Deep value, net-net analysis
- `phil_fisher` — Scuttlebutt, qualitative growth analysis
- `aswath_damodaran` — Rigorous DCF valuation
- `rakesh_jhunjhunwala` — Emerging market growth
- `mohnish_pabrai` — Dhandho framework, low risk/high uncertainty

**Custom agents** (your own philosophy):
- `apex` — Aggressive growth, AI infrastructure heavy, contrarian (see below)

### Signal Format

Every agent returns the same structure per ticker:

```json
{
  "signal": "bullish|bearish|neutral",
  "confidence": 0-100,
  "reasoning": "Why this signal"
}
```

The Portfolio Manager aggregates all signals and decides:

```json
{
  "action": "buy|sell|hold",
  "quantity": 10,
  "confidence": 85,
  "reasoning": "Consensus analysis"
}
```

---

## Custom Agent Creation

To create your own analyst agent (like we did with Apex):

### Step 1: Create the agent file

Create `src/agents/your_agent.py`:

```python
"""Your Agent — describe the investment philosophy here."""

import json
from langchain_core.messages import HumanMessage
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel
from typing_extensions import Literal

from src.graph.state import AgentState, show_agent_reasoning
from src.utils.llm import call_llm
from src.utils.progress import progress


class YourSignal(BaseModel):
    signal: Literal["bullish", "bearish", "neutral"]
    confidence: float
    reasoning: str


def your_agent(state: AgentState, agent_id: str = "your_agent"):
    """Your agent — describe what it does."""
    
    data = state["data"]
    tickers = data["tickers"]
    portfolio = data["portfolio"]
    
    analysis: dict = {}
    
    for ticker in tickers:
        progress.update_status(agent_id, ticker, "Analyzing")
        
        # Build your analysis context
        # Access portfolio["positions"][ticker] for current holdings
        # Access data["analyst_signals"] for other agents' signals (if they ran first)
        
        analysis_context = {
            "ticker": ticker,
            # Add whatever context your agent needs
        }
        
        # Call the LLM with your prompt
        template = ChatPromptTemplate.from_messages([
            ("system", """Your agent's system prompt here.
            
Define the investment philosophy, signal rules, and output format."""),
            ("human", """Analyze ticker: {ticker}
            
Context: {analysis_context}

Return JSON:
{{
  "signal": "bullish|bearish|neutral",
  "confidence": <float 0-100>,
  "reasoning": "<your reasoning>"
}}"""),
        ])
        
        prompt = template.invoke({
            "ticker": ticker,
            "analysis_context": json.dumps(analysis_context, indent=2),
        })
        
        def default_signal():
            return YourSignal(signal="neutral", confidence=40.0, reasoning="Analysis error")
        
        output = call_llm(
            prompt=prompt,
            pydantic_model=YourSignal,
            agent_name=agent_id,
            state=state,
            default_factory=default_signal,
        )
        
        analysis[ticker] = {
            "signal": output.signal,
            "confidence": output.confidence,
            "reasoning": output.reasoning,
        }
        progress.update_status(agent_id, ticker, "Done", analysis=output.reasoning)
    
    message = HumanMessage(content=json.dumps(analysis), name=agent_id)
    
    if state["metadata"].get("show_reasoning"):
        show_agent_reasoning(analysis, agent_id)
    
    state["data"]["analyst_signals"][agent_id] = analysis
    progress.update_status(agent_id, None, "Done")
    
    return {"messages": [message], "data": state["data"]}
```

### Step 2: Register the agent

Edit `src/utils/analysts.py`:

1. Add the import at the top:
```python
from src.agents.your_agent import your_agent
```

2. Add to ANALYST_CONFIG dict (use the next `order` number):
```python
"your_agent_name": {
    "display_name": "Your Agent Display Name",
    "description": "One-line description",
    "investing_style": "Detailed investment philosophy description.",
    "agent_func": your_agent,
    "type": "analyst",
    "order": 18,  # Next available number
},
```

### Step 3: Use it

```bash
poetry run python run_hedge_fund.py --analysts your_agent_name,warren_buffett --tickers NVDA
```

---

## Running Analysis

### Using run_hedge_fund.py (recommended)

This is the all-in-one runner that connects Alpaca + Analysis + Execution:

```bash
# Dry run — analyze all current holdings, show what trades WOULD happen
poetry run python run_hedge_fund.py

# Analyze specific tickers only
poetry run python run_hedge_fund.py --tickers NVDA,AVGO,TSM

# Show detailed reasoning from each agent
poetry run python run_hedge_fund.py --show-reasoning

# Use a different model
poetry run python run_hedge_fund.py --model qwen3.5:cloud

# Use specific analysts
poetry run python run_hedge_fund.py --analysts warren_buffett,apex,technical_analyst

# Telegram-friendly output (no tables, bullet lists)
poetry run python run_hedge_fund.py --telegram

# Actually execute trades (⚠️ places real orders on Alpaca)
poetry run python run_hedge_fund.py --execute
```

### Using the original CLI (standalone, no Alpaca)

```bash
# Interactive mode — prompts for model, analysts, etc.
poetry run python src/main.py

# Non-interactive with all flags
poetry run python src/main.py --tickers NVDA,AVGO --ollama --model llama3:8b \
  --analysts warren_buffett,michael_burry,cathie_wood --show-reasoning
```

### Performance Notes

| Tickers | Agents | Model | Approx Time |
|---|---|---|---|
| 1 | 3 | llama3:8b | ~30 seconds |
| 1 | 6 | llama3:8b | ~1 minute |
| 20 | 6 | llama3:8b | ~5-8 minutes |
| 1 | all (18) | llama3:8b | ~3-5 minutes |

Cloud models (qwen3.5:cloud) are slower due to network latency but may give better analysis quality.

---

## Executing Trades

### Automated (via run_hedge_fund.py)

```bash
# This will actually place orders
poetry run python run_hedge_fund.py --execute
```

The system:
1. Fetches your current Alpaca positions
2. Runs multi-agent analysis
3. Portfolio Manager generates buy/sell/hold decisions
4. Safety rails validate each trade
5. Valid trades are placed as market orders

### Manual Rebalance (rebalance.py)

For planned portfolio restructuring (selling multiple positions outside your strategy):

```bash
# Edit rebalance.py to set:
# - SELL_TICKERS: list of tickers to sell
# - API credentials
# - MIN_KEEP_PCT: minimum % to keep (default 10%)

poetry run python rebalance.py
```

### Direct API (for one-off trades)

```bash
# Buy 10 shares of NVDA
curl -X POST "https://paper-api.alpaca.markets/v2/orders" \
  -H "APCA-API-KEY-ID: YOUR_KEY" \
  -H "APCA-API-SECRET-KEY: YOUR_SECRET" \
  -H "Content-Type: application/json" \
  -d '{"symbol":"NVDA","qty":"10","side":"buy","type":"market","time_in_force":"day"}'

# Check order status
curl "https://paper-api.alpaca.markets/v2/orders?status=open" \
  -H "APCA-API-KEY-ID: YOUR_KEY" \
  -H "APCA-API-SECRET-KEY: YOUR_SECRET"

# Cancel all open orders
curl -X DELETE "https://paper-api.alpaca.markets/v2/orders" \
  -H "APCA-API-KEY-ID: YOUR_KEY" \
  -H "APCA-API-SECRET-KEY: YOUR_SECRET"
```

---

## Safety Rails

Built into `src/alpaca_integration.py`. These protect against catastrophic trades:

| Rail | Default | What It Does |
|---|---|---|
| Max trade size | 5% of portfolio | No single trade can exceed 5% of total portfolio value |
| Max daily trades | 5 per session | Prevents runaway trading loops |
| Min confidence | 70% | Portfolio Manager must be ≥70% confident |
| Min keep | 10% | Never sell entire position (always keep at least 10%) |
| Paper only | Enforced | Hardcoded to paper-api endpoint |
| Dry run default | On | Must explicitly pass `--execute` to place orders |

### Adjusting Rails

Edit the constants at the top of `src/alpaca_integration.py`:

```python
MAX_TRADE_PCT = 0.05       # Max 5% of portfolio per trade
MAX_DAILY_TRADES = 5       # Max 5 trades per run
MIN_KEEP_PCT = 0.10        # Keep at least 10% of any position when selling
MIN_CONFIDENCE = 70        # Minimum confidence % to execute a trade
```

For a planned rebalance, you may want to temporarily increase `MAX_TRADE_PCT` or `MAX_DAILY_TRADES`, then reset them after.

---

## Automation (Cron)

### OpenClaw Cron Setup

The system is designed to run on autopilot with OpenClaw cron jobs. Here's a production-tested setup:

#### 1. Daily Portfolio Check (every day, 9 AM)

Lightweight check — just reads positions from Alpaca and reports P/L. No analysis, no trades.

```bash
openclaw cron add alpaca-portfolio \
  --cron "0 9 * * *" \
  --tz "America/New_York" \
  --exact \
  --model google/gemini-2.5-flash \
  --session isolated \
  --message "Check Alpaca paper trading portfolio. API endpoint: https://paper-api.alpaca.markets/v2. Use API Key from env var ALPACA_API_KEY and Secret from ALPACA_API_SECRET. If env vars aren't set, read them from the .env file at ~/projects/swarm-trader/.env. Report: total portfolio value, top 5 positions by value, biggest movers (>5% swing), and daily P/L. Keep it concise — bullet format, no tables." \
  --announce \
  --channel telegram \
  --to YOUR_CHAT_ID
```

#### 2. Morning Analysis Scan (weekdays, 6:30 AM — pre-market)

Runs multi-agent analysis on all holdings. No execution — dry run only.

```bash
openclaw cron add morning-analysis \
  --cron "30 6 * * 1-5" \
  --tz "America/New_York" \
  --exact \
  --model google/gemini-2.5-flash \
  --session isolated \
  --message "Run the AI hedge fund analysis. cd ~/projects/swarm-trader && poetry run python run_hedge_fund.py --telegram. Send the output summary." \
  --announce \
  --channel telegram \
  --to YOUR_CHAT_ID
```

#### 3. Midday & Afternoon Scans (weekdays)

Additional analysis windows for intraday monitoring:

```bash
# Midday (12 PM)
openclaw cron add midday-scan \
  --cron "0 12 * * 1-5" \
  --tz "America/New_York" \
  --exact \
  --model google/gemini-2.5-flash \
  --session isolated \
  --message "Run midday portfolio analysis: cd ~/projects/swarm-trader && poetry run python run_hedge_fund.py --telegram --analysts technical_analyst,apex"

# Afternoon (2 PM)
openclaw cron add afternoon-scan \
  --cron "0 14 * * 1-5" \
  --tz "America/New_York" \
  --exact \
  --model google/gemini-2.5-flash \
  --session isolated \
  --message "Run afternoon portfolio analysis with full agent panel: cd ~/projects/swarm-trader && poetry run python run_hedge_fund.py --telegram"
```

#### 4. Evening Research (weekdays, 4:30 PM — post-close)

Deeper analysis after market close, when all daily data is final:

```bash
openclaw cron add evening-research \
  --cron "30 16 * * 1-5" \
  --tz "America/New_York" \
  --exact \
  --model google/gemini-2.5-flash \
  --session isolated \
  --message "Run end-of-day portfolio review: cd ~/projects/swarm-trader && poetry run python run_hedge_fund.py --telegram --show-reasoning. Summarize the day's signals and any overnight action items." \
  --announce \
  --channel telegram \
  --to YOUR_CHAT_ID
```

### Recommended Cron Schedule (Summary)

| Job | Schedule | Purpose |
|---|---|---|
| `alpaca-portfolio` | Daily 9:00 AM | Quick P/L check (all days, including weekends for visibility) |
| `morning-analysis` | Mon-Fri 6:30 AM | Pre-market multi-agent analysis |
| `midday-scan` | Mon-Fri 12:00 PM | Midday pulse check (lighter agent set) |
| `afternoon-scan` | Mon-Fri 2:00 PM | Afternoon analysis |
| `evening-research` | Mon-Fri 4:30 PM | Post-close deep analysis with reasoning |

### Key Design Decisions

- **Use `google/gemini-2.5-flash`** for cron jobs to save primary model tokens. Flash is fast and cheap — fine for portfolio reads and running the analysis scripts.
- **Use `--session isolated`** so cron runs don't pollute your main session history.
- **Use `--exact`** to disable cron staggering — you want market-timed jobs to run at the specified time.
- **Never put API keys in the cron `--message`**. Reference env vars or `.env` files instead.
- **Use `--announce`** to deliver results to Telegram/Discord/etc. without routing through the main session.

### Heartbeat Integration

For agents using OpenClaw heartbeats (periodic wake-ups), add to your `HEARTBEAT.md`:

```markdown
### Alpaca Portfolio Check (daily, morning)
- API: `https://paper-api.alpaca.markets/v2`
- Credentials: Read from `projects/swarm-trader/.env` (ALPACA_API_KEY, ALPACA_API_SECRET)
- Check positions, daily P/L, total portfolio value
- Alert on big movers (>5% single position swing)
- Post summary to Telegram
- Track in `memory/heartbeat-state.json` under `alpacaPortfolio`
- Consider rebalancing if any position drifts >50% from target allocation
```

The heartbeat approach is lighter than a cron — the agent checks on its regular 30-minute wake cycle and only reports if something interesting happened. Use this for monitoring; use cron for scheduled analysis.

### Manual Quick Check

```bash
# One-liner portfolio check (no analysis, just positions)
source ~/projects/swarm-trader/.env
curl -s "https://paper-api.alpaca.markets/v2/positions" \
  -H "APCA-API-KEY-ID: $ALPACA_API_KEY" \
  -H "APCA-API-SECRET-KEY: $ALPACA_API_SECRET" | \
  python3 -c "
import sys,json
positions = json.load(sys.stdin)
total = sum(float(p['market_value']) for p in positions)
print(f'Portfolio: \${total:,.2f} ({len(positions)} positions)')
for p in sorted(positions, key=lambda x: abs(float(x['market_value'])), reverse=True)[:5]:
    pl = float(p['unrealized_pl'])
    print(f'  {p[\"symbol\"]}: \${float(p[\"market_value\"]):,.2f} ({(\"+\" if pl>=0 else \"\")}{pl:,.2f})')
"
```

---

## Telegram Integration

The `--telegram` flag on `run_hedge_fund.py` outputs a clean format suitable for Telegram (bullet lists, no markdown tables).

### Via OpenClaw Cron (recommended)

Set up a cron job with `--announce --channel telegram --to YOUR_CHAT_ID` (see Automation section above). The cron runner handles delivery automatically.

### From an OpenClaw Agent Session

```python
# Use the message tool directly
message(
    action="send",
    channel="telegram",
    target="YOUR_CHAT_ID",
    message=analysis_output
)
```

### From a Script

```python
import subprocess

result = subprocess.run(
    ["poetry", "run", "python", "run_hedge_fund.py", "--telegram"],
    capture_output=True, text=True,
    cwd="/path/to/swarm-trader"
)
# result.stdout contains the Telegram-formatted summary
```

---

## Known Limitations

1. **Financial data coverage**: `api.py` is a hybrid dispatcher — it tries financialdatasets.ai first (free tier covers AAPL, GOOGL, MSFT, NVDA, TSLA), then automatically falls back to yfinance + SEC EDGAR for other tickers or API failures. Check `[api]` log lines to see which source was used. For tickers where both sources lack data, use `technical_analyst` + `apex` (no API dependency). A paid financialdatasets.ai key expands primary coverage beyond the 5 free tickers.

2. **ETFs and leveraged products**: TQQQ, SOXL, UPRO, XLE don't have company fundamentals. Agents that rely on company data will return bearish-by-default or neutral. The technical analyst and custom agents (like Apex) work fine on ETFs.

3. **Weekend/after-hours**: Market orders placed when market is closed get `status: accepted` and execute at next market open. This is fine for paper trading but be aware of gap risk.

4. **Fractional shares**: Alpaca supports fractional shares but the system casts to `int`. Positions with fractional shares (e.g., 0.35 shares of PLTR) may show as 0 shares in the analysis.

5. **Rate limiting**: financialdatasets.ai has rate limits. With 20+ tickers and multiple agents, you may hit 429 errors. The API client has built-in retry with backoff (60s, 90s, 120s).

6. **Model quality**: `llama3:8b` is fast but sometimes gives weak analysis. For important decisions, use a larger/smarter model like `qwen3.5:cloud` or `kimi-k2.5:cloud`.

---

## Troubleshooting

### "Insufficient data" for most tickers
**Cause:** No `FINANCIAL_DATASETS_API_KEY` set, or using free tier.
**Fix:** Get a paid key, or rely on technical_analyst + your custom agent (these don't need financial data APIs).

### "insufficient qty available for order"
**Cause:** Shares are held by existing open orders.
**Fix:** Cancel existing orders first: `DELETE /v2/orders`

### "Model not found" with Ollama
**Cause:** Model name in `ollama_models.json` doesn't match `ollama list`.
**Fix:** Run `ollama list` and update `ollama_models.json` to match exactly.

### Process hangs on LLM agents
**Cause:** Cloud model timeout or Ollama not running.
**Fix:** Check `ollama serve` is running. Use local models for reliability.

### Pydantic V1 warning with Python 3.14
**Harmless.** The warning is: "Core Pydantic V1 functionality isn't compatible with Python 3.14 or greater." Everything still works.

---

## File Reference

```
swarm-trader/
├── .env                          # API keys (don't commit!)
├── pyproject.toml                # Python dependencies (Poetry)
├── run_hedge_fund.py               # ⭐ Main runner — analysis + Alpaca execution
├── rebalance.py                  # Manual rebalance script
├── PLAYBOOK.md                   # This file
├── src/
│   ├── main.py                   # Core hedge fund engine
│   ├── alpaca_integration.py     # ⭐ Alpaca API + safety rails
│   ├── agents/
│   │   ├── apex.py           # ⭐ Custom agent (our investment philosophy)
│   │   ├── warren_buffett.py     # Buffett agent
│   │   ├── michael_burry.py      # Burry agent
│   │   ├── cathie_wood.py        # Cathie Wood agent
│   │   ├── fundamentals.py       # Data-driven fundamentals
│   │   ├── technicals.py         # Data-driven technicals
│   │   └── ... (12 more agents)
│   ├── graph/
│   │   └── state.py              # Agent state management
│   ├── llm/
│   │   ├── models.py             # Model configuration
│   │   └── ollama_models.json    # ⭐ Available Ollama models
│   ├── tools/
│   │   ├── api.py                # ⭐ Hybrid data dispatcher (financialdatasets.ai → yfinance/SEC fallback)
│   │   ├── api_original.py       # Original financialdatasets.ai-only client
│   │   └── api_free.py           # yfinance/SEC EDGAR-only client
│   └── utils/
│       ├── analysts.py           # ⭐ Agent registry (add new agents here)
│       ├── llm.py                # LLM call helper with retries
│       └── progress.py           # Progress display
└── app/                          # Web UI (optional)
```

**⭐ = Files you'll likely need to modify when setting up on a new agent.**

---

---

## Day Trading Mode

The system supports two operating modes that run side-by-side. Swing mode is the original buy-and-hold mode. Day trading mode is intraday only — positions are sized for single-day holds with mandatory stops.

### Universe

| Mode | Universe | Why |
|---|---|---|
| Swing | NVDA, AVGO, SMCI, TSM, TQQQ, SOXL, UPRO, PLTR, MSTR, COIN, RKLB, IONQ, RGTI, SOUN, LUNR | Long-term AI infra thesis |
| Day | NVDA, AVGO, TSM, AMD, MSFT, AAPL, META, GOOGL, AMZN, PLTR, COIN, MSTR, RKLB, SPY, QQQ | Liquid names only — tight spreads, deep books |

**What was removed from the day trade universe and why:**
- `IONQ, RGTI, SOUN, LUNR` — too illiquid for day trading (wide spreads, shallow books, slippage kills edge)
- `TQQQ, SOXL, UPRO` — 3x leverage amplifies overnight gap risk. Fine for swing, not for intraday.
- `SMCI` — too volatile and news-driven for reliable technical setups

### Risk Parameters (Day Mode)

| Parameter | Value | Meaning |
|---|---|---|
| `MAX_RISK_PER_TRADE` | 2% | Risk at most 2% of portfolio per trade |
| `MAX_PORTFOLIO_HEAT` | 10% | Max 10% of portfolio at risk across all open stops |
| `MAX_POSITION_SIZE` | 15% | No single position larger than 15% of portfolio |
| `DEFAULT_STOP_PCT` | 2% | Auto-stop at 2% below entry if agent doesn't provide one |
| `DEFAULT_TARGET_MULTIPLIER` | 2.0 | Target = entry + 2× stop distance (minimum 2:1 R:R) |
| `FLATTEN_BY` | 15:45 ET | Flatten speculative/leveraged positions by 3:45 PM |
| `MAX_LOSS_PER_DAY` | 3% | Circuit breaker — no new entries if down 3% on the day |

These constants live in `src/config.py` and are imported by `execute_trades.py` and `src/alpaca_integration.py`.

### New Agents

**`market_regime`** — Runs once per analysis cycle. Classifies SPY's intraday action:
- `trending_up` → buy dips to VWAP, ride momentum
- `trending_down` → sell rips, short weakness
- `range_bound` → fade extremes, quick profits, tight stops
- `volatile` → cut size 50%+ or sit out

**`apex`** (rewritten) — Now a day trader, not a growth investor. For each ticker:
- Reads VWAP, RSI, volume ratio, today's high/low, prev close
- Reads the market regime from `market_regime`
- Returns: `signal`, `confidence`, `entry_type` (market/limit/wait), `stop_price`, `target_price`

Run `market_regime` before `apex` for best results:
```bash
poetry run python run_hedge_fund.py --analysts market_regime,apex --tickers NVDA,AAPL,SPY
```

### Gathering Intraday Data

```bash
# Day mode — fetches 5-min bars, VWAP, RSI, volume from Alpaca data API
poetry run python gather_data.py --mode day

# Include the full day trade universe
poetry run python gather_data.py --mode day --include-universe

# Specific tickers (always adds SPY + QQQ automatically for regime)
poetry run python gather_data.py --mode day --tickers NVDA,AAPL,META
```

The `--mode day` payload includes per-ticker:
- `intraday.vwap` — Volume-weighted average price for the day
- `intraday.rsi_14` — RSI(14) on 5-min bars
- `intraday.price_vs_vwap_pct` — % above/below VWAP
- `intraday.high/low/open` — Today's intraday range
- `intraday.prev_close` — Previous day's close (for gap analysis)
- `intraday.premarket_high/low` — Pre-market range
- `intraday.volume_ratio` — Today's volume vs 20-day average
- `intraday.bars_5min` — Raw 5-min OHLCV bars

### Executing Day Trades

```bash
# All buy orders now auto-get brackets (stop + take profit) unless you specify otherwise
echo '{"trades":[{"ticker":"NVDA","action":"buy","qty":5,"reasoning":"VWAP bounce"}]}' \
  | poetry run python execute_trades.py

# With explicit bracket prices (preferred — use key levels, not default pcts)
echo '{"trades":[{"ticker":"NVDA","action":"buy","qty":5,"stop_price":880,"take_profit":920,"reasoning":"VWAP bounce"}]}' \
  | poetry run python execute_trades.py

# Short selling (action="short")
echo '{"trades":[{"ticker":"AAPL","action":"short","qty":10,"stop_price":195,"take_profit":185,"reasoning":"break below VWAP, SPY weak"}]}' \
  | poetry run python execute_trades.py

# Flatten ALL positions at market (end-of-day)
poetry run python execute_trades.py --flatten

# Preview flatten (dry run)
poetry run python execute_trades.py --flatten --dry-run
```

### Bracket Auto-Fill Logic

If you (or Cassius) submit a buy/short trade without `stop_price`:
1. `execute_trades.py` looks up the current price from Alpaca
2. Calculates stop at `current_price × (1 - DEFAULT_STOP_PCT)` = 2% below entry
3. Calculates target at `entry + stop_distance × DEFAULT_TARGET_MULTIPLIER` = 2:1 R:R
4. Submits as a bracket order

Prefer providing explicit stops at actual key levels (VWAP, day low, round numbers). The auto-fill is a safety net, not a substitute for analysis.

### Flatten-by-Close Concept

Day traders must exit speculative positions before market close to avoid overnight gap risk. The `FLATTEN_BY = "15:45"` constant is the target time. This is not enforced automatically — Cassius needs to call `execute_trades.py --flatten` as part of the end-of-day cron:

```bash
# In your cron at 3:45 PM ET (15:45 ET = 12:45 PT)
openclaw cron add flatten-eod \
  --cron "45 12 * * 1-5" \
  --tz "America/New_York" \
  --exact \
  --message "Flatten all open day trade positions. cd ~/projects/swarm-trader && poetry run python execute_trades.py --flatten. Report what was closed."
```

For swing positions (NVDA core, etc.) that you want to hold overnight, do NOT call `--flatten`. Instead, specify tickers in a partial flatten by piping a sell decision for only speculative positions.

### Intraday Cron Schedule (Day Trading Mode)

| Job | Schedule (ET) | Purpose |
|---|---|---|
| `premarket-scan` | Mon-Fri 5:00 AM | Gather intraday data, run `market_regime + apex` on day trade universe |
| `open-watch` | Mon-Fri 6:35 AM | 5 min after open — first signals, execute if clear setups |
| `midday-check` | Mon-Fri 9:00 AM | Midday regime check, manage open positions |
| `flatten-eod` | Mon-Fri 12:45 PM | Flatten speculative positions before close (3:45 PM ET) |
| `post-close` | Mon-Fri 1:05 PM | Review trades, log P&L, set watchlist for tomorrow |

### Switching Between Modes

```bash
# Swing mode (default — fundamentals, news, original universe)
poetry run python gather_data.py --mode swing --include-universe
poetry run python run_hedge_fund.py --analysts warren_buffett,apex,technical_analyst

# Day trading mode (intraday technicals, day trade universe)
poetry run python gather_data.py --mode day --include-universe
poetry run python run_hedge_fund.py --analysts market_regime,apex --tickers NVDA,AAPL,META,SPY
```

The two modes share the same codebase. Swing mode still works exactly as before — `UNIVERSE`, `ALL_UNIVERSE_TICKERS`, and `UNIVERSE_SIMPLE` are all backward-compatible aliases for `SWING_UNIVERSE`.

---

## Quick Start Checklist

For a new OpenClaw agent to get trading:

- [ ] Clone repo: `git clone https://github.com/zhound420/swarm-trader.git`
- [ ] Install: `poetry install`
- [ ] Create `.env` with API keys
- [ ] Update `ollama_models.json` with available models
- [ ] Add Alpaca credentials to `.env` file
- [ ] (Optional) Create custom agent in `src/agents/` and register in `src/utils/analysts.py`
- [ ] Test: `poetry run python run_hedge_fund.py --tickers NVDA`
- [ ] Go live: `poetry run python run_hedge_fund.py --execute`
- [ ] Automate: Add cron for daily morning analysis
- [ ] Monitor: Add Alpaca portfolio check to heartbeat
