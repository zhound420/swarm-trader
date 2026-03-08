"""
Drop-in replacement for api.py using free public data sources.
- Prices: yfinance
- Financial metrics: SEC EDGAR XBRL companyfacts + yfinance
- Line items: SEC EDGAR XBRL companyfacts
- Insider trades: SEC EDGAR Form 4 RSS
- Company news: yfinance
- Company facts / market cap: yfinance .info + SEC EDGAR
- File-based JSON cache with TTLs

No paid API keys required.
"""

import datetime
import hashlib
import json
import os
import time
import traceback
from pathlib import Path

import pandas as pd
import requests

from src.data.cache import get_cache
from src.data.models import (
    CompanyNews,
    CompanyNewsResponse,
    FinancialMetrics,
    FinancialMetricsResponse,
    Price,
    PriceResponse,
    LineItem,
    LineItemResponse,
    InsiderTrade,
    InsiderTradeResponse,
    CompanyFactsResponse,
    CompanyFacts,
)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

_cache = get_cache()

CACHE_DIR = Path(__file__).resolve().parents[2] / ".cache"
CACHE_DIR.mkdir(exist_ok=True)

SEC_USER_AGENT = os.environ.get(
    "SEC_USER_AGENT", "GLORFT mordecai@naboo.lan"
)
SEC_HEADERS = {
    "User-Agent": SEC_USER_AGENT,
    "Accept": "application/json",
}
_sec_last_request = 0.0
SEC_MIN_INTERVAL = 0.11  # ~9 req/sec, safely under 10

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sec_throttle():
    """Respect SEC EDGAR rate limit of 10 req/sec."""
    global _sec_last_request
    elapsed = time.time() - _sec_last_request
    if elapsed < SEC_MIN_INTERVAL:
        time.sleep(SEC_MIN_INTERVAL - elapsed)
    _sec_last_request = time.time()


def _disk_cache_get(namespace: str, key: str, ttl_seconds: int):
    """Read from JSON disk cache if fresh enough."""
    safe_key = hashlib.md5(key.encode()).hexdigest()
    path = CACHE_DIR / namespace / f"{safe_key}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        if time.time() - data.get("ts", 0) < ttl_seconds:
            return data["payload"]
    except Exception:
        pass
    return None


def _disk_cache_set(namespace: str, key: str, payload):
    """Write to JSON disk cache."""
    safe_key = hashlib.md5(key.encode()).hexdigest()
    ns_dir = CACHE_DIR / namespace
    ns_dir.mkdir(exist_ok=True)
    path = ns_dir / f"{safe_key}.json"
    try:
        path.write_text(json.dumps({"ts": time.time(), "payload": payload}, default=str))
    except Exception:
        pass


def _get_yf_ticker(ticker: str):
    """Get a yfinance Ticker object (lazy import)."""
    import yfinance as yf
    return yf.Ticker(ticker)


def _resolve_cik(ticker: str) -> str | None:
    """Resolve ticker to CIK via SEC EDGAR."""
    cached = _disk_cache_get("cik", ticker, ttl_seconds=30 * 86400)
    if cached:
        return cached

    _sec_throttle()
    url = "https://efts.sec.gov/LATEST/search-index?q=%22{}%22&dateRange=custom&startdt=2020-01-01&forms=10-K".format(ticker)
    # Better: use the tickers.json mapping
    try:
        _sec_throttle()
        resp = requests.get(
            "https://www.sec.gov/files/company_tickers.json",
            headers=SEC_HEADERS,
            timeout=15,
        )
        if resp.status_code == 200:
            data = resp.json()
            for entry in data.values():
                if entry.get("ticker", "").upper() == ticker.upper():
                    cik = str(entry["cik_str"]).zfill(10)
                    _disk_cache_set("cik", ticker, cik)
                    return cik
    except Exception:
        pass
    return None


def _get_company_facts_sec(cik: str) -> dict | None:
    """Fetch XBRL companyfacts from SEC EDGAR."""
    cache_key = f"companyfacts_{cik}"
    cached = _disk_cache_get("edgar", cache_key, ttl_seconds=24 * 3600)
    if cached:
        return cached

    _sec_throttle()
    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
    try:
        resp = requests.get(url, headers=SEC_HEADERS, timeout=30)
        if resp.status_code == 200:
            data = resp.json()
            _disk_cache_set("edgar", cache_key, data)
            return data
    except Exception:
        pass
    return None


