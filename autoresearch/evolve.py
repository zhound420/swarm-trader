#!/usr/bin/env python3
"""
evolve.py — Autonomous trading strategy evolution loop.

Orchestrates the autoresearch loop:
  1. Snapshot strategy.py
  2. Spawn coding agent (claude CLI) with program.md + experiment log + context
  3. Agent modifies strategy.py
  4. Syntax check → run backtest_fast.py
  5. Compare fitness → keep or revert
  6. Log result → git commit if kept
  7. Repeat until --iterations reached or abort conditions met

Usage:
    poetry run python autoresearch/evolve.py
    poetry run python autoresearch/evolve.py --iterations 50 --backtest-days 10
    poetry run python autoresearch/evolve.py --iterations 25 --agent claude --quiet
    poetry run python autoresearch/evolve.py --dry-run   # No agent calls, test harness only

Abort conditions:
    - 5 consecutive backtest failures
    - 3 consecutive syntax errors in agent output
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import textwrap
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).parent.parent
AUTORESEARCH_DIR = Path(__file__).parent
STRATEGY_PATH = AUTORESEARCH_DIR / "strategy.py"
STRATEGY_BACKUP_PATH = AUTORESEARCH_DIR / "strategy_backup.py"
EXPERIMENTS_DIR = AUTORESEARCH_DIR / "experiments"
LOG_PATH = EXPERIMENTS_DIR / "log.jsonl"
RUNS_LOG_PATH = EXPERIMENTS_DIR / "runs.jsonl"
PROGRAM_MD_PATH = AUTORESEARCH_DIR / "program.md"
BACKTEST_SCRIPT = AUTORESEARCH_DIR / "backtest_fast.py"

EXPERIMENTS_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Abort thresholds
# ---------------------------------------------------------------------------
MAX_CONSECUTIVE_FAILURES = 5     # backtest errors / non-timeout agent errors
MAX_CONSECUTIVE_TIMEOUTS = 4     # agent timeouts specifically
MAX_CONSECUTIVE_SYNTAX_ERRORS = 3
BACKTEST_TIMEOUT_SEC = 300       # 5 min per backtest run
AGENT_TIMEOUT_SEC = 420          # 7 min per iteration (5 min was too tight, ~40% failure rate)
PLATEAU_ITERATIONS = 15          # stop early if no improvement in this many consecutive iterations
OOS_FITNESS_FLOOR = -5.0         # reject keeper if OOS fitness drops below this
OOS_BACKTEST_DAYS = 20           # wider window for out-of-sample check


# ---------------------------------------------------------------------------
# Experiment log
# ---------------------------------------------------------------------------

def _load_recent_experiments(n: int = 10) -> list[dict]:
    """Load the last N experiments from log.jsonl."""
    if not LOG_PATH.exists():
        return []
    experiments = []
    with open(LOG_PATH) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    experiments.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return experiments[-n:]


def _append_experiment(record: dict) -> None:
    """Append an experiment record to log.jsonl."""
    with open(LOG_PATH, "a") as f:
        f.write(json.dumps(record, default=str) + "\n")


def _write_run_summary(record: dict) -> None:
    """Append a run summary record to runs.jsonl."""
    with open(RUNS_LOG_PATH, "a") as f:
        f.write(json.dumps(record, default=str) + "\n")


def _format_experiments_for_prompt(experiments: list[dict]) -> str:
    """Format experiment history for inclusion in the agent prompt."""
    if not experiments:
        return "No experiments yet — this is the first iteration."

    lines = []
    for exp in experiments:
        kept_str = "✓ KEPT" if exp.get("kept") else "✗ REVERTED"
        fitness = exp.get("fitness_score", "N/A")
        hypothesis = exp.get("hypothesis", "unknown")
        metrics = exp.get("metrics", {})
        ret = metrics.get("total_return_pct", "N/A")
        sharpe = metrics.get("sharpe_ratio", "N/A")
        win_rate = metrics.get("win_rate", "N/A")
        num_trades = metrics.get("num_trades", "N/A")
        error = exp.get("error", "")

        if isinstance(fitness, float):
            fitness_str = f"{fitness:.4f}"
        else:
            fitness_str = str(fitness)

        line = f"  [{kept_str}] id={exp.get('experiment_id','?')[:8]} fitness={fitness_str}"
        if not exp.get("kept") and error:
            line += f" error={error[:80]}"
        elif metrics:
            line += f" ret={ret} sharpe={sharpe} wr={win_rate} trades={num_trades}"
        line += f"\n    hypothesis: {hypothesis}"
        lines.append(line)

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Agent invocation
# ---------------------------------------------------------------------------

def _build_agent_prompt(
    iteration: int,
    total_iterations: int,
    current_fitness: float | None,
    baseline_fitness: float,
    recent_experiments: list[dict],
    backtest_days: int,
) -> str:
    """Build the full prompt for the coding agent."""
    exp_history = _format_experiments_for_prompt(recent_experiments)

    current_fitness_str = f"{current_fitness:.4f}" if current_fitness is not None else "not yet measured"
    baseline_str = f"{baseline_fitness:.4f}"

    prompt = textwrap.dedent(f"""\
        You are an autonomous trading strategy researcher. Your ONLY job is to modify `autoresearch/strategy.py` to improve its backtest fitness score.

        ## Current Status
        - Iteration: {iteration} of {total_iterations}
        - Baseline fitness: {baseline_str}
        - Current best fitness: {current_fitness_str}
        - Backtest window: last {backtest_days} trading days

        ## Your Task
        1. Read `autoresearch/program.md` — it contains your full instructions and the fitness formula
        2. Read `autoresearch/strategy.py` — this is the file you will modify
        3. Review the experiment history below — don't repeat failed experiments
        4. Form ONE clear hypothesis about what change should improve fitness
        5. Make exactly ONE focused change to `autoresearch/strategy.py`
        6. Update the comment block at the top of strategy.py:
           ```
           # EXPERIMENT: <short_name>
           # HYPOTHESIS: <why this change should help>
           # CHANGE: <what you modified>
           ```

        ## Experiment History (most recent {len(recent_experiments)} experiments)
        {exp_history}

        ## Rules (CRITICAL — violations will cause the experiment to be reverted)
        - Modify ONLY `autoresearch/strategy.py` — do not touch any other file
        - The strategy must be pure Python — no LLM calls, no network calls, no randomness
        - Every Signal must have a valid stop_price and target_price
        - The `generate_signals(bars_df, market_context)` function signature must be preserved
        - The `Signal` dataclass fields must be preserved (ticker, direction, confidence, entry_price, stop_price, target_price, reasoning)
        - Make ONE change — compound changes are hard to learn from
        - The code must be syntactically valid Python

        ## Fitness Formula (higher is better)
        fitness = (sharpe * 0.35) + (sortino * 0.25) + (total_return_pct * 0.20) + (win_rate * 0.10) + (profit_factor * 0.10)
        Penalties: drawdown>15% → -20, win_rate<30% → -10, trades<10 → -15, trades>200 → -5

        ## Ideas to Consider (if not already tried)
        - Tighten/loosen RSI thresholds (RSI_OVERSOLD, RSI_OVERBOUGHT)
        - Adjust VWAP deviation bands (VWAP_NEAR_BAND_PCT)
        - Change stop loss % (STOP_PCT) or R:R ratio (TARGET_MULTIPLIER)
        - Modify minimum confidence threshold (MIN_CONFIDENCE)
        - Change confidence component weights (CONF_WEIGHT_*)
        - Adjust time filters (NO_TRADE_OPEN_MINUTES, MARKET_CLOSE_CUTOFF)
        - Modify regime multipliers (REGIME_MULTIPLIER dict)
        - Add trailing stop logic
        - Change MACD periods
        - Add volume acceleration signal

        Make your change now.
    """)
    return prompt


def _run_agent_claude(prompt: str, quiet: bool = False) -> tuple[bool, str]:
    """
    Invoke claude CLI to modify strategy.py.
    Returns (success, output_or_error).
    """
    if not quiet:
        print("  [agent] Invoking claude...", file=sys.stderr)

    cmd = [
        "claude",
        "--print",
        "--model", "claude-sonnet-4-20250514",  # Pin Sonnet — prevents accidental Opus billing
        "--allowedTools", "Read,Edit,Bash",
        "-p", prompt,
    ]

    # Strip CLAUDECODE so the child claude process doesn't see a nested session
    child_env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}

    try:
        result = subprocess.run(
            cmd,
            cwd=str(REPO_ROOT),
            env=child_env,
            capture_output=True,
            text=True,
            timeout=AGENT_TIMEOUT_SEC,
        )
        if result.returncode != 0:
            err = result.stderr.strip() or result.stdout.strip()
            return False, f"claude exited with code {result.returncode}: {err[:200]}"
        return True, result.stdout.strip()
    except subprocess.TimeoutExpired:
        return False, f"Agent timed out after {AGENT_TIMEOUT_SEC}s"
    except FileNotFoundError:
        return False, "claude CLI not found in PATH. Install it or use --agent api or --agent dry-run."
    except Exception as e:
        return False, f"Agent error: {e}"


def _run_agent_api(prompt: str, quiet: bool = False) -> tuple[bool, str]:
    """
    Invoke Claude via Anthropic Python SDK to modify strategy.py.
    Avoids nested Claude session errors (no subprocess call to claude CLI).
    Returns (success, output_or_error).
    """
    try:
        import anthropic
    except ImportError:
        return False, "anthropic package not installed. Run: pip install anthropic"

    if not quiet:
        print("  [agent] Invoking Claude API (no nested session)...", file=sys.stderr)

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return False, "ANTHROPIC_API_KEY not set in environment"

    try:
        strategy_content = STRATEGY_PATH.read_text()
        program_md_content = PROGRAM_MD_PATH.read_text() if PROGRAM_MD_PATH.exists() else ""
    except Exception as e:
        return False, f"Failed to read input files: {e}"

    full_prompt = (
        prompt
        + f"\n\n## program.md (your full instructions)\n{program_md_content}"
        + f"\n\n## Current autoresearch/strategy.py\n```python\n{strategy_content}\n```"
        + "\n\n## Response format"
        + "\nOutput ONLY the complete modified strategy.py. No explanation, no markdown fences — raw Python only."
    )

    client = anthropic.Anthropic(api_key=api_key)
    try:
        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=8192,
            messages=[{"role": "user", "content": full_prompt}],
        )
    except Exception as e:
        return False, f"Anthropic API error: {e}"

    new_content = message.content[0].text.strip()

    # Strip markdown code fences if the model added them
    if new_content.startswith("```python"):
        new_content = new_content[len("```python"):].lstrip("\n")
    elif new_content.startswith("```"):
        new_content = new_content[3:].lstrip("\n")
    if new_content.endswith("```"):
        new_content = new_content[:-3].rstrip()

    try:
        STRATEGY_PATH.write_text(new_content)
    except Exception as e:
        return False, f"Failed to write strategy.py: {e}"

    tokens_used = getattr(message.usage, "input_tokens", "?"), getattr(message.usage, "output_tokens", "?")
    return True, f"API agent ok (tokens: {tokens_used[0]}in + {tokens_used[1]}out)"


def _run_agent_dry_run(prompt: str, quiet: bool = False) -> tuple[bool, str]:
    """Dry-run agent: makes a minimal parameter tweak to test the harness."""
    if not quiet:
        print("  [agent] DRY RUN — tweaking MIN_CONFIDENCE by 1...", file=sys.stderr)
    try:
        content = STRATEGY_PATH.read_text()
        # Find current MIN_CONFIDENCE value and tweak it slightly
        import re
        match = re.search(r"^MIN_CONFIDENCE\s*=\s*(\d+(?:\.\d+)?)", content, re.MULTILINE)
        if match:
            old_val = float(match.group(1))
            new_val = round(old_val - 2.0, 1)  # lower threshold = more signals
            new_val = max(40.0, new_val)  # don't go below 40
            content = re.sub(
                r"^MIN_CONFIDENCE\s*=\s*\d+(?:\.\d+)?",
                f"MIN_CONFIDENCE = {new_val}",
                content,
                flags=re.MULTILINE,
            )
            # Update experiment metadata
            content = re.sub(
                r"^EXPERIMENT_NAME\s*=.*",
                f'EXPERIMENT_NAME = "dry_run_{int(old_val)}to{int(new_val)}"',
                content, flags=re.MULTILINE,
            )
            content = re.sub(
                r"^EXPERIMENT_HYPOTHESIS\s*=.*",
                f'EXPERIMENT_HYPOTHESIS = "Lower MIN_CONFIDENCE from {old_val} to {new_val} to generate more signals"',
                content, flags=re.MULTILINE,
            )
            STRATEGY_PATH.write_text(content)
            return True, f"Tweaked MIN_CONFIDENCE: {old_val} → {new_val}"
        return False, "Could not find MIN_CONFIDENCE in strategy.py"
    except Exception as e:
        return False, f"Dry run error: {e}"


AGENTS: dict[str, Any] = {
    "claude": _run_agent_claude,
    "api": _run_agent_api,
    "dry-run": _run_agent_dry_run,
}


# ---------------------------------------------------------------------------
# Syntax check
# ---------------------------------------------------------------------------

def _syntax_check(path: Path) -> tuple[bool, str]:
    """Run py_compile on the strategy file. Returns (ok, error_msg)."""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "py_compile", str(path)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return True, ""
        return False, result.stderr.strip()
    except Exception as e:
        return False, str(e)


# ---------------------------------------------------------------------------
# Backtest runner
# ---------------------------------------------------------------------------

def _run_backtest(backtest_days: int, capital: float, quiet: bool = False, mode: str = "day") -> tuple[bool, dict]:
    """
    Run backtest_fast.py as a subprocess.
    Returns (success, metrics_dict).
    """
    cmd = [
        sys.executable,
        str(BACKTEST_SCRIPT),
        "--days", str(backtest_days),
        "--capital", str(int(capital)),
        "--mode", mode,
    ]
    if quiet:
        cmd.append("--quiet")

    try:
        result = subprocess.run(
            cmd,
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=BACKTEST_TIMEOUT_SEC,
        )
        if not quiet and result.stderr:
            # Print progress from backtest stderr
            for line in result.stderr.splitlines():
                print(f"    {line}", file=sys.stderr)

        if result.returncode != 0:
            return False, {"error": f"backtest exited {result.returncode}: {result.stderr[-300:]}"}

        metrics = json.loads(result.stdout.strip())
        return True, metrics

    except subprocess.TimeoutExpired:
        return False, {"error": f"Backtest timed out after {BACKTEST_TIMEOUT_SEC}s"}
    except json.JSONDecodeError as e:
        return False, {"error": f"Invalid JSON from backtest: {e}"}
    except Exception as e:
        return False, {"error": str(e)}


# ---------------------------------------------------------------------------
# Git integration
# ---------------------------------------------------------------------------

def _git_commit(hypothesis: str, experiment_id: str, fitness: float) -> bool:
    """Commit the current strategy.py with a descriptive message."""
    try:
        subprocess.run(
            ["git", "add", "autoresearch/strategy.py"],
            cwd=str(REPO_ROOT), check=True, capture_output=True,
        )
        msg = (
            f"feat(autoresearch): {hypothesis[:72]}\n\n"
            f"experiment_id={experiment_id} fitness={fitness:.4f}"
        )
        subprocess.run(
            ["git", "commit", "-m", msg],
            cwd=str(REPO_ROOT), check=True, capture_output=True,
        )
        return True
    except subprocess.CalledProcessError:
        return False


# ---------------------------------------------------------------------------
# Summary table printer
# ---------------------------------------------------------------------------

def _print_summary(experiments: list[dict], baseline_fitness: float) -> None:
    """Print final summary table of all experiments."""
    print("\n" + "=" * 80)
    print("AUTORESEARCH SUMMARY")
    print("=" * 80)

    kept = [e for e in experiments if e.get("kept")]
    reverted = [e for e in experiments if not e.get("kept")]

    print(f"Total experiments: {len(experiments)}")
    print(f"Kept: {len(kept)}   Reverted: {len(reverted)}")
    print(f"Baseline fitness: {baseline_fitness:.4f}")

    if kept:
        best = max(kept, key=lambda e: e.get("fitness_score", -999))
        print(f"Best fitness:     {best.get('fitness_score', 0):.4f}  "
              f"(id={best.get('experiment_id','?')[:8]})")
        improvement = best.get("fitness_score", 0) - baseline_fitness
        print(f"Improvement:      {improvement:+.4f}")

    print("\n{:<10} {:<12} {:<8} {:<8} {:<8} {:<8} {:<50}".format(
        "ID", "FITNESS", "KEPT", "RETURN%", "SHARPE", "TRADES", "HYPOTHESIS"
    ))
    print("-" * 100)

    for exp in experiments:
        eid = exp.get("experiment_id", "?")[:8]
        fitness = exp.get("fitness_score", 0)
        kept_str = "YES" if exp.get("kept") else "NO"
        metrics = exp.get("metrics", {})
        ret = metrics.get("total_return_pct", 0)
        sharpe = metrics.get("sharpe_ratio", 0)
        trades = metrics.get("num_trades", 0)
        hyp = exp.get("hypothesis", "?")[:48]
        print(f"{eid:<10} {fitness:<12.4f} {kept_str:<8} {ret:<8.2f} {sharpe:<8.3f} {trades:<8} {hyp}")

    print("=" * 80)


# ---------------------------------------------------------------------------
# Main evolution loop
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Autonomous trading strategy evolution loop"
    )
    parser.add_argument(
        "--mode", type=str, default="day", choices=["day", "swing"],
        help="Trading mode to evolve: day (intraday, default) or swing (daily bars, multi-day)",
    )
    parser.add_argument(
        "--iterations", type=int, default=50,
        help="Number of evolution iterations (default: 50)",
    )
    parser.add_argument(
        "--agent", type=str, default="claude",
        choices=["claude", "api", "dry-run"],
        help="Coding agent to use: 'claude' (CLI), 'api' (Anthropic SDK, no nested session), 'dry-run'",
    )
    parser.add_argument(
        "--backtest-days", type=int, default=None,
        help="Trading days per backtest (default: 10 for day, 30 for swing)",
    )
    parser.add_argument(
        "--capital", type=float, default=100_000.0,
        help="Starting capital (default: 100000)",
    )
    parser.add_argument(
        "--quiet", action="store_true",
        help="Suppress verbose progress output",
    )
    args = parser.parse_args()

    # Default backtest days depends on mode
    if args.backtest_days is None:
        args.backtest_days = 30 if args.mode == "swing" else 10

    agent_fn = AGENTS.get(args.agent)
    if agent_fn is None:
        print(f"ERROR: Unknown agent '{args.agent}'. Choose from: {list(AGENTS)}", file=sys.stderr)
        return 1

    print(f"\n{'='*60}", file=sys.stderr)
    print(f"AUTORESEARCH — Trading Strategy Evolution", file=sys.stderr)
    print(f"{'='*60}", file=sys.stderr)
    print(f"Mode:           {args.mode}", file=sys.stderr)
    print(f"Agent:          {args.agent}", file=sys.stderr)
    print(f"Iterations:     {args.iterations}", file=sys.stderr)
    print(f"Backtest days:  {args.backtest_days}", file=sys.stderr)
    print(f"Capital:        ${args.capital:,.0f}", file=sys.stderr)
    print(f"Strategy:       {STRATEGY_PATH}", file=sys.stderr)
    print(f"Log:            {LOG_PATH}", file=sys.stderr)
    print(f"{'='*60}\n", file=sys.stderr)

    # --- Baseline backtest ---
    print("[0/baseline] Running baseline backtest...", file=sys.stderr)
    ok, baseline_metrics = _run_backtest(args.backtest_days, args.capital, quiet=args.quiet, mode=args.mode)
    if not ok:
        print(f"ERROR: Baseline backtest failed: {baseline_metrics.get('error')}", file=sys.stderr)
        return 1

    baseline_fitness = baseline_metrics.get("fitness", -999.0)
    best_fitness = baseline_fitness
    print(
        f"[baseline] fitness={baseline_fitness:.4f}  "
        f"return={baseline_metrics.get('total_return_pct',0):+.2f}%  "
        f"sharpe={baseline_metrics.get('sharpe_ratio',0):.2f}  "
        f"trades={baseline_metrics.get('num_trades',0)}",
        file=sys.stderr,
    )

    # --- Evolution state ---
    run_id = str(uuid.uuid4())
    timestamp_start = datetime.utcnow().isoformat() + "Z"
    stop_reason = "completed"
    session_experiments: list[dict] = []
    consecutive_failures = 0
    consecutive_timeouts = 0
    consecutive_syntax_errors = 0
    iterations_since_improvement = 0

    for iteration in range(1, args.iterations + 1):
        print(f"\n[{iteration}/{args.iterations}] Starting experiment...", file=sys.stderr)

        experiment_id = str(uuid.uuid4())
        timestamp = datetime.utcnow().isoformat() + "Z"

        # 1. Snapshot current strategy
        shutil.copy2(STRATEGY_PATH, STRATEGY_BACKUP_PATH)

        # 2. Load recent experiment history
        recent_experiments = _load_recent_experiments(n=20)

        # 3. Build agent prompt
        prompt = _build_agent_prompt(
            iteration=iteration,
            total_iterations=args.iterations,
            current_fitness=best_fitness,
            baseline_fitness=baseline_fitness,
            recent_experiments=recent_experiments,
            backtest_days=args.backtest_days,
        )

        # 4. Invoke agent to modify strategy.py
        agent_ok, agent_output = agent_fn(prompt, quiet=args.quiet)

        if not agent_ok:
            print(f"  [FAIL] Agent error: {agent_output}", file=sys.stderr)
            shutil.copy2(STRATEGY_BACKUP_PATH, STRATEGY_PATH)
            _append_experiment({
                "experiment_id": experiment_id,
                "timestamp": timestamp,
                "iteration": iteration,
                "hypothesis": "agent_error",
                "diff": "",
                "fitness_score": None,
                "metrics": {},
                "kept": False,
                "error": f"agent_error: {agent_output[:200]}",
            })
            session_experiments.append({"experiment_id": experiment_id, "kept": False, "fitness_score": None, "hypothesis": "agent_error", "metrics": {}, "error": agent_output[:100]})
            if "timed out" in agent_output.lower() or "TimeoutExpired" in agent_output:
                consecutive_timeouts += 1
                if consecutive_timeouts >= MAX_CONSECUTIVE_TIMEOUTS:
                    print(f"\n[ABORT] {MAX_CONSECUTIVE_TIMEOUTS} consecutive agent timeouts.", file=sys.stderr)
                    stop_reason = "abort"
                    break
            else:
                consecutive_failures += 1
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    print(f"\n[ABORT] {MAX_CONSECUTIVE_FAILURES} consecutive failures. Stopping.", file=sys.stderr)
                    stop_reason = "abort"
                    break
            continue

        # 5. Syntax check
        syntax_ok, syntax_err = _syntax_check(STRATEGY_PATH)
        if not syntax_ok:
            print(f"  [SYNTAX ERROR] {syntax_err[:200]}", file=sys.stderr)
            shutil.copy2(STRATEGY_BACKUP_PATH, STRATEGY_PATH)
            consecutive_syntax_errors += 1
            _append_experiment({
                "experiment_id": experiment_id,
                "timestamp": timestamp,
                "iteration": iteration,
                "hypothesis": "syntax_error",
                "diff": "",
                "fitness_score": None,
                "metrics": {},
                "kept": False,
                "error": f"syntax_error: {syntax_err[:200]}",
            })
            session_experiments.append({"experiment_id": experiment_id, "kept": False, "fitness_score": None, "hypothesis": "syntax_error", "metrics": {}, "error": syntax_err[:100]})
            if consecutive_syntax_errors >= MAX_CONSECUTIVE_SYNTAX_ERRORS:
                print(f"\n[ABORT] {MAX_CONSECUTIVE_SYNTAX_ERRORS} consecutive syntax errors. Stopping.", file=sys.stderr)
                stop_reason = "abort"
                break
            continue

        consecutive_syntax_errors = 0  # reset on syntax success

        # 6. Extract hypothesis from updated strategy.py
        hypothesis = _extract_hypothesis()
        print(f"  hypothesis: {hypothesis}", file=sys.stderr)

        # 7. Compute diff for logging
        diff = _compute_diff(STRATEGY_BACKUP_PATH, STRATEGY_PATH)

        # 8. Run backtest
        print(f"  [backtest] Running {args.backtest_days}-day {args.mode} backtest...", file=sys.stderr)
        bt_ok, metrics = _run_backtest(args.backtest_days, args.capital, quiet=args.quiet, mode=args.mode)

        if not bt_ok:
            error_msg = metrics.get("error", "unknown error")
            print(f"  [FAIL] Backtest error: {error_msg}", file=sys.stderr)
            shutil.copy2(STRATEGY_BACKUP_PATH, STRATEGY_PATH)
            _append_experiment({
                "experiment_id": experiment_id,
                "timestamp": timestamp,
                "iteration": iteration,
                "hypothesis": hypothesis,
                "diff": diff,
                "fitness_score": None,
                "metrics": {},
                "kept": False,
                "error": f"backtest_error: {error_msg[:200]}",
            })
            session_experiments.append({"experiment_id": experiment_id, "kept": False, "fitness_score": None, "hypothesis": hypothesis, "metrics": {}, "error": error_msg[:100]})
            consecutive_failures += 1
            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                print(f"\n[ABORT] {MAX_CONSECUTIVE_FAILURES} consecutive failures. Stopping.", file=sys.stderr)
                stop_reason = "abort"
                break
            continue

        consecutive_failures = 0   # reset on backtest success
        consecutive_timeouts = 0   # reset on any successful agent call + backtest

        new_fitness = metrics.get("fitness", -999.0)
        fitness_delta = new_fitness - best_fitness

        # 9. Keep or revert (with OOS validation)
        kept = False
        oos_rejection_msg = None

        if new_fitness > best_fitness:
            # OOS validation — test on wider window before committing
            oos_ok, oos_metrics = _run_backtest(OOS_BACKTEST_DAYS, args.capital, quiet=True, mode=args.mode)
            oos_fitness = oos_metrics.get("fitness", -999.0) if oos_ok else -999.0
            if oos_fitness < OOS_FITNESS_FLOOR:
                shutil.copy2(STRATEGY_BACKUP_PATH, STRATEGY_PATH)
                oos_rejection_msg = f"oos_rejected: oos_fitness={oos_fitness:.4f}"
                print(
                    f"  [OOS REJECTED] IS fitness={new_fitness:.4f} but OOS fitness={oos_fitness:.4f} < floor {OOS_FITNESS_FLOOR}",
                    file=sys.stderr,
                )
            else:
                kept = True
                best_fitness = new_fitness
                print(
                    f"  [KEPT] IS={new_fitness:.4f} OOS={oos_fitness:.4f} (Δ{fitness_delta:+.4f})  "
                    f"return={metrics.get('total_return_pct',0):+.2f}%  "
                    f"sharpe={metrics.get('sharpe_ratio',0):.2f}  "
                    f"trades={metrics.get('num_trades',0)}",
                    file=sys.stderr,
                )
                # Git commit
                committed = _git_commit(hypothesis, experiment_id, new_fitness)
                if not args.quiet and committed:
                    print(f"  [git] Committed.", file=sys.stderr)
        else:
            # Revert to backup
            shutil.copy2(STRATEGY_BACKUP_PATH, STRATEGY_PATH)
            print(
                f"  [REVERTED] fitness={new_fitness:.4f} (Δ{fitness_delta:+.4f})  "
                f"best remains {best_fitness:.4f}",
                file=sys.stderr,
            )

        # 10. Log experiment
        record = {
            "experiment_id": experiment_id,
            "timestamp": timestamp,
            "iteration": iteration,
            "mode": args.mode,
            "hypothesis": hypothesis,
            "diff": diff,
            "fitness_score": new_fitness,
            "metrics": metrics,
            "kept": kept,
            "error": oos_rejection_msg,
        }
        _append_experiment(record)
        session_experiments.append({
            "experiment_id": experiment_id,
            "kept": kept,
            "fitness_score": new_fitness,
            "hypothesis": hypothesis,
            "metrics": metrics,
        })

        # 11. Plateau detection — stop early if no improvement for N consecutive iterations
        if kept:
            iterations_since_improvement = 0
        else:
            iterations_since_improvement += 1
            if iterations_since_improvement >= PLATEAU_ITERATIONS:
                print(
                    f"\n[PLATEAU] No improvement in {PLATEAU_ITERATIONS} iterations. Stopping early.",
                    file=sys.stderr,
                )
                stop_reason = "plateau"
                break

    # --- Final summary ---
    all_experiments = _load_recent_experiments(n=1000)
    _print_summary(all_experiments, baseline_fitness)

    print(f"\n[evolve] Best fitness achieved: {best_fitness:.4f}", file=sys.stderr)
    print(f"[evolve] Baseline:              {baseline_fitness:.4f}", file=sys.stderr)
    print(f"[evolve] Improvement:           {best_fitness - baseline_fitness:+.4f}", file=sys.stderr)

    # Clean up backup
    if STRATEGY_BACKUP_PATH.exists():
        STRATEGY_BACKUP_PATH.unlink()

    # --- Write run summary ---
    kept_experiments = [e for e in session_experiments if e.get("kept")]
    top_hypothesis: str | None = None
    if kept_experiments:
        best_kept = max(kept_experiments, key=lambda e: e.get("fitness_score") or -999)
        top_hypothesis = best_kept.get("hypothesis")

    error_count = sum(
        1 for e in session_experiments if e.get("error")
    )
    timeout_count = sum(
        1 for e in session_experiments
        if "timed out" in (e.get("error") or "").lower()
    )

    _write_run_summary({
        "run_id": run_id,
        "timestamp_start": timestamp_start,
        "timestamp_end": datetime.utcnow().isoformat() + "Z",
        "mode": args.mode,
        "iterations_requested": args.iterations,
        "iterations_completed": len(session_experiments),
        "stop_reason": stop_reason,
        "baseline_fitness": baseline_fitness,
        "best_fitness": best_fitness,
        "improvement": best_fitness - baseline_fitness,
        "keep_count": len(kept_experiments),
        "total_experiments": len(session_experiments),
        "error_count": error_count,
        "timeout_count": timeout_count,
        "top_hypothesis": top_hypothesis,
    })

    return 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_hypothesis() -> str:
    """Read EXPERIMENT_HYPOTHESIS from the modified strategy.py."""
    try:
        content = STRATEGY_PATH.read_text()
        for line in content.splitlines():
            if line.startswith("# HYPOTHESIS:"):
                return line[len("# HYPOTHESIS:"):].strip()
            if 'EXPERIMENT_HYPOTHESIS' in line and '=' in line:
                val = line.split("=", 1)[1].strip().strip('"').strip("'")
                return val
    except Exception:
        pass
    return "unknown"


def _compute_diff(old_path: Path, new_path: Path) -> str:
    """Compute unified diff between old and new strategy."""
    try:
        result = subprocess.run(
            ["diff", "-u", str(old_path), str(new_path)],
            capture_output=True,
            text=True,
        )
        diff = result.stdout
        # Truncate very large diffs
        if len(diff) > 4000:
            diff = diff[:4000] + "\n... (truncated)"
        return diff
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    sys.exit(main())
