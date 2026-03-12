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

```
┌─────────────────────────────────────────────────────┐
│              Alpaca Portfolio State                   │
│         (positions, cash, market values)              │
└──────────────────────┬──────────────────────────────┘
                       │
        ┌──────────────┼──────────────────┐
        ▼              ▼                  ▼
┌──────────────┐ ┌────────────┐  ┌────────────────┐
│ Data Agents  │ │ LLM Agents │  │ Market Regime  │
│              │ │            │  │                │
│ Fundamentals │ │ Buffett    │  │ SPY intraday   │
│ Technical    │ │ Burry      │  │ trend/range/   │
│ Sentiment    │ │ Wood       │  │ volatile       │
│ Growth       │ │ + 10 more  │  │ classification │
│ Valuation    │ │ + Apex     │  │                │
│ News         │ │ (day trade)│  │                │
└──────┬───────┘ └─────┬──────┘  └───────┬────────┘
       │               │                 │
       └───────────────┼─────────────────┘
                       ▼
              ┌─────────────────┐
              │ Portfolio Manager│
              │ Aggregates all  │
              │ signals, decides│
              │ buy/sell/hold   │
              └────────┬────────┘
                       ▼
              ┌─────────────────┐
              │  Safety Rails   │
              │ • Bracket orders│
              │ • Circuit break │
              │ • Daily loss cap│
              │ • Position limit│
              │  + Alpaca Exec  │
              └─────────────────┘
```

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