def _extract_xbrl_values(facts: dict, concept: str, namespace: str = "us-gaap", unit: str = "USD", period: str = "quarterly") -> list[dict]:
    """Extract values for a given XBRL concept from companyfacts."""
    try:
        ns_data = facts.get("facts", {}).get(namespace, {})
        concept_data = ns_data.get(concept, {})
        units = concept_data.get("units", {})
        entries = units.get(unit, []) or units.get("USD/shares", []) or units.get("pure", [])
        if not entries:
            # Try first available unit
            for u, v in units.items():
                entries = v
                break

        results = []
        for e in entries:
            # Filter by form type for quarterly/annual
            form = e.get("form", "")
            if period == "quarterly" and form not in ("10-Q", "10-K"):
                continue
            if period == "annual" and form != "10-K":
                continue
            results.append({
                "value": e.get("val"),
                "end": e.get("end"),
                "filed": e.get("filed"),
                "form": form,
                "fp": e.get("fp"),
            })
        return sorted(results, key=lambda x: x.get("end", ""), reverse=True)
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Public API — identical signatures to api.py
# ---------------------------------------------------------------------------


def get_prices(ticker: str, start_date: str, end_date: str, api_key: str = None) -> list[Price]:
    """Fetch price data using yfinance."""
    cache_key = f"{ticker}_{start_date}_{end_date}"

    if cached_data := _cache.get_prices(cache_key):
        return [Price(**price) for price in cached_data]

    # Check disk cache (15 min TTL)
    disk = _disk_cache_get("prices", cache_key, ttl_seconds=900)
    if disk:
        _cache.set_prices(cache_key, disk)
        return [Price(**p) for p in disk]

    try:
        yf_ticker = _get_yf_ticker(ticker)
        # yfinance end is exclusive, add 1 day
        end_dt = datetime.datetime.strptime(end_date, "%Y-%m-%d") + datetime.timedelta(days=1)
        df = yf_ticker.history(start=start_date, end=end_dt.strftime("%Y-%m-%d"), interval="1d")

        if df.empty:
            return []

        prices = []
        for idx, row in df.iterrows():
            prices.append({
                "open": float(row["Open"]),
                "close": float(row["Close"]),
                "high": float(row["High"]),
                "low": float(row["Low"]),
                "volume": int(row["Volume"]),
                "time": idx.strftime("%Y-%m-%dT00:00:00Z"),
            })

        _cache.set_prices(cache_key, prices)
        _disk_cache_set("prices", cache_key, prices)
        return [Price(**p) for p in prices]
    except Exception as e:
        print(f"[api_free] get_prices error for {ticker}: {e}")
        return []


