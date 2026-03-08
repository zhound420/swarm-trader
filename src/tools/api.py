"""
API module — routes through free public data sources (SEC EDGAR + yfinance).
Original financialdatasets.ai implementation backed up as api_original.py.

To revert: copy api_original.py back to api.py
"""

# Re-export everything from api_free so all existing imports work unchanged
from src.tools.api_free import (
    get_prices,
    get_financial_metrics,
    search_line_items,
    get_insider_trades,
    get_company_news,
    get_company_facts,
    get_market_cap,
    prices_to_df,
    get_price_data,
)

__all__ = [
    "get_prices",
    "get_financial_metrics",
    "search_line_items",
    "get_insider_trades",
    "get_company_news",
    "get_company_facts",
    "get_market_cap",
    "prices_to_df",
    "get_price_data",
]
