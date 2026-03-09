#!/usr/bin/env python3
"""
Mordecai AI Hedge Fund Runner

Fetches live Alpaca positions, runs multi-agent analysis, and executes trades with safety rails.

Usage:
  poetry run python run_analysis.py                    # Dry run, all holdings
  poetry run python run_analysis.py --execute          # Actually trade
  poetry run python run_analysis.py --tickers NVDA,AVGO  # Specific tickers only
  poetry run python run_analysis.py --telegram         # Telegram-friendly output
"""

import argparse
import json
import sys
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()  # Load .env before any imports that need credentials

from src.alpaca_integration import (
    get_alpaca_account,
    get_alpaca_positions,
    convert_to_portfolio,
    execute_decisions,
    format_positions_summary,
)
from src.main import run_hedge_fund


def main():
    parser = argparse.ArgumentParser(
        description="Run Mordecai AI Hedge Fund analysis and trading",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python run_analysis.py                           # Dry run all holdings
  python run_analysis.py --execute                 # Execute trades  
  python run_analysis.py --tickers NVDA,AVGO      # Analyze specific tickers
  python run_analysis.py --telegram               # Telegram-friendly format
  python run_analysis.py --model qwen3.5:cloud    # Use different model
        """
    )
    
    # Core flags
    parser.add_argument(
        "--execute", 
        action="store_true", 
        help="Actually execute trades (default: dry run)"
    )
    parser.add_argument(
        "--tickers", 
        type=str, 
        help="Comma-separated list of specific tickers to analyze (default: all holdings)"
    )
    parser.add_argument(
        "--telegram", 
        action="store_true", 
        help="Format output for Telegram (no markdown tables)"
    )
    
    # Model configuration
    parser.add_argument(
        "--model", 
        type=str, 
        default="claude-opus-4-20250514",
        help="Model to use (default: claude-opus-4-20250514)"
    )
    parser.add_argument(
        "--provider",
        type=str,
        default="Anthropic",
        help="Model provider (default: Anthropic)"
    )
    parser.add_argument(
        "--analysts", 
        type=str,
        default="mordecai,fundamentals_analyst,technical_analyst",
        help="Comma-separated list of analysts to use (default: mordecai + fundamentals + technical)"
    )
    parser.add_argument(
        "--top",
        type=int,
        default=5,
        help="Analyze only top N positions by value (default: 5, use 0 for all)"
    )
    parser.add_argument(
        "--show-reasoning", 
        action="store_true", 
        help="Show detailed reasoning from each agent"
    )
    
    args = parser.parse_args()
    
    # Parse inputs
    selected_analysts = [a.strip() for a in args.analysts.split(",") if a.strip()]
    specific_tickers = [t.strip() for t in args.tickers.split(",")] if args.tickers else None
    
    try:
        # Fetch current Alpaca state
        print("📡 Fetching Alpaca account and positions...")
        account = get_alpaca_account()
        positions_raw = get_alpaca_positions()
        
        if not args.telegram:
            print(f"✅ Found {len(positions_raw)} positions")
            print(format_positions_summary(positions_raw, account))
            print()
        
        # Determine analysis universe
        if specific_tickers:
            tickers_to_analyze = specific_tickers
            portfolio = convert_to_portfolio(positions_raw, account, specific_tickers)
        else:
            # Analyze holdings, optionally limited to top N by market value
            held = [p for p in positions_raw if float(p.get("qty", 0)) != 0]
            held.sort(key=lambda p: abs(float(p.get("market_value", 0))), reverse=True)
            if args.top and args.top > 0:
                held = held[:args.top]
            held_tickers = [p["symbol"] for p in held]
            tickers_to_analyze = held_tickers
            portfolio = convert_to_portfolio(positions_raw, account)
        
        if not tickers_to_analyze:
            print("❌ No tickers to analyze. Either specify --tickers or hold some positions.")
            return 1
            
        print(f"🔍 Analyzing {len(tickers_to_analyze)} ticker(s): {', '.join(tickers_to_analyze)}")
        print(f"🤖 Using analysts: {', '.join(selected_analysts)}")
        print(f"🧠 Model: {args.model} (Ollama)")
        print(f"💼 Mode: {'LIVE TRADING' if args.execute else 'DRY RUN'}")
        print()
        
        # Run the AI hedge fund analysis
        result = run_hedge_fund(
            tickers=tickers_to_analyze,
            start_date=(datetime.now().replace(day=1)).strftime("%Y-%m-%d"),  # Start of month
            end_date=datetime.now().strftime("%Y-%m-%d"),
            portfolio=portfolio,
            show_reasoning=args.show_reasoning,
            selected_analysts=selected_analysts,
            model_name=args.model,
            model_provider=args.provider,
        )
        
        decisions = result.get("decisions", {})
        analyst_signals = result.get("analyst_signals", {})
        
        if not decisions:
            print("❌ No trading decisions generated")
            return 1
            
        # Execute or preview trades
        trade_results = execute_decisions(
            decisions=decisions,
            positions_raw=positions_raw,
            account=account,
            dry_run=not args.execute,
        )
        
        # Format output
        if args.telegram:
            output_telegram_summary(decisions, trade_results, args.execute)
        else:
            output_detailed_summary(decisions, analyst_signals, trade_results, args.execute)
            
    except Exception as e:
        print(f"❌ Error: {e}")
        return 1
        
    return 0


def output_detailed_summary(decisions, analyst_signals, trade_results, executed):
    """Output detailed analysis and trade results."""
    print("=" * 80)
    print("🎯 TRADING DECISIONS")
    print("=" * 80)
    
    for ticker, decision in decisions.items():
        action = decision.get("action", "hold")
        qty = decision.get("quantity", 0)
        confidence = decision.get("confidence", 0)
        reasoning = decision.get("reasoning", "")
        
        print(f"\n📊 {ticker}")
        print(f"   Action: {action.upper()}")
        print(f"   Quantity: {qty}")
        print(f"   Confidence: {confidence:.1f}%")
        print(f"   Reasoning: {reasoning}")
    
    print("\n" + "=" * 80)
    print("📋 TRADE RESULTS")
    print("=" * 80)
    
    successful_trades = 0
    failed_trades = 0
    
    for result in trade_results:
        ticker = result["ticker"]
        action = result["action"]
        qty = result["qty"]
        success = result.get("success", False)
        
        if result.get("skipped"):
            print(f"⏸️  {ticker}: SKIPPED - {result.get('reason', '')}")
        elif success:
            successful_trades += 1
            if result.get("dry_run"):
                print(f"✅ {ticker}: WOULD {action.upper()} {qty} shares (DRY RUN)")
            else:
                order_id = result.get("order_id", "")
                print(f"✅ {ticker}: {action.upper()} {qty} shares (Order: {order_id})")
        else:
            failed_trades += 1
            reason = result.get("reason", "Unknown error")
            print(f"❌ {ticker}: FAILED - {reason}")
    
    print(f"\nSummary: {successful_trades} successful, {failed_trades} failed")


def output_telegram_summary(decisions, trade_results, executed):
    """Output Telegram-friendly summary (no markdown tables)."""
    print("🤖 Mordecai AI Hedge Fund Analysis")
    print(f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M PST')}")
    print()
    
    # Count signals
    signals_count = {"bullish": 0, "bearish": 0, "neutral": 0}
    trades_count = {"buy": 0, "sell": 0, "hold": 0}
    
    for decision in decisions.values():
        signal = decision.get("signal", "neutral").lower()
        action = decision.get("action", "hold").lower()
        signals_count[signal] = signals_count.get(signal, 0) + 1
        trades_count[action] = trades_count.get(action, 0) + 1
    
    print(f"📈 Signals: {signals_count['bullish']} Bullish, {signals_count['bearish']} Bearish, {signals_count['neutral']} Neutral")
    print(f"💼 Actions: {trades_count.get('buy', 0)} Buy, {trades_count.get('sell', 0)} Sell, {trades_count.get('hold', 0)} Hold")
    print()
    
    # Show only actionable trades
    actionable_trades = [
        (ticker, decision) for ticker, decision in decisions.items()
        if decision.get("action", "hold") != "hold" and decision.get("quantity", 0) > 0
    ]
    
    if actionable_trades:
        print("🎯 Trade Recommendations:")
        for ticker, decision in actionable_trades:
            action = decision["action"].upper()
            qty = decision["quantity"]
            confidence = decision["confidence"]
            print(f"  • {ticker}: {action} {qty} shares ({confidence:.0f}% confidence)")
    else:
        print("🎯 No trades recommended (all hold)")
    
    # Show execution results
    executed_count = len([r for r in trade_results if r.get("success") and not r.get("skipped")])
    failed_count = len([r for r in trade_results if not r.get("success") and not r.get("skipped")])
    
    if executed:
        print(f"\n✅ Executed: {executed_count} trades, {failed_count} failed")
    else:
        print(f"\n🧪 DRY RUN: {executed_count} trades would execute, {failed_count} blocked by safety rails")


if __name__ == "__main__":
    sys.exit(main())