def get_financial_metrics(
    ticker: str,
    end_date: str,
    period: str = "ttm",
    limit: int = 10,
    api_key: str = None,
) -> list[FinancialMetrics]:
    """Fetch financial metrics using yfinance .info + SEC EDGAR XBRL."""
    cache_key = f"{ticker}_{period}_{end_date}_{limit}"

    if cached_data := _cache.get_financial_metrics(cache_key):
        return [FinancialMetrics(**m) for m in cached_data]

    disk = _disk_cache_get("metrics", cache_key, ttl_seconds=24 * 3600)
    if disk:
        _cache.set_financial_metrics(cache_key, disk)
        return [FinancialMetrics(**m) for m in disk]

    try:
        yf_ticker = _get_yf_ticker(ticker)
        info = yf_ticker.info or {}

        # Pull financial statements for richer metrics
        try:
            inc_stmt = yf_ticker.financials  # annual income statement
            bal_sheet = yf_ticker.balance_sheet  # annual balance sheet
            cash_flow = yf_ticker.cashflow  # annual cash flow
        except Exception:
            inc_stmt = bal_sheet = cash_flow = None

        # Extract statement values (current, previous) for ratio computation
        revenue_cur, revenue_prev = _extract_statement_value(inc_stmt, "Total Revenue", "Revenue")
        cogs_cur, _ = _extract_statement_value(inc_stmt, "Cost Of Revenue", "Cost Of Goods Sold")
        ebit_cur, _ = _extract_statement_value(inc_stmt, "EBIT", "Operating Income")
        net_income_cur, net_income_prev = _extract_statement_value(inc_stmt, "Net Income", "Net Income Common Stockholders")
        interest_exp_cur, _ = _extract_statement_value(inc_stmt, "Interest Expense", "Interest Expense Non Operating")
        operating_income_cur, operating_income_prev = _extract_statement_value(inc_stmt, "Operating Income", "EBIT")
        ebitda_cur, ebitda_prev = _extract_statement_value(inc_stmt, "EBITDA", "Normalized EBITDA")

        total_assets_cur, _ = _extract_statement_value(bal_sheet, "Total Assets")
        current_assets_cur, _ = _extract_statement_value(bal_sheet, "Current Assets")
        current_liab_cur, _ = _extract_statement_value(bal_sheet, "Current Liabilities")
        inventory_cur, _ = _extract_statement_value(bal_sheet, "Inventory")
        receivables_cur, _ = _extract_statement_value(bal_sheet, "Accounts Receivable", "Net Receivables")
        cash_cur, _ = _extract_statement_value(bal_sheet, "Cash And Cash Equivalents", "Cash")
        total_debt_cur, _ = _extract_statement_value(bal_sheet, "Total Debt", "Long Term Debt")
        equity_cur, equity_prev = _extract_statement_value(bal_sheet, "Stockholders Equity", "Total Stockholder Equity")

        ocf_cur, _ = _extract_statement_value(cash_flow, "Operating Cash Flow", "Total Cash From Operating Activities")
        fcf_cur, fcf_prev = _extract_statement_value(cash_flow, "Free Cash Flow")

        # Compute EPS from statements for growth
        eps_cur = _safe_float(info.get("trailingEps"))
        shares = _safe_float(info.get("sharesOutstanding"))
        eps_prev = _safe_div(net_income_prev, shares) if net_income_prev and shares else None

        # Book value growth
        bv_cur = _safe_float(info.get("bookValue"))
        bv_prev = _safe_div(equity_prev, shares) if equity_prev and shares else None

        # Compute ratio fields from statements
        roic = _safe_div(ebit_cur, (total_assets_cur - current_liab_cur)) if total_assets_cur and current_liab_cur else None
        asset_turnover = _safe_div(revenue_cur, total_assets_cur)
        inventory_turnover = _safe_div(cogs_cur, inventory_cur)
        receivables_turnover = _safe_div(revenue_cur, receivables_cur)
        dso = _safe_div(365.0, receivables_turnover) if receivables_turnover else None
        dio = _safe_div(365.0, inventory_turnover) if inventory_turnover else None
        operating_cycle = (dio + dso) if dio is not None and dso is not None else None
        working_cap = (current_assets_cur - current_liab_cur) if current_assets_cur and current_liab_cur else None
        working_capital_turnover = _safe_div(revenue_cur, working_cap) if working_cap and working_cap != 0 else None
        cash_ratio = _safe_div(cash_cur, current_liab_cur)
        ocf_ratio = _safe_div(ocf_cur, current_liab_cur)
        debt_to_assets = _safe_div(total_debt_cur, total_assets_cur)
        interest_coverage = _safe_div(ebit_cur, abs(interest_exp_cur)) if interest_exp_cur and interest_exp_cur != 0 else None

        # Compute growth fields
        revenue_growth = info.get("revenueGrowth") or _safe_growth(revenue_cur, revenue_prev)
        earnings_growth = info.get("earningsGrowth") or _safe_growth(net_income_cur, net_income_prev)
        book_value_growth = _safe_growth(bv_cur, bv_prev)
        eps_growth = _safe_growth(eps_cur, eps_prev)
        fcf_growth = _safe_growth(fcf_cur, fcf_prev)
        oi_growth = _safe_growth(operating_income_cur, operating_income_prev)
        ebitda_growth = _safe_growth(ebitda_cur, ebitda_prev)

        # Build a single metrics entry from yfinance info + statements
        metric = {
            "ticker": ticker,
            "report_period": end_date,
            "period": period,
            "currency": info.get("currency", "USD"),
            "market_cap": info.get("marketCap"),
            "enterprise_value": info.get("enterpriseValue"),
            "price_to_earnings_ratio": info.get("trailingPE"),
            "price_to_book_ratio": info.get("priceToBook"),
            "price_to_sales_ratio": info.get("priceToSalesTrailing12Months"),
            "enterprise_value_to_ebitda_ratio": info.get("enterpriseToEbitda"),
            "enterprise_value_to_revenue_ratio": info.get("enterpriseToRevenue"),
            "free_cash_flow_yield": _safe_div(info.get("freeCashflow"), info.get("marketCap")),
            "peg_ratio": info.get("pegRatio"),
            "gross_margin": info.get("grossMargins"),
            "operating_margin": info.get("operatingMargins"),
            "net_margin": info.get("profitMargins"),
            "return_on_equity": info.get("returnOnEquity"),
            "return_on_assets": info.get("returnOnAssets"),
            "return_on_invested_capital": roic,
            "asset_turnover": asset_turnover,
            "inventory_turnover": inventory_turnover,
            "receivables_turnover": receivables_turnover,
            "days_sales_outstanding": dso,
            "operating_cycle": operating_cycle,
            "working_capital_turnover": working_capital_turnover,
            "current_ratio": info.get("currentRatio"),
            "quick_ratio": info.get("quickRatio"),
            "cash_ratio": cash_ratio,
            "operating_cash_flow_ratio": ocf_ratio,
            "debt_to_equity": info.get("debtToEquity"),
            "debt_to_assets": debt_to_assets,
            "interest_coverage": interest_coverage,
            "revenue_growth": revenue_growth,
            "earnings_growth": earnings_growth,
            "book_value_growth": book_value_growth,
            "earnings_per_share_growth": eps_growth,
            "free_cash_flow_growth": fcf_growth,
            "operating_income_growth": oi_growth,
            "ebitda_growth": ebitda_growth,
            "payout_ratio": info.get("payoutRatio"),
            "earnings_per_share": eps_cur,
            "book_value_per_share": bv_cur,
            "free_cash_flow_per_share": _safe_div(info.get("freeCashflow"), shares),
        }

        # Try to enrich with SEC EDGAR for historical periods
        metrics_list = [metric]

        if limit > 1:
            cik = _resolve_cik(ticker)
            if cik:
                facts = _get_company_facts_sec(cik)
                if facts:
                    historical = _build_historical_metrics(ticker, facts, end_date, period, limit - 1)
                    metrics_list.extend(historical)

        metrics_list = metrics_list[:limit]
        _cache.set_financial_metrics(cache_key, metrics_list)
        _disk_cache_set("metrics", cache_key, metrics_list)
        return [FinancialMetrics(**m) for m in metrics_list]
    except Exception as e:
        print(f"[api_free] get_financial_metrics error for {ticker}: {e}")
        return []


