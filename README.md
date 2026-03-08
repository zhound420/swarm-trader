# Swarm Trader

Multi-agent AI trading system. LLM-powered analyst agents (Buffett, Munger, Burry, Cathie Wood, and more) independently analyze stocks, then a Portfolio Manager aggregates their signals to make trading decisions. Executes on Alpaca paper trading.

**Zero paid data APIs required.** All financial data sourced from SEC EDGAR + yfinance.

> Built on [virattt/ai-hedge-fund](https://github.com/virattt/ai-hedge-fund), extended with free data sources, Alpaca execution, custom agents, and automation.

---

## What's Different

| Feature | Upstream (ai-hedge-fund) | Swarm Trader |
|---|---|---|
| Financial data | financialdatasets.ai ($200/mo) | **SEC EDGAR + yfinance (free)** |
| Trade execution | Simulated only | **Alpaca paper trading** |
| Custom agents | Not supported | **Create your own analyst agents** |
| Automation | Manual runs | **Cron-based daily pipeline** |
| Agent-native | Interactive CLI | **Fully headless, `.env` config, structured JSON output** |

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
│ Fundamentals │ │ Buffett   │ │ Position   │
│ Technical    │ │ Burry     │ │ Sizing     │
│ Sentiment    │ │ Wood      │ │ Volatility │
│ Growth       │ │ + 9 more  │ │            │
│ News         │ │ + custom  │ │            │
└──────┬───────┘ └─────┬─────┘ └─────┬──────┘
       │               │             │
       └───────────────┼─────────────┘
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
              │  + Alpaca Exec  │
              └─────────────────┘
```

---

## Data Sources

All financial data comes from free, public APIs. No accounts or API keys needed for data.

| Data Type | Source | Cache TTL |
|---|---|---|
| Prices (OHLCV) | yfinance | 15 min |
| Financial metrics (P/E, margins, etc.) | yfinance `.info` + SEC EDGAR XBRL | 24 hrs |
| Financial statements (line items) | SEC EDGAR XBRL companyfacts | 24 hrs |
| Insider trades | SEC EDGAR Form 4 filings | 7 days |
| Company news | yfinance news feed | 15 min |
| Market cap / company info | yfinance + SEC EDGAR | 24 hrs |
| CIK resolution | SEC EDGAR company_tickers.json | 30 days |

The data layer (`src/tools/api_free.py`) is a drop-in replacement for the upstream's `financialdatasets.ai` client. Same function signatures, same return types — agents don't know the difference.

> **Note:** The original `financialdatasets.ai` implementation is preserved as `src/tools/api_original.py` if you ever want to revert.

---

## Quick Start

### Prerequisites

- Python 3.11+
- [Poetry](https://python-poetry.org/)
- [Ollama](https://ollama.ai/) with at least one model pulled (`ollama pull llama3:8b`)
- [Alpaca](https://app.alpaca.markets) paper trading account (free)

### Setup

```bash
git clone https://github.com/zhound420/swarm-trader.git
cd swarm-trader
poetry install
pip install yfinance  # Required for free data layer

cp .env.example .env
# Add your Alpaca keys to .env
```

### Verify Data Layer

```bash
python test_data.py --ticker NVDA
```

Expected output:
```
============================================================
  SMOKE TEST — NVDA
  Data source: SEC EDGAR + yfinance (api_free.py)
============================================================
  get_prices                     ✅ PASS (20 days)
  get_financial_metrics          ✅ PASS (3 periods, market_cap=...)
  search_line_items              ✅ PASS (4 periods, fields: [...])
  get_insider_trades             ✅ PASS (10 trades, ...)
  get_company_news               ✅ PASS (10 articles)
  get_market_cap                 ✅ PASS ($...)
  get_price_data                 ✅ PASS (20 rows, ...)
============================================================
  Result: 7/7 passed
============================================================
```

### Run

```bash
# Dry run — analyze holdings, show what trades would happen
poetry run python run_hedge_fund.py

# Analyze specific tickers with reasoning
poetry run python run_hedge_fund.py --tickers NVDA,AVGO,TSM --show-reasoning

# Actually execute trades on Alpaca paper
poetry run python run_hedge_fund.py --execute

# Chat-friendly output (for Telegram/Discord)
poetry run python run_hedge_fund.py --telegram
```

---

## CLI Flags

| Flag | Default | Description |
|---|---|---|
| `--execute` | off (dry run) | Place real orders on Alpaca paper |
| `--tickers X,Y,Z` | all holdings | Analyze specific tickers |
| `--model NAME` | `llama3:8b` | Ollama model to use |
| `--analysts a,b,c` | buffett, burry, wood, + more | Comma-separated analyst list |
| `--show-reasoning` | off | Print detailed reasoning from each agent |
| `--telegram` | off | Bullet-list output for chat |

---

## Analyst Agents

### LLM Agents (12 built-in)

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

### Data Agents (no LLM)

| Agent | What it does |
|---|---|
| `fundamentals_analyst` | ROE, margins, P/E, P/B |
| `technical_analyst` | Trend, momentum, volatility |
| `sentiment_analyst` | Market sentiment |
| `growth_analyst` | Revenue acceleration, R&D |

### Custom Agents

Create your own — see `src/agents/mordecai.py` as a template. Register in `src/utils/analysts.py`. Full guide in [PLAYBOOK.md](./PLAYBOOK.md).

---

## Safety Rails

Every trade passes through all rails before execution:

| Rail | Default | Purpose |
|---|---|---|
| Max trade size | 5% of portfolio | No single trade too large |
| Max daily trades | 5 per session | Prevents runaway loops |
| Min confidence | 70% | Must be confident to act |
| Min keep | 10% | Never sells entire position |
| Paper only | Enforced | Hardcoded to paper endpoint |
| Dry run default | On | Must pass `--execute` |

---

## Automation

Built for headless, cron-driven operation. Example daily schedule:

| Time | Job |
|---|---|
| 6:30 AM | Pre-market full analysis |
| 9:00 AM | Portfolio check + P/L report |
| 12:00 PM | Midday pulse (lighter agents) |
| 2:00 PM | Afternoon analysis |
| 4:30 PM | Post-close deep research |

See [PLAYBOOK.md](./PLAYBOOK.md) for complete cron setup.

---

## Project Structure

```
swarm-trader/
├── run_hedge_fund.py          # Main runner — analysis + execution
├── check_portfolio.py         # Quick Alpaca portfolio check
├── rebalance.py               # Bulk sell positions outside universe
├── test_data.py               # Data layer smoke test
├── PLAYBOOK.md                # Complete operations guide
├── .env.example               # Secret template
├── src/
│   ├── tools/
│   │   ├── api.py             # Import router (points to api_free)
│   │   ├── api_free.py        # ⭐ Free data layer (SEC EDGAR + yfinance)
│   │   └── api_original.py    # Original financialdatasets.ai client
│   ├── agents/                # 12 LLM + 4 data agents + custom
│   ├── alpaca_integration.py  # Alpaca API + safety rails
│   └── llm/
│       └── ollama_models.json # Available models (edit this)
└── .cache/                    # Disk cache (gitignored)
```

---

## Credits

Built on [virattt/ai-hedge-fund](https://github.com/virattt/ai-hedge-fund). Free data layer replaces the paid `financialdatasets.ai` dependency with SEC EDGAR XBRL + yfinance.

## Disclaimer

Educational and research purposes only. Not investment advice. Paper trading only.

## License

MIT
