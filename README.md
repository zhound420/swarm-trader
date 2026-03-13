# Swarm Trader

Multi-agent AI trading system with **two modes**: swing trading and intraday day trading. 20 analyst agents — 13 LLM-powered investor personalities (Buffett, Munger, Burry, and more) plus 7 data/quant specialists — independently analyze stocks. A Portfolio Manager aggregates their signals to make trading decisions. Executes on Alpaca paper trading with enforced risk management.

**Multi-provider LLM support** — 13 providers: OpenAI, Anthropic, Google, DeepSeek, Groq, Ollama, xAI, OpenRouter, Azure OpenAI, GigaChat, Alibaba, Meta, Mistral.

**Zero paid data APIs required.** Hybrid data layer tries financialdatasets.ai first, falls back to SEC EDGAR + yfinance. Works fully free out of the box.

> Built on [virattt/ai-hedge-fund](https://github.com/virattt/ai-hedge-fund), extended with free data sources, multi-provider LLM support, Alpaca execution, day trading mode, custom agents, and automation.

---

## What's Different

| Feature | Upstream | Swarm Trader |
|---|---|---|
| LLM providers | Single provider | **13 providers (Ollama, OpenAI, Anthropic, Google, etc.)** |
| Financial data | financialdatasets.ai ($200/mo) | **Hybrid: financialdatasets.ai → SEC EDGAR + yfinance (free)** |
| Trade execution | Simulated only | **Alpaca paper trading — market, limit, bracket, OCO, trailing stop** |
| Trading modes | Swing only | **Swing + intraday day trading with 5-min bars, VWAP, RSI** |
| Risk management | Basic | **Mandatory brackets, circuit breaker, daily loss limit, end-of-day flatten** |
| Short selling | Not supported | **Full short selling support** |
| Analyst agents | 12 built-in | **20 agents (12 + apex + market regime + 6 data/quant)** |
| Custom agents | Not supported | **Create your own analyst agents** |
| Automation | Manual runs | **Cron-based intraday pipeline (5 runs/day)** |
| Agent-native | Interactive CLI | **Fully headless, `.env` config, structured JSON output** |
| Strategy evolution | None | **AutoResearch loop — AI agent evolves strategy.py overnight, backtests every mutation** |

---

## Trading Modes

### Swing Mode (default)

Traditional multi-agent analysis using fundamentals, news, insider trades, and technical indicators. Runs 1-2x daily. Best for position trading over days/weeks.

```bash
poetry run python gather_data.py --mode swing
```

### Day Trading Mode

Intraday technical analysis using 5-minute bars, VWAP, RSI, volume profiles, and market regime classification. Designed for multiple runs during market hours with disciplined risk management.

```bash
poetry run python gather_data.py --mode day
```

**Day trading features:**
- **Dynamic market scanner** — discovers today's movers, most active stocks, and high-volume opportunities before each session (no static ticker list)
- **5-min intraday bars** from Alpaca market data API
- **VWAP** — volume-weighted average price (key intraday level)
- **RSI(14)** — momentum oscillator on 5-min timeframe
- **Volume ratio** — today's volume vs 20-day average
- **Key levels** — previous day high/low, premarket high/low
- **Market regime detection** — trending, range-bound, or volatile (adapts strategy)
- **Mandatory bracket orders** — every trade gets stop loss + take profit
- **Daily circuit breaker** — stops all new entries if down 3% for the day
- **End-of-day flatten** — closes risky positions before market close

**Dynamic ticker discovery:** The market scanner (`scan_market.py`) runs before each trading session. It pulls top gainers/losers, most active stocks by volume, and filters for day-trading quality (min $10 price, no penny stocks, no warrants, no leveraged ETFs). Results are merged with a core watchlist of liquid mega-caps:

| Source | What it finds |
|---|---|
| Core watchlist | NVDA, AVGO, TSM, AMD, MSFT, AAPL, META, GOOGL, AMZN, SPY, QQQ |
| Scanner: movers | Today's biggest gainers and losers (filtered for quality) |
| Scanner: most active | Highest trade count / volume stocks of the day |

The scanner typically surfaces 15-25 tickers per session — a mix of names you always watch plus whatever's hot today (earnings movers, gap plays, unusual volume).

---

## Architecture

![Swarm Trader Architecture](docs/architecture.png)

---

## AutoResearch — Strategy Evolution Lab

> Adapted from [karpathy/autoresearch](https://github.com/karpathy/autoresearch) — Andrej Karpathy's autonomous AI research framework that went viral in March 2026 (~30k GitHub stars in one week). We took the same loop and applied it to trading strategy evolution instead of ML model training.

**The original:** An AI agent modifies `train.py`, runs a 5-minute GPU training run, measures `val_bpb` (validation loss), keeps the change if it improves, reverts if it doesn't. Repeat 100 times overnight. Karpathy's system found an 11% efficiency improvement in GPT-2 training across ~700 autonomous experiments.

**Our adaptation:** The agent modifies `strategy.py` (pure-Python — indicators, thresholds, signal logic), runs a fast backtest against cached Alpaca 5-min bars, measures a composite fitness score, keeps or reverts. Same idea, different domain.

| | Karpathy's autoresearch | Swarm Trader AutoResearch |
|---|---|---|
| What evolves | `train.py` (PyTorch LLM training) | `strategy.py` (intraday trading rules) |
| Evaluation metric | `val_bpb` (validation loss) | Fitness = Sharpe×0.35 + Sortino×0.25 + Return%×0.20 + WinRate×0.10 + ProfitFactor×0.10 |
| Evaluation budget | 5-min GPU training run | Fast backtest on cached 5-min bars (~30s) |
| Research instructions | `program.md` | `program.md` |
| Experiment log | JSONL with diffs and metrics | `experiments/log.jsonl` (same format) |
| Agent | Claude Code (claude CLI) | Claude Code (claude CLI, pinned to Sonnet) or Anthropic SDK |
| Overnight yield | ~100 experiments | ~25–50 iterations (configurable) |

### How It Works

```
program.md  (research instructions — human writes this)
    ↓
evolve.py   (orchestrator — spawns agent, runs backtest, keeps/reverts)
    ↓
strategy.py (pure-Python strategy — the file being evolved, NO LLM calls)
    ↓
backtest_fast.py (fitness function — cached 5-min bars, deterministic, fast)
    ↓
experiments/log.jsonl (full experiment history with diffs and metrics)
```

The agent reads `program.md` + recent experiment history + the current `strategy.py`, forms a hypothesis, edits the file, and hands control back to `evolve.py`. The backtester runs it. If fitness improves, the change is committed. If not, it's reverted and the failure is logged. One iteration takes ~1–2 minutes. Run 25 overnight, wake up to a better strategy.

### Fitness Score

```
fitness = (sharpe × 0.35) + (sortino × 0.25) + (return% × 0.20) + (win_rate × 0.10) + (profit_factor × 0.10)
```

Penalties for: drawdown > 15%, win rate < 30%, too few trades (< 10), overtrading (> 200).

### Quick Start

```bash
# 1. Cache historical data (one-time, ~2 min)
poetry run python autoresearch/backtest_fast.py --days 10

# 2. Run the baseline backtest
poetry run python autoresearch/backtest_fast.py

# 3. Kick off autonomous evolution (25 iterations)
poetry run python autoresearch/evolve.py --iterations 25

# 4. Review per-run trend (after first run completes)
poetry run python autoresearch/analyze.py

# 5. Or drill into raw experiment log
cat autoresearch/experiments/log.jsonl | python3 -m json.tool

# 6. Or let it run automatically via cron (see Automation section)
# autoresearch-evolve cron runs at 5:00 PM PT Mon-Fri after market close
```

### Files

| File | Role | Modified by |
|------|------|-------------|
| `autoresearch/strategy.py` | Pure-Python strategy (indicators, params, signal rules) | Agent |
| `autoresearch/backtest_fast.py` | Deterministic backtester, fitness scorer | Nobody |
| `autoresearch/evolve.py` | Evolution loop orchestrator | Nobody |
| `autoresearch/analyze.py` | Cross-run analytics — run history, fitness trend, hypothesis frequency | Nobody |
| `autoresearch/program.md` | Agent instructions | Human |
| `autoresearch/experiments/log.jsonl` | Full experiment history (one record per experiment) | System |
| `autoresearch/experiments/runs.jsonl` | Per-session run summaries (one record per evolve.py run) | System |

### From Research to Production

AutoResearch and the live trading system are **intentionally decoupled**. There is no automatic bridge — this is a safety feature.

```
AutoResearch (offline)          Live Trading (your agent)
─────────────────────          ─────────────────────────
autoresearch/strategy.py        src/agents/apex.py
Pure Python, no LLM             LLM-based (Opus)
Backtests cached data           Trades real money (paper)
Mutations every iteration       Stable config
         │                              ▲
         │    Human review gate         │
         └──────────────────────────────┘
```

**How findings flow to production:**

1. Review `experiments/log.jsonl` — see what was tried, what improved fitness
2. Identify the winning changes (lower RSI threshold? new indicator? tighter stops?)
3. Decide if the finding is real alpha or overfitting to the backtest window
4. Manually update `src/config.py` (parameters) or `src/agents/apex.py` (prompt/logic)
5. Monitor your agent's live performance after the change

**Why no auto-bridge?** Backtesting ≠ live trading. Slippage, liquidity, regime changes, and overfitting mean a strategy that backtests well can still lose money live. The human gate ensures someone with judgment reviews before changes hit production.

---

## Data Sources

Hybrid data layer: tries financialdatasets.ai first (if API key is set), falls back to SEC EDGAR + yfinance on failure or empty results. No paid API keys required for data — works fully free out of the box.

| Data Type | Source | Mode | Cache TTL |
|---|---|---|---|
| Prices (OHLCV) | yfinance | swing | 15 min |
| 5-min intraday bars | Alpaca market data API | day | none (live) |
| VWAP / RSI / volume | Calculated from bars | day | none |
| Financial metrics | yfinance + SEC EDGAR XBRL | swing | 24 hrs |
| Financial statements | SEC EDGAR XBRL | swing | 24 hrs |
| Insider trades | SEC EDGAR Form 4 | swing | 7 days |
| Company news | yfinance news feed | both | 15 min |
| Market cap / company info | yfinance + SEC EDGAR | swing | 24 hrs |

---

## Quick Start

### Prerequisites

- Python 3.11+
- [Poetry](https://python-poetry.org/)
- [Alpaca](https://app.alpaca.markets) paper trading account (free)
- At least one LLM provider configured

### Setup

```bash
git clone https://github.com/zhound420/swarm-trader.git
cd swarm-trader
poetry install

cp .env.example .env
```

Edit `.env` with your credentials:

```bash
# Required — Alpaca paper trading
ALPACA_API_KEY=your_key_here
ALPACA_API_SECRET=your_secret_here

# LLM provider (at least one required)
OPENAI_API_KEY=              # GPT-4, etc.
ANTHROPIC_API_KEY=           # Claude
GOOGLE_API_KEY=              # Gemini
GROQ_API_KEY=                # Groq cloud
DEEPSEEK_API_KEY=            # DeepSeek

# Optional — paid data (free layer works without this)
FINANCIAL_DATASETS_API_KEY=
```

### Verify Data Layer

```bash
python test_data.py --ticker NVDA
```

### Run (Swing Mode)

```bash
# Dry run — analyze holdings, show what trades would happen
poetry run python run_hedge_fund.py

# Analyze specific tickers with reasoning
poetry run python run_hedge_fund.py --tickers NVDA,AVGO,TSM --show-reasoning

# Execute trades on Alpaca paper
poetry run python run_hedge_fund.py --execute
```

### Run (Day Trading Mode)

```bash
# Step 1: Scan for today's opportunities
TICKERS=$(poetry run python scan_market.py --max 25)
echo "Trading: $TICKERS"

# Step 2: Gather intraday data for scanned tickers
poetry run python gather_data.py --mode day --tickers $TICKERS --output /tmp/market-data.json

# Or scan + gather in one pipeline:
poetry run python gather_data.py --mode day --tickers $(poetry run python scan_market.py) --output /tmp/data.json

# Execute from agent decisions
echo '{"trades":[{"ticker":"NVDA","action":"buy","qty":10,"stop_price":180,"take_profit":190,"reasoning":"VWAP bounce"}]}' | \
  poetry run python execute_trades.py

# Flatten all positions (end of day)
poetry run python execute_trades.py --flatten

# Short selling
echo '{"trades":[{"ticker":"MSTR","action":"short","qty":20,"stop_price":142,"take_profit":130,"reasoning":"rejection at resistance"}]}' | \
  poetry run python execute_trades.py
```

---

## Safety Rails

### Swing Mode

| Rail | Value | Purpose |
|---|---|---|
| Max trade size | 10% of portfolio | No single trade too large |
| Max daily trades | 8 per session | Prevents runaway loops |
| Min confidence | 60% | Must be confident to act |
| Min keep | 5% | Never sells entire position |
| Paper only | Enforced | Hardcoded to paper endpoint |
| Dry run default | On | Must pass `--execute` to trade |

### Day Trading Mode

| Rail | Value | Purpose |
|---|---|---|
| Max trade size | 15% of portfolio | Allows bigger intraday positions |
| Max daily trades | 20 per session | Day trading needs more activity |
| Min confidence | 55% | More opportunities, lower threshold |
| Max risk per trade | 2% of portfolio | Defined risk on every entry |
| Max portfolio heat | 10% | Total capital at risk at any time |
| Max position size | 15% | No single name > 15% of portfolio |
| **Daily circuit breaker** | **3% loss** | **Stops all new entries if down 3% today** |
| **Mandatory brackets** | **Every trade** | **Auto-calculates stop + target if not provided** |
| **End-of-day flatten** | **3:45 PM ET** | **Closes speculative/short positions before close** |
| Short selling | Enabled | Make money in both directions |
| Paper only | Enforced | Hardcoded to paper endpoint |

---

## Analyst Agents

### LLM Agents (13)

12 legendary investor personalities + 1 custom day trader:

| Agent | Philosophy |
|---|---|
| `warren_buffett` | Value investing, moats, margin of safety |
| `charlie_munger` | Quality at fair prices |
| `michael_burry` | Contrarian deep value, FCF |
| `cathie_wood` | Disruptive innovation |
| `peter_lynch` | Growth at reasonable price |
| `bill_ackman` | Activist, concentrated positions |
| `stanley_druckenmiller` | Macro, asymmetric bets |
| `ben_graham` | Deep value, net-nets |
| `phil_fisher` | Qualitative growth |
| `aswath_damodaran` | DCF valuation |
| `rakesh_jhunjhunwala` | Emerging market growth |
| `mohnish_pabrai` | Dhandho framework |
| `apex` | **Intraday day trader** — VWAP, RSI, momentum, key levels, market regime |

### Data/Quant Agents (7)

| Agent | What it does |
|---|---|
| `fundamentals_analyst` | ROE, margins, P/E, P/B |
| `technical_analyst` | Trend, momentum, volatility |
| `sentiment_analyst` | Market sentiment |
| `growth_analyst` | Revenue acceleration, R&D |
| `valuation_analyst` | Fair value models |
| `news_sentiment_analyst` | News-driven sentiment signals |
| `market_regime` | **Classifies market conditions: trending up/down, range-bound, volatile** |

### Custom Agents

Create your own — see `src/agents/apex.py` as a template. Register in `src/utils/analysts.py`. Full guide in [PLAYBOOK.md](./PLAYBOOK.md).

---

## Order Types

### Execution Methods & Safety Rails

![Execution Methods & Safety Rails](docs/execution-methods.png)

The Portfolio Manager decides on trade actions (buy, sell, short, cover) and emits order types to Alpaca. These are automatically routed through a series of **safety rails** to prevent unintended risks.

| Type | When to use | Key fields |
|---|---|---|
| `market` | Default — fill immediately | — |
| `limit` | Enter/exit at a specific price | `limit_price` |
| `bracket` | Entry with automatic stop-loss + take-profit | `stop_price`, `take_profit` |
| `stop` | Standalone stop-loss on existing position | `stop_price` |
| `oco` | Exit-only: stop + take-profit on existing position | `stop_price`, `take_profit` |
| `trailing_stop` | Stop that rises with price to lock in gains | `trail_percent` |

In day trading mode, **bracket orders are mandatory**. If a trade is submitted without `stop_price`, the system auto-calculates a 2% stop loss and 2:1 reward target.

---

## Automation with OpenClaw

The system is designed to run fully autonomous via [OpenClaw](https://github.com/openclaw/openclaw) cron jobs. An AI agent executes the full pipeline on schedule: scan → gather → analyze → trade → report.

### Prerequisites

1. **OpenClaw installed and running** — `openclaw gateway status` should show healthy
2. **An agent configured** — e.g., `my-agent` with access to the swarm-trader workspace
3. **Telegram group** (optional) — for trade reports. Replace `-5217663499` with your chat ID

### Agent Setup

If you don't have a trading agent yet:

```bash
openclaw agents add my-agent \
  --model "your-preferred-model" \
  --workspace ~/path/to/swarm-trader
```

**Model choice:** Any model that can follow multi-step instructions and output structured JSON works. The data gathering and execution are deterministic Python scripts — the LLM only handles analysis and trade decisions. Stronger models produce better trade analysis, but mid-tier models handle the pipeline fine for most use cases.

**Frontier models** (best analysis, higher cost):

| Model | Provider | Input/Output per 1M tokens | Context | Notes |
|---|---|---|---|---|
| GPT-5.4 | OpenAI | $2.50 / $15.00 | 1M | Tied #1 on Intelligence Index (57). Latest and greatest |
| Gemini 3.1 Pro | Google | ~$1.25 / $10.00 | 1M | Tied #1. Excellent multimodal + long context |
| Gemini 3 Pro | Google | $1.25 / $10.00 | 1M | Near-frontier, great value for the price |
| Claude Opus 4.6 | Anthropic | $15.00 / $75.00 | 200K | Best instruction-following, adaptive reasoning |
| GPT-5.2 | OpenAI | $2.00 / $8.00 | 128K | Strong all-rounder, cheaper than 5.4 |
| Grok 3 | xAI | $3.00 / $15.00 | 131K | Solid reasoning and coding |

**Cost-effective** (strong performance, lower cost):

| Model | Provider | Input/Output per 1M tokens | Context | Notes |
|---|---|---|---|---|
| Claude Sonnet 4.6 | Anthropic | $3.00 / $15.00 | 200K | 95% of Opus quality at 1/5 the price |
| DeepSeek V3.2 | DeepSeek | $0.28 / $0.42 | 130K | Absurdly cheap, very strong (685B params) |
| Kimi K2.5 | Moonshot | Free tier available | 262K | 1T params, competitive with frontier |
| Qwen 3.5 | Alibaba | Varies by host | 262K | 397B MoE, excellent reasoning |
| MiniMax M2.5 | MiniMax | $0.30 / $1.20 | 205K | Good SWE-bench scores, cheap |
| Step-3.5-Flash | Stepfun | $0.10 / $0.30 | 256K | Ultra-cheap, decent quality |

**Open-weights / self-hosted** (no API cost, runs on your hardware):

| Model | Params | Notes |
|---|---|---|
| GLM-5 | 744B | Top open-weights model (Intelligence Index 50) |
| Kimi K2.5 | 1T | Near-frontier, open weights |
| DeepSeek V3.2 | 685B | Also available as API (see above) |
| Qwen 3.5 | 397B (17B active) | MoE = big-model quality, smaller inference cost |
| Llama 4 Maverick | 400B | Meta's latest, 1M context window |
| QwQ-32B | 32B | Compact reasoning beast — runs on consumer hardware |
| Nemotron Ultra | 253B | Nvidia's open model, strong benchmarks |

**Our recommendation:** For day trading, use the best model you can afford — trade decisions are where model quality matters most. DeepSeek V3.2 at $0.28/M input is an incredible value pick. GPT-5.4 or Gemini 3.1 Pro if you want the absolute best. Claude Sonnet 4.6 is a great middle ground.

### Pipeline Flow

Each cron job follows this pipeline:

```
scan_market.py          →  Discover today's movers (dynamic tickers)
gather_data.py --mode day  →  Fetch 5-min bars, VWAP, RSI, volume for each ticker
[Agent analyzes]        →  LLM reads data, decides trades with stop/target prices
execute_trades.py       →  Places bracket orders on Alpaca with safety rails
[Agent reports]         →  Posts summary to Telegram/Discord
```

### Day Trading Schedule (recommended)

5 runs during market hours, Mon-Fri:

| Time (ET) | Job | Purpose |
|---|---|---|
| 9:00 AM | `swarm-portfolio-check` | Daily P/L report, portfolio health |
| 9:30 AM | `swarm-open` | Market open — scan + aggressive entries |
| 11:00 AM | `swarm-midmorning` | Re-scan, tactical adjustments |
| 1:00 PM | `swarm-lunch` | Light session, range plays |
| 3:00 PM | `swarm-late` | Final hour push, last major moves |
| 3:45 PM | `swarm-flatten` | **CRITICAL**: Close risky/short positions before close |
| 5:00 PM | `autoresearch-run` | Overnight strategy evolution (50 iterations) |
| 8:00 PM | `autoresearch-report` | Post evolution results to Telegram |

### Cron Setup Commands

Copy-paste these to set up the full day trading schedule. Adjust `--agent`, `--model`, `--to` (Telegram chat ID), and workspace paths for your setup.

**Daily Portfolio Check (9:00 AM, every day):**

```bash
openclaw cron add --name swarm-portfolio-check \
  --cron "0 9 * * *" \
  --tz "America/New_York" \
  --exact \
  --session isolated \
  --agent my-agent \
  --model "your-preferred-model" \
  --announce \
  --channel telegram \
  --to "YOUR_CHAT_ID" \
  --message "Daily portfolio health check.

Run: cd ~/path/to/swarm-trader && poetry run python check_portfolio.py

Report: total equity, daily P&L, top positions, any big movers (>5% swing)."
```

**Market Open (9:30 AM, Mon-Fri):**

```bash
openclaw cron add --name swarm-open \
  --cron "30 9 * * 1-5" \
  --tz "America/New_York" \
  --exact \
  --session isolated \
  --agent my-agent \
  --model "your-preferred-model" \
  --announce \
  --channel telegram \
  --to "YOUR_CHAT_ID" \
  --message '🟢 Market open trading session.

1. Scan for opportunities:
   cd ~/path/to/swarm-trader && TICKERS=$(poetry run python scan_market.py --max 25)
   Echo the discovered tickers.

2. Gather intraday data:
   poetry run python gather_data.py --mode day --tickers $TICKERS --output /tmp/swarm-intraday.json

3. Read /tmp/swarm-intraday.json — 5-min bars, VWAP, RSI, volume, key levels.

4. Analyze each ticker: Price vs VWAP, RSI, volume conviction, key levels, why the scanner flagged it.

5. Write trade decisions to /tmp/swarm-trades.json (bracket orders with stop_price and take_profit required).

6. Execute: poetry run python execute_trades.py --file /tmp/swarm-trades.json

7. Post summary: scanner discoveries, trades executed, market regime, key setups.'
```

**Mid-Morning (11:00 AM, Mon-Fri):**

```bash
openclaw cron add --name swarm-midmorning \
  --cron "0 11 * * 1-5" \
  --tz "America/New_York" \
  --exact \
  --session isolated \
  --agent my-agent \
  --model "your-preferred-model" \
  --announce \
  --channel telegram \
  --to "YOUR_CHAT_ID" \
  --message '🔄 Mid-morning check.

1. Re-scan for new movers:
   cd ~/path/to/swarm-trader && TICKERS=$(poetry run python scan_market.py --max 25)

2. Gather fresh data:
   poetry run python gather_data.py --mode day --tickers $TICKERS --output /tmp/swarm-midmorning.json

3. Review existing positions — any stops getting hit? Any breakouts?

4. Tactical trades (momentum/mean reversion). Write to /tmp/swarm-midmorning-trades.json and execute.

5. Brief update: market direction, new scanner finds, position adjustments.'
```

**Lunch (1:00 PM, Mon-Fri):**

```bash
openclaw cron add --name swarm-lunch \
  --cron "0 13 * * 1-5" \
  --tz "America/New_York" \
  --exact \
  --session isolated \
  --agent my-agent \
  --model "your-preferred-model" \
  --announce \
  --channel telegram \
  --to "YOUR_CHAT_ID" \
  --message '🍽️ Lunch session.

1. Quick scan:
   cd ~/path/to/swarm-trader && TICKERS=$(poetry run python scan_market.py --max 20)

2. Gather data:
   poetry run python gather_data.py --mode day --tickers $TICKERS --output /tmp/swarm-lunch.json

3. Light trading — range plays, quick scalps if setups are clean. Reduce exposure if choppy, add if trending.'
```

**Late Afternoon (3:00 PM, Mon-Fri):**

```bash
openclaw cron add --name swarm-late \
  --cron "0 15 * * 1-5" \
  --tz "America/New_York" \
  --exact \
  --session isolated \
  --agent my-agent \
  --model "your-preferred-model" \
  --announce \
  --channel telegram \
  --to "YOUR_CHAT_ID" \
  --message '⏰ Late afternoon — final hour push.

1. Full scan:
   cd ~/path/to/swarm-trader && TICKERS=$(poetry run python scan_market.py --max 25)

2. Gather data:
   poetry run python gather_data.py --mode day --tickers $TICKERS --output /tmp/swarm-late.json

3. Last chance for major position changes. Plan what gets flattened at 3:45 PM vs what holds overnight.'
```

**End-of-Day Flatten (3:45 PM, Mon-Fri):**

```bash
openclaw cron add --name swarm-flatten \
  --cron "45 15 * * 1-5" \
  --tz "America/New_York" \
  --exact \
  --session isolated \
  --agent my-agent \
  --model "your-preferred-model" \
  --announce \
  --channel telegram \
  --to "YOUR_CHAT_ID" \
  --message '🔴 FLATTEN RISKY POSITIONS — 15 minutes to close.

1. Review all open positions:
   cd ~/path/to/swarm-trader && poetry run python check_portfolio.py

2. Flatten anything speculative, leveraged, or short:
   - Any short positions (cover by close)
   - Any positions >10% of portfolio
   - Any momentum/crypto proxies

3. Execute flattening:
   poetry run python execute_trades.py --flatten

4. Report: positions flattened, overnight holdings, cash raised, daily P&L.

NO EXCEPTIONS. Risk management > profit.'
```

**AutoResearch — Why two crons?**

The evolution loop has two distinct jobs that warrant different models:

| Layer | Job | Model | Why |
|---|---|---|---|
| Orchestrator | Launch `evolve.py`, wait 3 hours | `google/gemini-2.5-flash` | Just runs a shell command — no reasoning needed, use cheapest model |
| Inner loop | Strategy mutation (per iteration) | `claude-sonnet-4-20250514` (pinned in `evolve.py`) | Needs real reasoning to read experiments, form hypothesis, edit code |
| Reporter | Read log, format Telegram summary | `google/gemini-2.5-flash` | Read-and-summarize, no reasoning needed |

Using a powerful model as the outer orchestrator is pure waste — it just calls `subprocess.run()` and waits. Flash handles that fine at ~10x lower cost. Sonnet is reserved for the inner strategy mutation loop where reasoning actually matters (and is pinned via `--model` in `evolve.py` to prevent accidental Opus usage).

**AutoResearch Run (5:00 PM, Mon-Fri — worker):**

```bash
openclaw cron add --name autoresearch-run \
  --cron "0 17 * * 1-5" \
  --tz "America/New_York" \
  --exact \
  --session isolated \
  --agent my-agent \
  --model "google/gemini-2.5-flash" \
  --timeout 10800 \
  --message "Run the autoresearch evolution loop (no announcement — report job handles that).

cd ~/path/to/swarm-trader && poetry run python autoresearch/backtest_fast.py --days 10 && poetry run python autoresearch/evolve.py --iterations 50 --backtest-days 10 --agent claude 2>&1 | tee /tmp/autoresearch-latest.log"
```

**AutoResearch Report (8:00 PM, Mon-Fri — reporter):**

```bash
openclaw cron add --name autoresearch-report \
  --cron "0 20 * * 1-5" \
  --tz "America/New_York" \
  --exact \
  --session isolated \
  --agent my-agent \
  --model "google/gemini-2.5-flash" \
  --timeout 120 \
  --announce \
  --channel telegram \
  --to "YOUR_CHAT_ID" \
  --message "Read /tmp/autoresearch-latest.log and post the autoresearch results.

Summarize: how many experiments ran, best fitness achieved, what changes were kept, and the top 3 findings."
```

The 3-hour timeout on the run job covers 50 iterations at ~1-2 min each (with slack). The report job fires at 8 PM after evolution is done.

### Swing Trading Schedule (alternative)

For longer-term position trading instead of intraday:

| Time (ET) | Job | Purpose |
|---|---|---|
| 6:30 AM | Morning analysis | Pre-market multi-agent analysis |
| 9:00 AM | Portfolio check | Quick P/L report |
| 4:30 PM | Evening research | Post-close deep analysis |
| 5:00 PM | `autoresearch-run-swing` | Swing strategy evolution (50 iterations, `--mode swing`) |
| 8:00 PM | `autoresearch-report-swing` | Post swing evolution results to Telegram |

Swing mode uses `gather_data.py --mode swing` (fundamentals, news, insider trades) instead of intraday technicals. See [PLAYBOOK.md](./PLAYBOOK.md) for swing cron setup.

**AutoResearch swing evolution crons (same two-cron pattern):**

```bash
openclaw cron add --name autoresearch-run-swing \
  --cron "0 17 * * 1-5" \
  --tz "America/New_York" \
  --exact \
  --session isolated \
  --agent my-agent \
  --model "google/gemini-2.5-flash" \
  --timeout 10800 \
  --message "Run the autoresearch swing evolution loop (no announcement — report job handles that).

cd ~/path/to/swarm-trader && poetry run python autoresearch/backtest_fast.py --days 30 && poetry run python autoresearch/evolve.py --mode swing --iterations 50 --backtest-days 30 --agent claude 2>&1 | tee /tmp/autoresearch-swing-latest.log"

openclaw cron add --name autoresearch-report-swing \
  --cron "0 20 * * 1-5" \
  --tz "America/New_York" \
  --exact \
  --session isolated \
  --agent my-agent \
  --model "google/gemini-2.5-flash" \
  --timeout 120 \
  --announce \
  --channel telegram \
  --to "YOUR_CHAT_ID" \
  --message "Read /tmp/autoresearch-swing-latest.log and post the swing autoresearch results.

Summarize: how many experiments ran, best fitness achieved, what swing strategy changes were kept (MA periods, stop %, trend thresholds), and the top 3 findings."
```

### Managing Crons

```bash
openclaw cron list                        # See all jobs + next run times
openclaw cron run swarm-open              # Trigger a job manually (debug)
openclaw cron disable swarm-flatten       # Pause a job
openclaw cron enable swarm-flatten        # Resume
openclaw cron rm <job-id>                 # Delete a job
openclaw cron runs swarm-open --limit 5   # View recent run history
```

### Monitoring

Check if the system is healthy:

```bash
# Are crons running?
openclaw cron list | grep swarm

# Recent trade activity
cat data/trade_journal.jsonl | tail -5

# Check for circuit breaker
poetry run python execute_trades.py --dry-run < /dev/null

# Alpaca account status
curl -s "https://paper-api.alpaca.markets/v2/account" \
  -H "APCA-API-KEY-ID: $ALPACA_API_KEY" \
  -H "APCA-API-SECRET-KEY: $ALPACA_API_SECRET" | python3 -m json.tool
```

---

## Configuration

### Risk Parameters (`src/config.py`)

All risk parameters are in one place. Adjust to your risk tolerance:

```python
# Day trading risk params
MAX_RISK_PER_TRADE = 0.02       # 2% of portfolio risked per trade
MAX_PORTFOLIO_HEAT = 0.10       # Max 10% of portfolio at risk simultaneously
MAX_POSITION_SIZE = 0.15        # No single position > 15% of portfolio
DEFAULT_STOP_PCT = 0.02         # 2% stop loss (auto-applied if agent doesn't specify)
DEFAULT_TARGET_MULTIPLIER = 2.0 # 2:1 reward-to-risk ratio
FLATTEN_BY = '15:45'            # Flatten risky positions by 3:45 PM ET
```

### Safety Rails (`src/alpaca_integration.py`)

```python
MAX_TRADE_PCT = 0.15    # Max single trade = 15% of portfolio
MAX_DAILY_TRADES = 20   # Max trades per session
MIN_CONFIDENCE = 55     # Min confidence % to execute
MAX_LOSS_PER_DAY = 0.03 # Circuit breaker: stop at 3% daily loss
```

### Scanner Filters (`scan_market.py`)

```python
CORE_WATCHLIST = ["NVDA", "AVGO", ...]  # Always-watched tickers
EXCLUDE = {"TQQQ", "SQQQ", ...}        # Never trade these
DEFAULT_MIN_PRICE = 10.0                # Skip penny stocks
DEFAULT_MIN_TRADES = 5000               # Min trade count for "most active"
DEFAULT_MAX_TICKERS = 25                # Max tickers per scan
```

### Swing Universe (`src/config.py`)

The swing trading universe is separate from day trading:

```python
SWING_UNIVERSE = {
    "ai_infra":       {"tickers": ["NVDA", "AVGO", "SMCI", "TSM"], "target_pct": 0.40},
    "leveraged_etfs": {"tickers": ["TQQQ", "SOXL", "UPRO"],       "target_pct": 0.25},
    "momentum":       {"tickers": ["PLTR", "MSTR", "COIN", "RKLB"],"target_pct": 0.20},
    "moonshots":      {"tickers": ["IONQ", "RGTI", "SOUN", "LUNR"],"target_pct": 0.15},
}
```

---

## CLI Flags

### `run_hedge_fund.py`

| Flag | Default | Description |
|---|---|---|
| `--execute` | off (dry run) | Place real orders on Alpaca paper |
| `--tickers X,Y,Z` | all holdings | Analyze specific tickers |
| `--model NAME` | from config | LLM model to use |
| `--analysts a,b,c` | default set | Comma-separated analyst list |
| `--show-reasoning` | off | Print detailed reasoning |
| `--telegram` | off | Chat-friendly output |

### `gather_data.py`

| Flag | Default | Description |
|---|---|---|
| `--mode swing\|day` | swing | Data gathering mode |
| `--tickers X,Y,Z` | all holdings | Specific tickers |
| `--top N` | all | Top N positions by value |
| `--include-universe` | off | Include full universe |
| `--output PATH` | stdout | Write JSON to file |

### `scan_market.py`

| Flag | Default | Description |
|---|---|---|
| `--json` | off | Full JSON output with metadata |
| `--max N` | 25 | Max total tickers |
| `--min-price N` | 10.0 | Min stock price (filters penny stocks) |
| `--min-trades N` | 5000 | Min trade count for "most active" |
| `--no-core` | off | Skip core watchlist, only discovered |

### `execute_trades.py`

| Flag | Default | Description |
|---|---|---|
| `--file PATH` | stdin | Read decisions from file |
| `--dry-run` | off | Simulate without placing orders |
| `--flatten` | off | Market-sell all positions |

---

## Project Structure

```
swarm-trader/
├── run_hedge_fund.py          # Main runner — analysis + execution
├── scan_market.py             # Dynamic market scanner (movers + most active)
├── gather_data.py             # Market data gatherer (swing + day modes)
├── execute_trades.py          # Trade executor with bracket enforcement
├── check_portfolio.py         # Quick Alpaca portfolio check
├── rebalance.py               # Bulk sell positions outside universe
├── test_data.py               # Data layer smoke test
├── PLAYBOOK.md                # Complete operations guide
├── .env.example               # Secret template
├── src/
│   ├── config.py              # Universe definitions + risk parameters
│   ├── tools/
│   │   ├── api.py             # Hybrid dispatcher (paid → free fallback)
│   │   ├── api_free.py        # Free data layer (SEC EDGAR + yfinance)
│   │   └── api_original.py    # Original financialdatasets.ai client
│   ├── agents/
│   │   ├── apex.py            # Intraday day trading agent
│   │   ├── market_regime.py   # Market regime classifier
│   │   └── ...                # 12 LLM + 6 data/quant agents
│   ├── alpaca_integration.py  # Alpaca API + safety rails + circuit breaker
│   └── llm/
│       ├── models.py          # 13 LLM provider definitions
│       ├── api_models.json    # API provider model catalog
│       └── ollama_models.json # Local Ollama model catalog
└── .cache/                    # Disk cache (gitignored)
```

---

## Troubleshooting

| Problem | Cause | Fix |
|---|---|---|
| `Circuit breaker ACTIVE` | Down 3%+ today | Normal — system protects capital. Resets next trading day |
| `ALPACA_API_KEY not set` | Missing `.env` | Copy `.env.example` to `.env` and add your keys |
| `insufficient qty` on sell | Shares locked by open orders | `cancel_all_orders()` or cancel via Alpaca dashboard |
| Scanner returns only core tickers | Market is closed or Alpaca screener down | Scanner works during market hours; pre-market data is limited |
| `Model not found` with Ollama | Model name mismatch | Run `ollama list` and update `ollama_models.json` to match |
| Pydantic V1 warning | Python 3.14 compatibility | Harmless warning, everything still works |
| Bracket order rejected | Stop/target too close to current price | Widen stop/target or use market orders |
| `flatten` sells nothing | No positions to flatten | Expected if already flat or market is closed |

---

## Credits

Built on [virattt/ai-hedge-fund](https://github.com/virattt/ai-hedge-fund). Extended with free data sources, day trading mode, intraday technicals, enforced risk management, short selling, and multi-provider LLM support.

AutoResearch loop adapted from [karpathy/autoresearch](https://github.com/karpathy/autoresearch) by Andrej Karpathy. The original framework autonomously evolves ML training code using a fixed compute budget; we adapted the same pattern to evolve trading strategies using a fixed backtest budget. Core idea, architecture, and `program.md` convention are his — we ported it to the trading domain.

## Disclaimer

Educational and research purposes only. Not investment advice. Paper trading only.

## License

MIT