def _safe_div(a, b):
    if a is not None and b is not None and b != 0:
        return a / b
    return None


def _safe_growth(current, previous):
    """Compute percentage growth safely: (current - previous) / abs(previous)."""
    if current is not None and previous is not None and previous != 0:
        return (current - previous) / abs(previous)
    return None


def _safe_float(val):
    """Convert a value to float, returning None if not possible."""
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _extract_statement_value(df, *row_names):
    """Extract the most recent value from a yfinance statement DataFrame, trying multiple row names."""
    if df is None or df.empty:
        return None, None
    for name in row_names:
        if name in df.index:
            row = df.loc[name]
            # First column is most recent
            val = _safe_float(row.iloc[0])
            prev = _safe_float(row.iloc[1]) if len(row) > 1 else None
            return val, prev
    return None, None


def _build_historical_metrics(ticker: str, facts: dict, end_date: str, period: str, limit: int) -> list[dict]:
    """Build historical FinancialMetrics from SEC EDGAR XBRL data."""
    # Map concept names to metric fields
    revenue_vals = _extract_xbrl_values(facts, "Revenues", period=period) or _extract_xbrl_values(facts, "RevenueFromContractWithCustomerExcludingAssessedTax", period=period)
    net_income_vals = _extract_xbrl_values(facts, "NetIncomeLoss", period=period)
    total_assets_vals = _extract_xbrl_values(facts, "Assets", period=period)
    total_equity_vals = _extract_xbrl_values(facts, "StockholdersEquity", period=period)
    total_debt_vals = _extract_xbrl_values(facts, "LongTermDebt", period=period)
    eps_vals = _extract_xbrl_values(facts, "EarningsPerShareBasic", period=period)

    # Collect unique report periods
    periods_seen = set()
    report_periods = []
    for v in (revenue_vals + net_income_vals + total_assets_vals):
        end = v.get("end")
        if end and end <= end_date and end not in periods_seen:
            periods_seen.add(end)
            report_periods.append(end)
    report_periods = sorted(report_periods, reverse=True)[:limit]

    def _find_val(vals, rp):
        for v in vals:
            if v.get("end") == rp:
                return v.get("value")
        return None

    metrics = []
    for rp in report_periods:
        revenue = _find_val(revenue_vals, rp)
        net_income = _find_val(net_income_vals, rp)
        assets = _find_val(total_assets_vals, rp)
        equity = _find_val(total_equity_vals, rp)
        debt = _find_val(total_debt_vals, rp)

        metrics.append({
            "ticker": ticker,
            "report_period": rp,
            "period": period,
            "currency": "USD",
            "market_cap": None,
            "enterprise_value": None,
            "price_to_earnings_ratio": None,
            "price_to_book_ratio": None,
            "price_to_sales_ratio": None,
            "enterprise_value_to_ebitda_ratio": None,
            "enterprise_value_to_revenue_ratio": None,
            "free_cash_flow_yield": None,
            "peg_ratio": None,
            "gross_margin": None,
            "operating_margin": _safe_div(net_income, revenue) if revenue else None,
            "net_margin": _safe_div(net_income, revenue) if revenue else None,
            "return_on_equity": _safe_div(net_income, equity) if equity else None,
            "return_on_assets": _safe_div(net_income, assets) if assets else None,
            "return_on_invested_capital": None,
            "asset_turnover": _safe_div(revenue, assets) if assets else None,
            "inventory_turnover": None,
            "receivables_turnover": None,
            "days_sales_outstanding": None,
            "operating_cycle": None,
            "working_capital_turnover": None,
            "current_ratio": None,
            "quick_ratio": None,
            "cash_ratio": None,
            "operating_cash_flow_ratio": None,
            "debt_to_equity": _safe_div(debt, equity) if equity else None,
            "debt_to_assets": _safe_div(debt, assets) if assets else None,
            "interest_coverage": None,
            "revenue_growth": None,
            "earnings_growth": None,
            "book_value_growth": None,
            "earnings_per_share_growth": None,
            "free_cash_flow_growth": None,
            "operating_income_growth": None,
            "ebitda_growth": None,
            "payout_ratio": None,
            "earnings_per_share": _find_val(eps_vals, rp),
            "book_value_per_share": None,
            "free_cash_flow_per_share": None,
        })

    return metrics


