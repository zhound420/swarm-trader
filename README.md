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
- **5-min intraday bars** from Alpaca market data API
- **VWAP** — volume-weighted average price (key intraday level)
- **RSI(14)** — momentum oscillator on 5-min timeframe
- **Volume ratio** — today's volume vs 20-day average
- **Key levels** — previous day high/low, premarket high/low
- **Market regime detection** — trending, range-bound, or volatile (adapts strategy)
- **Mandatory bracket orders** — every trade gets stop loss + take profit
- **Daily circuit breaker** — stops all new entries if down 3% for the day
- **End-of-day flatten** — closes risky positions before market close

**Day trading universe** (liquid, high-volume names only):
| Category | Tickers |
|---|---|
| Mega-cap tech | NVDA, AVGO, TSM, AMD, MSFT, AAPL, META, GOOGL, AMZN |
| Momentum | PLTR, COIN, MSTR, RKLB |
| Direction/hedge | SPY, QQQ |

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
# Add your Alpaca keys and LLM provider keys to .env
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
# Gather intraday data
poetry run python gather_data.py --mode day --include-universe --output /tmp/market-data.json

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

## Automation

### Day Trading Schedule (recommended)

5 runs during market hours, Mon-Fri:

| Time (PT) | Job | Purpose |
|---|---|---|
| 9:30 AM | `swarm-open` | Market open — fresh entries, aggressive |
| 11:00 AM | `swarm-midmorning` | Position management, tactical adjustments |
| 1:00 PM | `swarm-lunch` | Light session, scalps and range plays |
| 3:00 PM | `swarm-late` | Final hour push, last major moves |
| 3:45 PM | `swarm-flatten` | **CRITICAL**: Close risky/short positions before close |

Plus a daily portfolio health check at 9:00 AM.

### Swing Trading Schedule

| Time (PT) | Job | Purpose |
|---|---|---|
| 6:30 AM | Morning analysis | Pre-market multi-agent analysis |
| 9:00 AM | Portfolio check | Quick P/L report |
| 4:30 PM | Evening research | Post-close deep analysis |

See [PLAYBOOK.md](./PLAYBOOK.md) for complete cron setup with [OpenClaw](https://github.com/openclaw/openclaw) integration.

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

## Credits

Built on [virattt/ai-hedge-fund](https://github.com/virattt/ai-hedge-fund). Extended with free data sources, day trading mode, intraday technicals, enforced risk management, short selling, and multi-provider LLM support.

## Disclaimer

Educational and research purposes only. Not investment advice. Paper trading only.

## License

MIT
