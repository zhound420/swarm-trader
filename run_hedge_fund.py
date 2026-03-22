#!/usr/bin/env python3
"""
Swarm Trader AI Hedge Fund Runner — Canonical execution script.

Fetches live Alpaca positions, runs multi-agent analysis, and executes trades
via execute_trades.py (which enforces V2 risk rules).

Usage:
  poetry run python run_hedge_fund.py                          # Dry run, all holdings
  poetry run python run_hedge_fund.py --execute                # Actually trade
  poetry run python run_hedge_fund.py --mode swing             # Swing mode (default)
  poetry run python run_hedge_fund.py --mode day               # Day trading mode
  poetry run python run_hedge_fund.py --tickers NVDA,AVGO      # Specific tickers
  poetry run python run_hedge_fund.py --telegram               # Telegram-friendly output
  poetry run python run_hedge_fund.py --model qwen3.5:cloud    # Use different model
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()  # Load .env before any imports that need credentials

from src.alpaca_integration import (
    get_alpaca_account,
    get_alpaca_positions,
    convert_to_portfolio,
    format_positions_summary,
)
from src.config import resolve_mode, get_mode_config
from src.main import run_hedge_fund


def _resolve_openclaw_model(fallback="qwen3.5:397b-cloud"):
    """Read the primary model from ~/.openclaw/openclaw.json (drox agent or defaults)."""
    config_path = Path.home() / ".openclaw" / "openclaw.json"
    try:
        config = json.loads(config_path.read_text())
        agents = config.get("agents", {})
        # Look for drox agent first, then fall back to defaults
        for agent in agents.get("list", []):
            if agent.get("id") == "drox":
                model = agent["model"]["primary"]
                return model.split("/", 1)[-1] if "/" in model else model
        model = agents.get("defaults", {}).get("model", {}).get("primary", "")
        if model:
            return model.split("/", 1)[-1] if "/" in model else model
    except Exception:
        pass
    return fallback


def _decisions_to_trades(decisions: dict) -> dict:
    """Convert run_hedge_fund() decisions dict to execute_trades.py input format."""
    trades = []
    for ticker, decision in decisions.items():
        action = decision.get("action", "hold")
        if action == "hold":
            continue
        qty = int(decision.get("quantity", 0))
        if qty <= 0:
            continue
        trade = {
            "ticker": ticker,
            "action": action,
            "qty": qty,
            "reasoning": decision.get("reasoning", ""),
        }
        # Pass through optional order parameters
        for key in ("order_type", "stop_price", "take_profit", "limit_price",
                    "trail_percent", "entry_price"):
            if decision.get(key) is not None:
                trade[key] = decision[key]
        trades.append(trade)
    return {"trades": trades}


def _execute_via_execute_trades(decisions: dict, mode: str, dry_run: bool) -> list[dict]:
    """Delegate execution to execute_trades.py (which enforces V2 risk rules)."""
    trades_input = _decisions_to_trades(decisions)
    if not trades_input["trades"]:
        return []

    cmd = [sys.executable, str(Path(__file__).parent / "execute_trades.py"), "--mode", mode]
    if dry_run:
        cmd.append("--dry-run")

    result = subprocess.run(
        cmd,
        input=json.dumps(trades_input),
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).parent),
    )

    if result.stderr:
        print(result.stderr, file=sys.stderr, end="")

    if result.returncode != 0 or not result.stdout.strip():
        return []

    try:
        output = json.loads(result.stdout)
        return output.get("results", [])
    except json.JSONDecodeError:
        print(f"⚠️ Could not parse execute_trades.py output", file=sys.stderr)
        return []


def main():
    parser = argparse.ArgumentParser(
        description="Run Swarm Trader AI Hedge Fund analysis and trading",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python run_hedge_fund.py                             # Dry run all holdings (swing)
  python run_hedge_fund.py --execute                   # Execute trades
  python run_hedge_fund.py --mode day --execute        # Day trading mode, execute
  python run_hedge_fund.py --tickers NVDA,AVGO         # Analyze specific tickers
  python run_hedge_fund.py --telegram                  # Telegram-friendly format
  python run_hedge_fund.py --model qwen3.5:cloud       # Use different model
        """
    )

    parser.add_argument("--execute", action="store_true",
                        help="Actually execute trades (default: dry run)")
    parser.add_argument("--mode", choices=["swing", "day"], default=None,
                        help="Trading mode (default: resolved from trading_mode.json / env)")
    parser.add_argument("--tickers", type=str,
                        help="Comma-separated list of specific tickers to analyze")
    parser.add_argument("--telegram", action="store_true",
                        help="Format output for Telegram (no markdown tables)")
    parser.add_argument("--model", type=str, default=None,
                        help="Model to use (default: LLM_MODEL env var or openclaw.json)")
    parser.add_argument("--analysts", type=str,
                        default="warren_buffett,michael_burry,cathie_wood,apex,autoresearch,fundamentals_analyst,technical_analyst",
                        help="Comma-separated list of analysts to use")
    parser.add_argument("--show-reasoning", action="store_true",
                        help="Show detailed reasoning from each agent")

    args = parser.parse_args()

    # Resolve mode and model
    mode = resolve_mode(cli_mode=args.mode)
    mode_config = get_mode_config(mode)
    if args.model is None:
        args.model = os.getenv("LLM_MODEL") or _resolve_openclaw_model()

    selected_analysts = [a.strip() for a in args.analysts.split(",") if a.strip()]
    specific_tickers = [t.strip() for t in args.tickers.split(",")] if args.tickers else None

    try:
        print(f"📡 Fetching Alpaca account and positions... [{mode.upper()} — {mode_config['label']}]")
        account = get_alpaca_account()
        positions_raw = get_alpaca_positions()

        if not args.telegram:
            print(f"✅ Found {len(positions_raw)} positions")
            print(format_positions_summary(positions_raw, account))
            print()

        if specific_tickers:
            tickers_to_analyze = specific_tickers
            portfolio = convert_to_portfolio(positions_raw, account, specific_tickers)
        else:
            held_tickers = [p["symbol"] for p in positions_raw if float(p.get("qty", 0)) != 0]
            tickers_to_analyze = held_tickers
            portfolio = convert_to_portfolio(positions_raw, account)

        if not tickers_to_analyze:
            print("❌ No tickers to analyze. Either specify --tickers or hold some positions.")
            return 1

        print(f"🔍 Analyzing {len(tickers_to_analyze)} ticker(s): {', '.join(tickers_to_analyze)}")
        model_provider = os.getenv("LLM_PROVIDER", "Ollama")
        print(f"🤖 Using analysts: {', '.join(selected_analysts)}")
        print(f"🧠 Model: {args.model} ({model_provider})")
        print(f"💼 Mode: {'LIVE TRADING' if args.execute else 'DRY RUN'}")
        print()

        result = run_hedge_fund(
            tickers=tickers_to_analyze,
            start_date=(datetime.now().replace(day=1)).strftime("%Y-%m-%d"),
            end_date=datetime.now().strftime("%Y-%m-%d"),
            portfolio=portfolio,
            show_reasoning=args.show_reasoning,
            selected_analysts=selected_analysts,
            model_name=args.model,
            model_provider=model_provider,
        )

        decisions = result.get("decisions", {})
        analyst_signals = result.get("analyst_signals", {})

        if not decisions:
            print("❌ No trading decisions generated")
            return 1

        # Route execution through execute_trades.py (V2 risk validation)
        trade_results = _execute_via_execute_trades(
            decisions=decisions,
            mode=mode,
            dry_run=not args.execute,
        )

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
        status = result.get("status", "")
        success = status in ("would_execute", "executed") or result.get("success", False)

        if status == "skipped":
            print(f"⏸️  {ticker}: SKIPPED - {result.get('reason', '')}")
        elif success:
            successful_trades += 1
            if status == "would_execute" or result.get("dry_run"):
                print(f"✅ {ticker}: WOULD {action.upper()} {qty} shares (DRY RUN)")
            else:
                order_id = result.get("order_id", "")
                print(f"✅ {ticker}: {action.upper()} {qty} shares (Order: {order_id})")
        else:
            failed_trades += 1
            reason = result.get("reason", result.get("error", "Unknown error"))
            print(f"❌ {ticker}: FAILED - {reason}")

    print(f"\nSummary: {successful_trades} successful, {failed_trades} failed")


def output_telegram_summary(decisions, trade_results, executed):
    """Output Telegram-friendly summary (no markdown tables)."""
    print("🤖 Swarm Trader AI Hedge Fund Analysis")
    print(f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M PST')}")
    print()

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

    successful = len([r for r in trade_results if r.get("status") in ("would_execute", "executed") or r.get("success")])
    failed = len([r for r in trade_results if r.get("status") in ("blocked", "failed")])

    if executed:
        print(f"\n✅ Executed: {successful} trades, {failed} failed")
    else:
        print(f"\n🧪 DRY RUN: {successful} trades would execute, {failed} blocked by V2 risk rules")


if __name__ == "__main__":
    sys.exit(main())