def search_line_items(
    ticker: str,
    line_items: list[str],
    end_date: str,
    period: str = "ttm",
    limit: int = 10,
    api_key: str = None,
) -> list[LineItem]:
    """Fetch line items from SEC EDGAR XBRL companyfacts."""
    cache_key = f"{ticker}_{'_'.join(sorted(line_items))}_{end_date}_{period}_{limit}"

    disk = _disk_cache_get("lineitems", cache_key, ttl_seconds=24 * 3600)
    if disk:
        return [LineItem(**item) for item in disk]

    cik = _resolve_cik(ticker)
    if not cik:
        return []

    facts = _get_company_facts_sec(cik)
    if not facts:
        return []

    # Map common line item names to XBRL concepts
    LINE_ITEM_MAP = {
        # Income statement
        "revenue": ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet"],
        "total_revenue": ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet"],
        "cost_of_revenue": ["CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold"],
        "gross_profit": ["GrossProfit"],
        "operating_income": ["OperatingIncomeLoss"],
        "operating_expense": ["OperatingExpenses"],
        "net_income": ["NetIncomeLoss"],
        "ebitda": ["EarningsBeforeInterestTaxesDepreciationAndAmortization"],
        "interest_expense": ["InterestExpense"],
        "income_tax_expense": ["IncomeTaxExpenseBenefit"],
        "depreciation_and_amortization": ["DepreciationDepletionAndAmortization", "DepreciationAndAmortization"],
        "research_and_development": ["ResearchAndDevelopmentExpense"],
        "selling_general_and_administrative": ["SellingGeneralAndAdministrativeExpense"],
        # Balance sheet
        "total_assets": ["Assets"],
        "total_liabilities": ["Liabilities"],
        "total_equity": ["StockholdersEquity"],
        "current_assets": ["AssetsCurrent"],
        "current_liabilities": ["LiabilitiesCurrent"],
        "cash_and_equivalents": ["CashAndCashEquivalentsAtCarryingValue"],
        "cash_and_short_term_investments": ["CashCashEquivalentsAndShortTermInvestments"],
        "inventory": ["InventoryNet"],
        "accounts_receivable": ["AccountsReceivableNetCurrent"],
        "accounts_payable": ["AccountsPayableCurrent"],
        "long_term_debt": ["LongTermDebt", "LongTermDebtNoncurrent"],
        "short_term_debt": ["ShortTermBorrowings"],
        "total_debt": ["LongTermDebt", "DebtCurrent"],
        "goodwill": ["Goodwill"],
        "intangible_assets": ["IntangibleAssetsNetExcludingGoodwill"],
        "shareholders_equity": ["StockholdersEquity"],
        "retained_earnings": ["RetainedEarningsAccumulatedDeficit"],
        "book_value_per_share": ["BookValuePerShareDiluted"],
        "outstanding_shares": ["CommonStockSharesOutstanding", "EntityCommonStockSharesOutstanding"],
        # Cash flow
        "operating_cash_flow": ["NetCashProvidedByUsedInOperatingActivities"],
        "capital_expenditure": ["PaymentsToAcquirePropertyPlantAndEquipment"],
        "free_cash_flow": ["NetCashProvidedByUsedInOperatingActivities"],  # Will subtract capex
        "dividends_paid": ["PaymentsOfDividends", "PaymentsOfDividendsCommonStock"],
        "share_repurchase": ["PaymentsForRepurchaseOfCommonStock"],
        "issuance_of_debt": ["ProceedsFromIssuanceOfLongTermDebt"],
        "repayment_of_debt": ["RepaymentsOfLongTermDebt"],
        "net_income_from_cash_flow": ["NetIncomeLoss"],
        "depreciation_from_cash_flow": ["DepreciationDepletionAndAmortization"],
        # Additional mappings for completeness
        "ebit": ["OperatingIncomeLoss"],
        "income_before_tax": ["IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest"],
        "weighted_average_shares": ["WeightedAverageNumberOfShareOutstandingBasicAndDiluted", "CommonStockSharesOutstanding"],
        "net_cash_from_operations": ["NetCashProvidedByUsedInOperatingActivities"],
        "net_cash_from_investing": ["NetCashProvidedByUsedInInvestingActivities"],
        "net_cash_from_financing": ["NetCashProvidedByUsedInFinancingActivities"],
    }

    # Collect report periods from XBRL data
    all_results = []
    report_period_data = {}  # rp -> {field: value}

    for item_name in line_items:
        concepts = LINE_ITEM_MAP.get(item_name.lower(), [item_name])
        for concept in concepts:
            vals = _extract_xbrl_values(facts, concept, period="quarterly" if period != "annual" else "annual")
            if vals:
                for v in vals:
                    rp = v.get("end", "")
                    if rp and rp <= end_date:
                        if rp not in report_period_data:
                            report_period_data[rp] = {}
                        if item_name not in report_period_data[rp]:
                            report_period_data[rp][item_name] = v.get("value")
                break  # Use first concept that has data

    # Build LineItem objects
    sorted_periods = sorted(report_period_data.keys(), reverse=True)[:limit]
    results = []
    for rp in sorted_periods:
        item_data = {
            "ticker": ticker,
            "report_period": rp,
            "period": period,
            "currency": "USD",
        }
        item_data.update(report_period_data[rp])
        results.append(item_data)

    if results:
        _disk_cache_set("lineitems", cache_key, results)

    return [LineItem(**item) for item in results]