The system is designed to run fully autonomous via [OpenClaw](https://github.com/openclaw/openclaw) cron jobs. An AI agent (we use "Cassius" running Claude Opus) executes the full pipeline on schedule: scan → gather → analyze → trade → report.

### Prerequisites

1. **OpenClaw installed and running** — `openclaw gateway status` should show healthy
2. **An agent configured** — e.g., `cassius` with access to the swarm-trader workspace
3. **Telegram group** (optional) — for trade reports. Replace `-5217663499` with your chat ID

### Agent Setup

If you don't have a trading agent yet:

```bash
openclaw agents add cassius \
  --model "anthropic/claude-opus-4-6" \
  --workspace ~/path/to/swarm-trader
```

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

| Time (PT) | Job | Purpose |
|---|---|---|
| 9:00 AM | `swarm-portfolio-check` | Daily P/L report, portfolio health |
| 9:30 AM | `swarm-open` | Market open — scan + aggressive entries |
| 11:00 AM | `swarm-midmorning` | Re-scan, tactical adjustments |
| 1:00 PM | `swarm-lunch` | Light session, range plays |
| 3:00 PM | `swarm-late` | Final hour push, last major moves |
| 3:45 PM | `swarm-flatten` | **CRITICAL**: Close risky/short positions before close |

### Cron Setup Commands

Copy-paste these to set up the full day trading schedule. Adjust `--agent`, `--model`, `--to` (Telegram chat ID), and workspace paths for your setup.

**Daily Portfolio Check (9:00 AM, every day):**

```bash
openclaw cron add --name swarm-portfolio-check \
  --cron "0 9 * * *" \
  --tz "America/Los_Angeles" \
  --exact \
  --session isolated \
  --agent cassius \
  --model "anthropic/claude-opus-4-6" \
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
  --tz "America/Los_Angeles" \
  --exact \
  --session isolated \
  --agent cassius \
  --model "anthropic/claude-opus-4-6" \
  --announce \
  --channel telegram \
  --to "YOUR_CHAT_ID" \
  --message '🟢 Market open trading session.

1. Scan for opportunities:
   cd ~/path/to/swarm-trader && TICKERS=$(poetry run python scan_market.py --max 25)
   Echo the discovered tickers.

2. Gather intraday data:
   poetry run python gather_data.py --mode day --tickers $TICKERS --output /tmp/cassius-intraday.json

3. Read /tmp/cassius-intraday.json — 5-min bars, VWAP, RSI, volume, key levels.

4. Analyze each ticker: Price vs VWAP, RSI, volume conviction, key levels, why the scanner flagged it.

5. Write trade decisions to /tmp/cassius-trades.json (bracket orders with stop_price and take_profit required).

6. Execute: poetry run python execute_trades.py --file /tmp/cassius-trades.json

7. Post summary: scanner discoveries, trades executed, market regime, key setups.'
```

**Mid-Morning (11:00 AM, Mon-Fri):**

```bash
openclaw cron add --name swarm-midmorning \
  --cron "0 11 * * 1-5" \
  --tz "America/Los_Angeles" \
  --exact \
  --session isolated \
  --agent cassius \
  --model "anthropic/claude-opus-4-6" \
  --announce \
  --channel telegram \
  --to "YOUR_CHAT_ID" \
  --message '🔄 Mid-morning check.

1. Re-scan for new movers:
   cd ~/path/to/swarm-trader && TICKERS=$(poetry run python scan_market.py --max 25)

2. Gather fresh data:
   poetry run python gather_data.py --mode day --tickers $TICKERS --output /tmp/cassius-midmorning.json

3. Review existing positions — any stops getting hit? Any breakouts?

4. Tactical trades (momentum/mean reversion). Write to /tmp/cassius-midmorning-trades.json and execute.

5. Brief update: market direction, new scanner finds, position adjustments.'
```

**Lunch (1:00 PM, Mon-Fri):**

```bash
openclaw cron add --name swarm-lunch \
  --cron "0 13 * * 1-5" \
  --tz "America/Los_Angeles" \
  --exact \
  --session isolated \
  --agent cassius \
  --model "anthropic/claude-opus-4-6" \
  --announce \
  --channel telegram \
  --to "YOUR_CHAT_ID" \
  --message '🍽️ Lunch session.

1. Quick scan:
   cd ~/path/to/swarm-trader && TICKERS=$(poetry run python scan_market.py --max 20)

2. Gather data:
   poetry run python gather_data.py --mode day --tickers $TICKERS --output /tmp/cassius-lunch.json

3. Light trading — range plays, quick scalps if setups are clean. Reduce exposure if choppy, add if trending.'
```

**Late Afternoon (3:00 PM, Mon-Fri):**

```bash
openclaw cron add --name swarm-late \
  --cron "0 15 * * 1-5" \
  --tz "America/Los_Angeles" \
  --exact \
  --session isolated \
  --agent cassius \
  --model "anthropic/claude-opus-4-6" \
  --announce \
  --channel telegram \
  --to "YOUR_CHAT_ID" \
  --message '⏰ Late afternoon — final hour push.

1. Full scan:
   cd ~/path/to/swarm-trader && TICKERS=$(poetry run python scan_market.py --max 25)

2. Gather data:
   poetry run python gather_data.py --mode day --tickers $TICKERS --output /tmp/cassius-late.json

3. Last chance for major position changes. Plan what gets flattened at 3:45 PM vs what holds overnight.'
```

**End-of-Day Flatten (3:45 PM, Mon-Fri):**

```bash
openclaw cron add --name swarm-flatten \
  --cron "45 15 * * 1-5" \
  --tz "America/Los_Angeles" \
  --exact \
  --session isolated \
  --agent cassius \
  --model "anthropic/claude-opus-4-6" \
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

### Swing Trading Schedule (alternative)

For longer-term position trading instead of intraday:

| Time (PT) | Job | Purpose |
|---|---|---|
| 6:30 AM | Morning analysis | Pre-market multi-agent analysis |
| 9:00 AM | Portfolio check | Quick P/L report |
| 4:30 PM | Evening research | Post-close deep analysis |

Swing mode uses `gather_data.py --mode swing` (fundamentals, news, insider trades) instead of intraday technicals. See [PLAYBOOK.md](./PLAYBOOK.md) for swing cron setup.

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

## Disclaimer

Educational and research purposes only. Not investment advice. Paper trading only.

## License

MIT
