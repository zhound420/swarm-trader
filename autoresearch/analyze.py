#!/usr/bin/env python3
"""
analyze.py — Cross-run analytics for autoresearch sessions.

Reads experiments/runs.jsonl (per-session summaries) and experiments/log.jsonl
(per-experiment detail) and prints a multi-section performance report.

Usage:
    poetry run python autoresearch/analyze.py              # full report
    poetry run python autoresearch/analyze.py --mode day   # filter by mode
    poetry run python autoresearch/analyze.py --runs 5     # last N runs only
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

AUTORESEARCH_DIR = Path(__file__).parent
EXPERIMENTS_DIR = AUTORESEARCH_DIR / "experiments"
RUNS_LOG_PATH = EXPERIMENTS_DIR / "runs.jsonl"
LOG_PATH = EXPERIMENTS_DIR / "log.jsonl"


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def _load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    records = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return records


# ---------------------------------------------------------------------------
# ASCII sparkline
# ---------------------------------------------------------------------------

_SPARK_CHARS = "▁▂▃▄▅▆▇█"


def _sparkline(values: list[float]) -> str:
    if not values:
        return ""
    mn, mx = min(values), max(values)
    span = mx - mn
    if span == 0:
        return _SPARK_CHARS[4] * len(values)
    chars = []
    for v in values:
        bucket = int((v - mn) / span * (len(_SPARK_CHARS) - 1))
        chars.append(_SPARK_CHARS[bucket])
    return "".join(chars)


# ---------------------------------------------------------------------------
# Report sections
# ---------------------------------------------------------------------------

def _section_run_history(runs: list[dict]) -> None:
    print("\n" + "=" * 80)
    print("RUN HISTORY")
    print("=" * 80)
    header = f"{'DATE':<12} {'MODE':<6} {'ITERS':<8} {'BASELINE':<10} {'BEST':<10} {'ΔFITNESS':<10} {'KEEP%':<7} {'REASON'}"
    print(header)
    print("-" * 75)
    for r in runs:
        ts = r.get("timestamp_start", "")[:10]
        mode = r.get("mode", "?")
        iters_req = r.get("iterations_requested", 0)
        iters_done = r.get("iterations_completed", 0)
        baseline = r.get("baseline_fitness", 0.0)
        best = r.get("best_fitness", 0.0)
        delta = r.get("improvement", best - baseline)
        total = r.get("total_experiments", 0)
        keep_count = r.get("keep_count", 0)
        keep_pct = f"{int(keep_count / total * 100)}%" if total else "0%"
        reason = r.get("stop_reason", "?")
        print(
            f"{ts:<12} {mode:<6} {iters_done}/{iters_req:<5} "
            f"{baseline:<10.3f} {best:<10.3f} {delta:+<10.3f} {keep_pct:<7} {reason}"
        )


def _section_fitness_trajectory(runs: list[dict], mode_filter: str | None) -> None:
    print("\n" + "=" * 80)
    filtered = [r for r in runs if mode_filter is None or r.get("mode") == mode_filter]
    label = f"day mode" if mode_filter == "day" else (f"{mode_filter} mode" if mode_filter else "all modes")
    print(f"FITNESS TRAJECTORY ({label})")
    print("=" * 80)

    if not filtered:
        print("  No data.")
        return

    best_vals = [r.get("best_fitness", 0.0) for r in filtered]
    spark = _sparkline(best_vals)
    first, last = best_vals[0], best_vals[-1]

    if len(best_vals) > 1:
        deltas = [best_vals[i] - best_vals[i - 1] for i in range(1, len(best_vals))]
        avg_delta = sum(deltas) / len(deltas)
        trend_str = f"trend: {avg_delta:+.3f}/run avg"
    else:
        trend_str = "only 1 run"

    print(f"  Best fitness per run: {first:.3f} ──{spark}──  → {last:.3f}   ({trend_str})")

    baselines = [r.get("baseline_fitness", 0.0) for r in filtered]
    improvements = [r.get("improvement", 0.0) for r in filtered]
    print(f"  Improvement per run:  min={min(improvements):+.3f}  max={max(improvements):+.3f}  "
          f"avg={sum(improvements)/len(improvements):+.3f}")
    print(f"  Baseline range:       {min(baselines):.3f} → {max(baselines):.3f}")


def _section_current_strategy(experiments: list[dict]) -> None:
    print("\n" + "=" * 80)
    print("CURRENT STRATEGY (most recent kept experiment)")
    print("=" * 80)

    kept = [e for e in experiments if e.get("kept")]
    if not kept:
        print("  No kept experiments found.")
        return

    best = max(kept, key=lambda e: e.get("fitness_score") or -999)
    m = best.get("metrics", {})
    fitness = best.get("fitness_score", 0.0)
    sharpe = m.get("sharpe_ratio", "N/A")
    ret = m.get("total_return_pct", "N/A")
    wr = m.get("win_rate", "N/A")
    trades = m.get("num_trades", "N/A")
    dd = m.get("max_drawdown_pct", "N/A")
    hyp = best.get("hypothesis", "N/A")

    print(f"  fitness={fitness:.3f}  sharpe={sharpe}  return={ret}%  "
          f"win_rate={wr}  trades={trades}  drawdown={dd}%")
    print(f"  hypothesis: {hyp}")


def _section_hypothesis_frequency(experiments: list[dict]) -> None:
    print("\n" + "=" * 80)
    print("HYPOTHESIS FREQUENCY (top tested, from log.jsonl)")
    print("=" * 80)

    # Keyword buckets — order matters (first match wins)
    buckets: list[tuple[str, list[str]]] = [
        ("MACD parameters",       ["macd", "ema", "macd_fast", "macd_slow"]),
        ("MIN_CONFIDENCE",        ["min_confidence", "confidence threshold"]),
        ("STOP_PCT / TARGET",     ["stop_pct", "target_multiplier", "stop loss", "r:r"]),
        ("VWAP bands",            ["vwap", "vwap_near_band"]),
        ("RSI thresholds",        ["rsi", "rsi_oversold", "rsi_overbought"]),
        ("Regime multipliers",    ["regime", "regime_multiplier"]),
        ("Time filters",          ["no_trade_open", "market_close", "time filter"]),
        ("Volume / acceleration", ["volume", "vol_accel"]),
        ("Confidence weights",    ["conf_weight", "confidence weight"]),
        ("Trailing stop",        ["trailing stop"]),
    ]

    counts: dict[str, list[int]] = {name: [0, 0] for name, _ in buckets}
    unmatched_tried = 0
    unmatched_kept = 0

    for exp in experiments:
        hyp = (exp.get("hypothesis") or "").lower()
        if not hyp or hyp in ("unknown", "agent_error", "syntax_error"):
            continue
        matched = False
        for name, keywords in buckets:
            if any(kw in hyp for kw in keywords):
                counts[name][0] += 1
                if exp.get("kept"):
                    counts[name][1] += 1
                matched = True
                break
        if not matched:
            unmatched_tried += 1
            if exp.get("kept"):
                unmatched_kept += 1

    # Sort by tried count descending
    sorted_counts = sorted(counts.items(), key=lambda x: x[1][0], reverse=True)
    for name, (tried, kept) in sorted_counts:
        if tried > 0:
            print(f"  {name:<28} {tried:>3}x tried, {kept}x kept")
    if unmatched_tried > 0:
        print(f"  {'(other)':<28} {unmatched_tried:>3}x tried, {unmatched_kept}x kept")


def _section_diminishing_returns(runs: list[dict], mode_filter: str | None) -> None:
    filtered = [r for r in runs if mode_filter is None or r.get("mode") == mode_filter]
    if len(filtered) < 3:
        return

    last3 = [r.get("improvement", 0.0) for r in filtered[-3:]]
    if all(d < 0.5 for d in last3):
        print("\n" + "=" * 80)
        print("[SIGNAL] DIMINISHING RETURNS")
        print("=" * 80)
        arrow = " → ".join(f"{d:+.2f}" for d in last3)
        print(f"  Improvement trending down: {arrow}. Consider refreshing data window.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Cross-run autoresearch analytics")
    parser.add_argument("--mode", type=str, default=None, help="Filter by mode (day|swing)")
    parser.add_argument("--runs", type=int, default=None, help="Limit to last N runs")
    args = parser.parse_args()

    runs = _load_jsonl(RUNS_LOG_PATH)
    if not runs:
        print(f"No runs found at {RUNS_LOG_PATH}", file=sys.stderr)
        print("Run evolve.py at least once to generate run summaries.", file=sys.stderr)
        return 1

    if args.mode:
        runs = [r for r in runs if r.get("mode") == args.mode]
    if args.runs:
        runs = runs[-args.runs:]

    experiments = _load_jsonl(LOG_PATH)

    _section_run_history(runs)
    _section_fitness_trajectory(runs, args.mode)
    _section_current_strategy(experiments)
    _section_hypothesis_frequency(experiments)
    _section_diminishing_returns(runs, args.mode)

    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