def get_insider_trades(
    ticker: str,
    end_date: str,
    start_date: str | None = None,
    limit: int = 1000,
    api_key: str = None,
) -> list[InsiderTrade]:
    """Fetch insider trades from SEC EDGAR Form 4 filings."""
    cache_key = f"{ticker}_{start_date or 'none'}_{end_date}_{limit}"

    if cached_data := _cache.get_insider_trades(cache_key):
        return [InsiderTrade(**trade) for trade in cached_data]

    disk = _disk_cache_get("insider", cache_key, ttl_seconds=7 * 86400)
    if disk:
        _cache.set_insider_trades(cache_key, disk)
        return [InsiderTrade(**t) for t in disk]

    cik = _resolve_cik(ticker)
    if not cik:
        return []

    try:
        _sec_throttle()
        # Use EDGAR full-text search for Form 4 filings
        url = f"https://efts.sec.gov/LATEST/search-index?q=%22{ticker}%22&forms=4&dateRange=custom"
        if start_date:
            url += f"&startdt={start_date}"
        url += f"&enddt={end_date}"

        # Alternative: use the submissions API
        _sec_throttle()
        submissions_url = f"https://data.sec.gov/submissions/CIK{cik}.json"
        resp = requests.get(submissions_url, headers=SEC_HEADERS, timeout=30)

        if resp.status_code != 200:
            return []

        data = resp.json()
        company_name = data.get("name", ticker)
        recent = data.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        dates = recent.get("filingDate", [])
        accessions = recent.get("accessionNumber", [])

        trades = []
        for i, form in enumerate(forms):
            if form != "4" and form != "4/A":
                continue
            filing_date = dates[i] if i < len(dates) else ""
            if filing_date > end_date:
                continue
            if start_date and filing_date < start_date:
                continue

            # We can't easily parse the XML for full detail without heavy lifting,
            # so we create a simplified trade record from the filing metadata
            trades.append({
                "ticker": ticker,
                "issuer": company_name,
                "name": None,
                "title": None,
                "is_board_director": None,
                "transaction_date": filing_date,
                "transaction_shares": None,
                "transaction_price_per_share": None,
                "transaction_value": None,
                "shares_owned_before_transaction": None,
                "shares_owned_after_transaction": None,
                "security_title": None,
                "filing_date": filing_date,
            })

            if len(trades) >= limit:
                break

        # Enrich with actual Form 4 XML parsing for recent filings (top 10)
        for j, trade in enumerate(trades[:10]):
            acc = None
            for i, form in enumerate(forms):
                if (form == "4" or form == "4/A") and dates[i] == trade["filing_date"]:
                    acc = accessions[i]
                    break
            if acc:
                parsed = _parse_form4_xml(cik, acc)
                if parsed:
                    trade.update(parsed)

        if trades:
            _cache.set_insider_trades(cache_key, trades)
            _disk_cache_set("insider", cache_key, trades)

        return [InsiderTrade(**t) for t in trades]
    except Exception as e:
        print(f"[api_free] get_insider_trades error for {ticker}: {e}")
        return []


