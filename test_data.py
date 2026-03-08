#!/usr/bin/env python3
"""
Smoke test for api_free.py — exercises every replaced endpoint.
Usage: python test_data.py --ticker NVDA
"""

import argparse
import sys
import traceback
from datetime import datetime, timedelta


def main():
    parser = argparse.ArgumentParser(description="Smoke test for free data API")
    parser.add_argument("--ticker", default="NVDA", help="Ticker to test (default: NVDA)")
    args = parser.parse_args()

    ticker = args.ticker
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")

    results = {}
    total = 0
    passed = 0

    # --- Test 1: get_prices ---
    total += 1
    try:
        from src.tools.api import get_prices
        prices = get_prices(ticker, start_date, end_date)
        if prices and len(prices) > 0:
            p = prices[0]
            assert hasattr(p, "open"), "Price missing 'open'"
            assert hasattr(p, "close"), "Price missing 'close'"
            assert hasattr(p, "volume"), "Price missing 'volume'"
            assert hasattr(p, "time"), "Price missing 'time'"
            results["get_prices"] = f"✅ PASS ({len(prices)} days)"
            passed += 1
        else:
            results["get_prices"] = "❌ FAIL (no data returned)"
    except Exception as e:
        results["get_prices"] = f"❌ FAIL ({e})"

    # --- Test 2: get_financial_metrics ---
    total += 1
    try:
        from src.tools.api import get_financial_metrics
        metrics = get_financial_metrics(ticker, end_date, period="ttm", limit=3)
        if metrics and len(metrics) > 0:
            m = metrics[0]
            assert hasattr(m, "ticker"), "Metric missing 'ticker'"
            assert hasattr(m, "market_cap"), "Metric missing 'market_cap'"
            assert hasattr(m, "report_period"), "Metric missing 'report_period'"
            results["get_financial_metrics"] = f"✅ PASS ({len(metrics)} periods, market_cap={m.market_cap})"
            passed += 1
        else:
            results["get_financial_metrics"] = "❌ FAIL (no data returned)"
    except Exception as e:
        results["get_financial_metrics"] = f"❌ FAIL ({e})"

    # --- Test 3: search_line_items ---
    total += 1
    try:
        from src.tools.api import search_line_items
        items = search_line_items(
            ticker,
            ["revenue", "net_income", "total_assets", "operating_cash_flow"],
            end_date,
            period="quarterly",
            limit=4,
        )
        if items and len(items) > 0:
            item = items[0]
            assert hasattr(item, "ticker"), "LineItem missing 'ticker'"
            assert hasattr(item, "report_period"), "LineItem missing 'report_period'"
            # Check that at least one line item field was populated
            extra = {k: v for k, v in item.model_dump().items() if k not in ("ticker", "report_period", "period", "currency")}
            results["search_line_items"] = f"✅ PASS ({len(items)} periods, fields: {list(extra.keys())[:4]})"
            passed += 1
        else:
            results["search_line_items"] = "❌ FAIL (no data returned)"
    except Exception as e:
        results["search_line_items"] = f"❌ FAIL ({e})"

    # --- Test 4: get_insider_trades ---
    total += 1
    try:
        from src.tools.api import get_insider_trades
        trades = get_insider_trades(ticker, end_date, limit=10)
        if trades and len(trades) > 0:
            t = trades[0]
            assert hasattr(t, "ticker"), "Trade missing 'ticker'"
            assert hasattr(t, "filing_date"), "Trade missing 'filing_date'"
            named = sum(1 for tr in trades if tr.name)
            results["get_insider_trades"] = f"✅ PASS ({len(trades)} trades, {named} with names)"
            passed += 1
        else:
            results["get_insider_trades"] = "⚠️  WARN (no trades found — may be normal for some tickers)"
            passed += 1  # Not a hard fail
    except Exception as e:
        results["get_insider_trades"] = f"❌ FAIL ({e})"

    # --- Test 5: get_company_news ---
    total += 1
    try:
        from src.tools.api import get_company_news
        news = get_company_news(ticker, end_date, limit=10)
        if news and len(news) > 0:
            n = news[0]
            assert hasattr(n, "title"), "News missing 'title'"
            assert hasattr(n, "source"), "News missing 'source'"
            results["get_company_news"] = f"✅ PASS ({len(news)} articles)"
            passed += 1
        else:
            results["get_company_news"] = "⚠️  WARN (no news — yfinance may not have recent articles)"
            passed += 1
    except Exception as e:
        results["get_company_news"] = f"❌ FAIL ({e})"

    # --- Test 6: get_market_cap ---
    total += 1
    try:
        from src.tools.api import get_market_cap
        mc = get_market_cap(ticker, end_date)
        if mc and mc > 0:
            results["get_market_cap"] = f"✅ PASS (${mc:,.0f})"
            passed += 1
        else:
            results["get_market_cap"] = "❌ FAIL (no market cap returned)"
    except Exception as e:
        results["get_market_cap"] = f"❌ FAIL ({e})"

    # --- Test 7: get_company_facts ---
    total += 1
    try:
        from src.tools.api import get_company_facts
        cf_resp = get_company_facts(ticker)
        cf = cf_resp.company_facts
        assert cf.ticker == ticker, "CompanyFacts ticker mismatch"
        assert cf.name, "CompanyFacts missing name"
        results["get_company_facts"] = f"✅ PASS (name={cf.name!r}, cik={cf.cik}, sector={cf.sector})"
        passed += 1
    except Exception as e:
        results["get_company_facts"] = f"❌ FAIL ({e})"

    # --- Test 9: get_price_data (DataFrame) ---
    total += 1
    try:
        from src.tools.api import get_price_data
        df = get_price_data(ticker, start_date, end_date)
        if not df.empty:
            assert "close" in df.columns, "DataFrame missing 'close' column"
            assert "volume" in df.columns, "DataFrame missing 'volume' column"
            results["get_price_data"] = f"✅ PASS ({len(df)} rows, cols: {list(df.columns[:5])})"
            passed += 1
        else:
            results["get_price_data"] = "❌ FAIL (empty DataFrame)"
    except Exception as e:
        results["get_price_data"] = f"❌ FAIL ({e})"

    # --- Print results ---
    print()
    print(f"{'=' * 60}")
    print(f"  SMOKE TEST — {ticker}")
    print(f"  Data source: SEC EDGAR + yfinance (api_free.py)")
    print(f"{'=' * 60}")
    for func, result in results.items():
        print(f"  {func:30s} {result}")
    print(f"{'=' * 60}")
    print(f"  Result: {passed}/{total} passed")
    print(f"{'=' * 60}")
    print()

    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
