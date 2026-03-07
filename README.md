# AI Hedge Fund — Multi-Agent Trading System

A multi-agent AI hedge fund that uses LLM-powered analyst agents to make trading decisions, with Alpaca paper trading for execution and built-in safety rails.

> **For AI agents:** Read [PLAYBOOK.md](./PLAYBOOK.md) — it contains everything you need to set up, customize, automate, and operate this system end-to-end.

Built on [virattt/ai-hedge-fund](https://github.com/virattt/ai-hedge-fund), extended with:
- **Alpaca paper trading** — order execution with safety rails (max trade size, confidence thresholds, position protection)
- **Custom analyst agents** — create agents encoding your own investment philosophy
- **Automated pipeline** — cron-based daily analysis, portfolio monitoring, and Telegram/Discord reporting
- **Agent-native design** — `.env` for secrets, CLI flags for everything, structured output for programmatic consumption

---

## Architecture

```
┌─────────────────────────────────────────────────────┐
│              Alpaca Portfolio State                   │
│         (positions, cash, market values)              │
└──────────────────────┬──────────────────────────────┘
                       │
        ┌──────────────┼──────────────┐
        ▼              ▼              ▼
┌──────────────┐ ┌───────────┐ ┌────────────┐
│ Data Agents  │ │ LLM Agents│ │ Risk Agent │
│              │ │           │ │            │
│ Fundamentals │ │ Buffett   │ │ Volatility │
│ Technical    │ │ Burry     │ │ Correlation│
│ Sentiment    │ │ Wood      │ │ Position   │
│ Growth       │ │ + 9 more  │ │ Sizing     │
│ News         │ │ + custom  │ │            │
└──────┬───────┘ └─────┬─────┘ └─────┬──────┘
       │               │             │
       └───────────────┼─────────────┘
                       ▼
              ┌─────────────────┐
              │ Portfolio Manager│
              │                 │
              │ Weighs signals, │
              │ decides actions │
              └────────┬────────┘
                       ▼
              ┌─────────────────┐
              │  Safety Rails   │
              │  + Alpaca Exec  │
              └─────────────────┘
```

Each analyst agent independently analyzes every ticker and returns a signal (`bullish` / `bearish` / `neutral`) with confidence and reasoning. The Portfolio Manager aggregates all signals and decides `buy` / `sell` / `hold` with quantities. Safety rails validate before execution.

---

## Quick Start

### Prerequisites

| Requirement | Install |
|---|---|
| Python 3.11+ | System package manager |
| Poetry | `curl -sSL https://install.python-poetry.org \| python3 -` |
| Ollama | [ollama.ai](https://ollama.ai/) — then `ollama pull llama3:8b` |
| Alpaca account | [app.alpaca.markets](https://app.alpaca.markets) (free paper trading) |

### 1. Clone & Install

```bash
git clone https://github.com/zhound420/ai-hedge-fund.git
cd ai-hedge-fund
poetry install
```

### 2. Configure Secrets

```bash
cp .env.example .env
# Edit .env with your keys
```

Required in `.env`:
```bash
ALPACA_API_KEY=your-alpaca-key
ALPACA_API_SECRET=your-alpaca-secret
```

Optional:
```bash
FINANCIAL_DATASETS_API_KEY=   # Free for AAPL, GOOGL, MSFT, NVDA, TSLA — paid for others
OPENAI_API_KEY=               # Only if NOT using Ollama
```

> **Important:** All secrets live in `.env` (gitignored). Source files read from env vars only — no hardcoded keys anywhere.

### 3. Configure Ollama Models

Edit `src/llm/ollama_models.json` to match your available models:

```json
[
  {
    "display_name": "Llama 3 (8B)",
    "model_name": "llama3:8b",
    "provider": "Ollama"
  }
]
```

Run `ollama list` to see available models. The `model_name` must match exactly.

### 4. Run

```bash
# Dry run — analyze all Alpaca holdings, show what trades WOULD happen
poetry run python run_hedge_fund.py

# Analyze specific tickers
poetry run python run_hedge_fund.py --tickers NVDA,AVGO,TSM

# Show detailed reasoning from each agent
poetry run python run_hedge_fund.py --show-reasoning

# Use a specific model
poetry run python run_hedge_fund.py --model llama3:8b

# Pick specific analysts
poetry run python run_hedge_fund.py --analysts warren_buffett,michael_burry,technical_analyst

# Telegram-friendly output (bullet lists, no markdown tables)
poetry run python run_hedge_fund.py --telegram

# Actually execute trades on Alpaca paper
poetry run python run_hedge_fund.py --execute
```

---

## CLI Reference

### `run_hedge_fund.py`

| Flag | Default | Description |
|---|---|---|
| `--execute` | off (dry run) | Place real orders on Alpaca paper |
| `--tickers X,Y,Z` | all holdings | Analyze specific tickers only |
| `--model NAME` | `llama3:8b` | Ollama model to use |
| `--analysts a,b,c` | buffett, burry, wood, mordecai, fundamentals, technicals | Comma-separated analyst list |
| `--show-reasoning` | off | Print detailed reasoning from each agent |
| `--telegram` | off | Format output for Telegram (no tables) |

### `rebalance.py`

Bulk sells positions outside your target universe. Edit `SELL_TICKERS` list in the file, then:

```bash
poetry run python rebalance.py
```

Keeps 10% of each position as a safety floor.

---

## Analyst Agents

### Built-in LLM Agents

| Agent ID | Philosophy |
|---|---|
| `warren_buffett` | Value investing, moats, margin of safety |
| `michael_burry` | Contrarian deep value, FCF analysis |
| `cathie_wood` | Disruptive innovation, exponential growth |
| `charlie_munger` | Quality companies at fair prices |
| `peter_lynch` | Growth at a reasonable price (GARP) |
| `bill_ackman` | Activist investing, concentrated positions |
| `stanley_druckenmiller` | Macro-driven, asymmetric bets |
| `ben_graham` | Deep value, net-net analysis |
| `phil_fisher` | Scuttlebutt, qualitative growth |
| `aswath_damodaran` | Rigorous DCF valuation |
| `rakesh_jhunjhunwala` | Emerging market growth |
| `mohnish_pabrai` | Dhandho framework, low risk / high uncertainty |

### Built-in Data Agents (no LLM, pure calculation)

| Agent ID | What it does |
|---|---|
| `fundamentals_analyst` | ROE, margins, growth rates, P/E, P/B |
| `technical_analyst` | Trend, mean reversion, momentum, volatility |
| `sentiment_analyst` | Market sentiment indicators |
| `growth_analyst` | Revenue acceleration, R&D, operating leverage |

### Custom Agents

Create your own agent with a unique investment philosophy. Example included: `src/agents/mordecai.py` — aggressive growth, AI infrastructure conviction, contrarian edge.

**To create a custom agent:**
1. Create `src/agents/your_agent.py` (use `mordecai.py` as template)
2. Register in `src/utils/analysts.py`
3. Use: `--analysts your_agent_name`

Full guide with code template: [PLAYBOOK.md → Custom Agent Creation](./PLAYBOOK.md#custom-agent-creation)

---

## Safety Rails

Built into `src/alpaca_integration.py`. Every trade passes through all rails before execution:

| Rail | Default | Purpose |
|---|---|---|
| Max trade size | 5% of portfolio | No single trade exceeds 5% of total portfolio value |
| Max daily trades | 5 per session | Prevents runaway trading loops |
| Min confidence | 70% | Portfolio Manager must be ≥70% confident to act |
| Min keep | 10% | Never sells entire position (always keeps ≥10%) |
| Paper only | Enforced | Hardcoded to `paper-api.alpaca.markets` — cannot hit live endpoint |
| Dry run default | On | Must explicitly pass `--execute` to place orders |

Configurable via constants at the top of `alpaca_integration.py`:
```python
MAX_TRADE_PCT = 0.05
MAX_DAILY_TRADES = 5
MIN_KEEP_PCT = 0.10
MIN_CONFIDENCE = 70
```

---

## Automation

### Cron (OpenClaw)

Production-tested cron setup for daily automated analysis:

| Job | Schedule | Purpose |
|---|---|---|
| Portfolio check | Daily 9:00 AM | Quick P/L report (all days) |
| Morning scan | Mon-Fri 6:30 AM | Pre-market multi-agent analysis |
| Midday scan | Mon-Fri 12:00 PM | Midday pulse (lighter agent set) |
| Afternoon scan | Mon-Fri 2:00 PM | Full afternoon analysis |
| Evening research | Mon-Fri 4:30 PM | Post-close deep analysis with reasoning |

Example cron setup:
```bash
openclaw cron add alpaca-portfolio \
  --cron "0 9 * * *" \
  --tz "America/Los_Angeles" \
  --exact \
  --model google/gemini-2.5-flash \
  --session isolated \
  --message "Check Alpaca portfolio. Read keys from ~/projects/ai-hedge-fund/.env. Report: total value, top 5 positions, biggest movers (>5%), daily P/L. Bullet format." \
  --announce --channel telegram --to YOUR_CHAT_ID
```

Full cron configuration guide: [PLAYBOOK.md → Automation](./PLAYBOOK.md#automation-cron)

### Heartbeat Integration

For agents using periodic wake-ups, add a portfolio check to your heartbeat config:

```markdown
### Alpaca Portfolio Check
- API: https://paper-api.alpaca.markets/v2
- Credentials: Read from projects/ai-hedge-fund/.env
- Check positions, daily P/L, total portfolio value
- Alert on big movers (>5% swing)
```

---

## Signal Format

Every analyst agent returns the same structure per ticker:

```json
{
  "signal": "bullish",
  "confidence": 82.5,
  "reasoning": "Strong fundamentals, underweight in portfolio, contrarian opportunity"
}
```

The Portfolio Manager aggregates all signals and outputs:

```json
{
  "action": "buy",
  "quantity": 10,
  "confidence": 78.0,
  "reasoning": "Consensus bullish across 4/6 agents, technical momentum confirmed"
}
```

This structured output is what makes the system agent-consumable — downstream tools can parse and act on the JSON directly.

---

## File Reference

```
ai-hedge-fund/
├── .env.example                  # Template for secrets (copy to .env)
├── .env                          # Your API keys (gitignored)
├── run_hedge_fund.py             # ⭐ Main runner — analysis + Alpaca execution
├── rebalance.py                  # Bulk sell positions outside target universe
├── PLAYBOOK.md                   # ⭐ Complete operations guide (750+ lines)
├── src/
│   ├── main.py                   # Core hedge fund engine
│   ├── alpaca_integration.py     # ⭐ Alpaca API + safety rails
│   ├── agents/
│   │   ├── mordecai.py           # Example custom agent (aggressive growth)
│   │   ├── warren_buffett.py     # Buffett agent
│   │   ├── michael_burry.py      # Burry agent
│   │   ├── cathie_wood.py        # Cathie Wood agent
│   │   ├── fundamentals.py       # Data-driven fundamentals
│   │   ├── technicals.py         # Data-driven technicals
│   │   └── ...                   # 12+ more agents
│   ├── graph/
│   │   └── state.py              # Agent state management
│   ├── llm/
│   │   ├── models.py             # Model configuration
│   │   └── ollama_models.json    # ⭐ Available Ollama models (edit this)
│   ├── tools/
│   │   └── api.py                # Financial data API client
│   └── utils/
│       ├── analysts.py           # ⭐ Agent registry (register custom agents here)
│       ├── llm.py                # LLM call helper with retries
│       └── progress.py           # Progress display
└── app/                          # Web UI (optional, see app/README.md)
```

**⭐ = Files you'll modify when setting up or customizing.**

---

## For AI Agents

This repo is built for autonomous AI agents. Here's how to use it:

1. **Read [PLAYBOOK.md](./PLAYBOOK.md)** — complete guide covering setup, Alpaca API, custom agents, automation, and troubleshooting
2. **Set up `.env`** — copy `.env.example`, add your Alpaca keys
3. **Configure models** — edit `ollama_models.json` to match your Ollama instance
4. **Run analysis** — `poetry run python run_hedge_fund.py --tickers NVDA --show-reasoning`
5. **Create your agent** — use `src/agents/mordecai.py` as a template, register in `src/utils/analysts.py`
6. **Automate** — set up cron jobs for daily pre-market analysis and portfolio monitoring
7. **Report** — use `--telegram` flag for chat-friendly output, pipe to your channel

**Key design decisions for agent compatibility:**
- All secrets in `.env` (never in source) — agents read from env vars
- All configuration via CLI flags — no interactive prompts in `run_hedge_fund.py`
- Structured JSON output from agents — parseable by downstream tools
- Safety rails are on by default — agents can't accidentally place trades without `--execute`
- Dry run is the default — safe to run analysis without consequences

---

## Known Limitations

1. **Financial data**: Free tier covers AAPL, GOOGL, MSFT, NVDA, TSLA only. Other tickers get "insufficient data" from fundamentals-dependent agents. [Get a paid key →](https://financialdatasets.ai/)
2. **ETFs**: TQQQ, SOXL, UPRO don't have company fundamentals. Technical analyst and custom agents work fine on them.
3. **Weekend orders**: Market orders placed when closed get `accepted` status and execute at next market open.
4. **Fractional shares**: System casts to `int`. Positions with <1 share may show as 0.
5. **Model quality**: `llama3:8b` is fast but analysis can be shallow. Use larger models (`qwen3.5:cloud`, `kimi-k2.5:cloud`) for important decisions.

---

## Disclaimer

**Educational and research purposes only.** Not intended for real trading or investment. No investment advice or guarantees. Past performance ≠ future results. Consult a financial advisor for real investment decisions.

## Credits

Built on [virattt/ai-hedge-fund](https://github.com/virattt/ai-hedge-fund).

## License

MIT — see [LICENSE](./LICENSE).