def _parse_form4_xml(cik: str, accession: str) -> dict | None:
    """Parse a Form 4 XML filing for transaction details."""
    try:
        acc_clean = accession.replace("-", "")
        _sec_throttle()
        # Try to get the index page for the filing
        index_url = f"https://www.sec.gov/Archives/edgar/data/{cik.lstrip('0')}/{acc_clean}/"
        resp = requests.get(index_url, headers=SEC_HEADERS, timeout=15)
        if resp.status_code != 200:
            return None

        # Look for the XML file in a simple way
        import re
        xml_files = re.findall(r'href="([^"]*\.xml)"', resp.text)
        if not xml_files:
            return None

        xml_url = f"{index_url}{xml_files[0]}"
        _sec_throttle()
        xml_resp = requests.get(xml_url, headers=SEC_HEADERS, timeout=15)
        if xml_resp.status_code != 200:
            return None

        # Simple XML parsing
        import xml.etree.ElementTree as ET
        root = ET.fromstring(xml_resp.text)

        # Namespace handling
        ns = {"": "http://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=0001045810&type=4&dateb=&owner=include&count=40"}

        # Try to extract without namespace first
        def _find_text(element, *tags):
            for tag in tags:
                el = element.find(f".//{tag}")
                if el is not None and el.text:
                    return el.text.strip()
            return None

        reporter_name = _find_text(root, "rptOwnerName")
        reporter_title = _find_text(root, "officerTitle")
        is_director = _find_text(root, "isDirector")
        tx_date = _find_text(root, "transactionDate", "value")
        tx_shares = _find_text(root, "transactionShares", "value")
        tx_price = _find_text(root, "transactionPricePerShare", "value")
        shares_after = _find_text(root, "sharesOwnedFollowingTransaction", "value")
        security = _find_text(root, "securityTitle", "value")

        result = {}
        if reporter_name:
            result["name"] = reporter_name
        if reporter_title:
            result["title"] = reporter_title
        if is_director:
            result["is_board_director"] = is_director == "1" or is_director.lower() == "true"
        if tx_date:
            result["transaction_date"] = tx_date
        if tx_shares:
            try:
                result["transaction_shares"] = float(tx_shares)
            except ValueError:
                pass
        if tx_price:
            try:
                result["transaction_price_per_share"] = float(tx_price)
            except ValueError:
                pass
        if tx_shares and tx_price:
            try:
                result["transaction_value"] = abs(float(tx_shares) * float(tx_price))
            except ValueError:
                pass
        if shares_after:
            try:
                result["shares_owned_after_transaction"] = float(shares_after)
            except ValueError:
                pass
        if security:
            result["security_title"] = security

        return result if result else None
    except Exception:
        return None


def get_company_news(
    ticker: str,
    end_date: str,
    start_date: str | None = None,
    limit: int = 1000,
    api_key: str = None,
) -> list[CompanyNews]:
    """Fetch company news using yfinance."""
    cache_key = f"{ticker}_{start_date or 'none'}_{end_date}_{limit}"

    if cached_data := _cache.get_company_news(cache_key):
        return [CompanyNews(**news) for news in cached_data]

    disk = _disk_cache_get("news", cache_key, ttl_seconds=900)
    if disk:
        _cache.set_company_news(cache_key, disk)
        return [CompanyNews(**n) for n in disk]

    try:
        yf_ticker = _get_yf_ticker(ticker)
        news_items = yf_ticker.news or []

        all_news = []
        for item in news_items:
            pub_date = ""
            if "providerPublishTime" in item:
                pub_date = datetime.datetime.fromtimestamp(
                    item["providerPublishTime"]
                ).strftime("%Y-%m-%dT%H:%M:%SZ")
            elif "content" in item and "pubDate" in item.get("content", {}):
                pub_date = item["content"]["pubDate"]

            # Handle yfinance news format variations
            title = item.get("title", "")
            if not title and "content" in item:
                title = item["content"].get("title", "")

            source = item.get("publisher", "")
            if not source and "content" in item:
                source = item["content"].get("provider", {}).get("displayName", "")

            link = item.get("link", "")
            if not link and "content" in item:
                link = item["content"].get("canonicalUrl", {}).get("url", "")

            if not title:
                continue

            # Date filtering
            date_str = pub_date.split("T")[0] if pub_date else ""
            if date_str:
                if date_str > end_date:
                    continue
                if start_date and date_str < start_date:
                    continue

            all_news.append({
                "ticker": ticker,
                "title": title,
                "author": "",
                "source": source,
                "date": pub_date,
                "url": link,
                "sentiment": None,
            })

            if len(all_news) >= limit:
                break

        if all_news:
            _cache.set_company_news(cache_key, all_news)
            _disk_cache_set("news", cache_key, all_news)

        return [CompanyNews(**n) for n in all_news]
    except Exception as e:
        print(f"[api_free] get_company_news error for {ticker}: {e}")
        return []


def get_market_cap(
    ticker: str,
    end_date: str,
    api_key: str = None,
) -> float | None:
    """Fetch market cap using yfinance."""
    try:
        yf_ticker = _get_yf_ticker(ticker)
        info = yf_ticker.info or {}
        mc = info.get("marketCap")
        if mc:
            return float(mc)

        # Fallback to financial metrics
        metrics = get_financial_metrics(ticker, end_date, api_key=api_key)
        if metrics:
            return metrics[0].market_cap
        return None
    except Exception as e:
        print(f"[api_free] get_market_cap error for {ticker}: {e}")
        return None


def prices_to_df(prices: list[Price]) -> pd.DataFrame:
    """Convert prices to a DataFrame."""
    df = pd.DataFrame([p.model_dump() for p in prices])
    df["Date"] = pd.to_datetime(df["time"])
    df.set_index("Date", inplace=True)
    numeric_cols = ["open", "close", "high", "low", "volume"]
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df.sort_index(inplace=True)
    return df


def get_price_data(ticker: str, start_date: str, end_date: str, api_key: str = None) -> pd.DataFrame:
    """Get price data as a DataFrame."""
    prices = get_prices(ticker, start_date, end_date, api_key=api_key)
    return prices_to_df(prices)


def get_company_facts(ticker: str, api_key: str = None) -> CompanyFactsResponse:
    """Fetch company facts from SEC EDGAR submissions + yfinance."""
    cache_key = f"{ticker}_company_facts"
    disk = _disk_cache_get("company_facts", cache_key, ttl_seconds=3600)
    if disk:
        return CompanyFactsResponse(company_facts=CompanyFacts(**disk))

    cik = _resolve_cik(ticker)

    sec_info: dict = {}
    if cik:
        _sec_throttle()
        try:
            resp = requests.get(
                f"https://data.sec.gov/submissions/CIK{cik}.json",
                headers=SEC_HEADERS,
                timeout=15,
            )
            if resp.status_code == 200:
                sec_info = resp.json()
        except Exception:
            pass

    info: dict = {}
    try:
        info = _get_yf_ticker(ticker).info or {}
    except Exception:
        pass

    def sf(key) -> float | None:
        v = info.get(key)
        return float(v) if v is not None else None

    name = (
        info.get("longName")
        or info.get("shortName")
        or sec_info.get("name")
        or ticker
    )

    facts = CompanyFacts(
        ticker=ticker,
        name=name,
        cik=cik,
        industry=info.get("industry"),
        sector=info.get("sector"),
        exchange=info.get("exchange"),
        market_cap=sf("marketCap"),
        number_of_employees=info.get("fullTimeEmployees"),
        website_url=info.get("website"),
        sic_code=sec_info.get("sic"),
        sic_industry=sec_info.get("sicDescription"),
        sic_sector=None,
        sec_filings_url=(
            f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}"
            f"&type=&dateb=&owner=include&count=40"
            if cik else None
        ),
        weighted_average_shares=info.get("sharesOutstanding"),
        is_active=bool(info.get("quoteType")),
        listing_date=None,
        location=info.get("city"),
        category=None,
    )

    _disk_cache_set("company_facts", cache_key, facts.model_dump())
    return CompanyFactsResponse(company_facts=facts)
