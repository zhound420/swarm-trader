# Autoresearch Cron Architecture — Token Optimization Pattern

*How Mordecai restructured the autoresearch cron to eliminate LLM timeouts and cut orchestrator costs by ~99%.*

---

## The Problem

The original setup used a single cron job that:
1. Spun up **Cassius** (Claude Opus) as the orchestrator
2. Cassius received the task prompt, processed it with Opus, then ran `evolve.py`
3. `evolve.py` internally calls `claude --print` (Sonnet) for each iteration
4. **Result: Opus calling Sonnet in a loop** — double LLM billing, and the Opus orchestrator kept timing out before the long-running script finished

```
OLD: Cron → Cassius (Opus) → evolve.py → claude CLI (Sonnet) × 50 iterations
                ↑                              ↑
         Expensive reasoning            Also expensive reasoning
         just to run a shell cmd         (this is the actual work)
```

The Opus call was purely overhead — Cassius was just running a shell command and waiting. No reasoning needed for that part.

---

## The Solution: Split Into Two Crons

### Cron 1: `autoresearch-run` (the worker)

| Field | Value |
|-------|-------|
| **Schedule** | `0 17 * * 1-5` (5 PM PT, weekdays) |
| **Agent** | `cassius` |
| **Model** | `google/gemini-2.5-flash` ← the key change |
| **Timeout** | `10800s` (3 hours) |
| **Delivery** | None (no announcement) |

**Task prompt:**
```
Run the autoresearch evolution loop. Execute this command and wait for it to complete:

cd ~/clawd/projects/swarm-trader && poetry run python autoresearch/evolve.py \
  --iterations 50 --backtest-days 10 --agent claude \
  2>&1 | tee /tmp/autoresearch-latest.log

When the command finishes, save the output. Do NOT summarize — a separate job handles reporting.
```

**Why Flash works here:** The orchestrator agent is literally just executing a shell command and waiting. It doesn't need to reason, analyze, or decide anything. Flash handles "run this command" perfectly at a fraction of the cost.

The *actual* reasoning happens inside `evolve.py`, which calls `claude --print --model claude-sonnet-4-20250514` for each iteration. Sonnet does the strategy analysis and code modification. This billing is separate and unavoidable — it's the real work.

### Cron 2: `autoresearch-report` (the reporter)

| Field | Value |
|-------|-------|
| **Schedule** | `0 20 * * 1-5` (8 PM PT, weekdays — 3hr buffer after run) |
| **Agent** | `cassius` |
| **Model** | `google/gemini-2.5-flash` |
| **Timeout** | `120s` |
| **Delivery** | Announce to Telegram |

**Task prompt:**
```
Read the autoresearch results log and send a summary to Zo.

1. Read /tmp/autoresearch-latest.log
2. If the file doesn't exist or is empty, say 'Autoresearch did not produce output today'
3. Otherwise, summarize concisely:
   - How many experiments ran
   - Best fitness achieved (and vs baseline)
   - Which changes were kept vs reverted
   - Top 2-3 insights or patterns
   - Any abort conditions hit

Keep it tight — this goes to Telegram.
```

**Why this is separate:** Reading a log file and summarizing it is a ~10 second Flash task. No reason to bundle it with the 30-60 min execution job.

---

## The Architecture

```
NEW:
  Cron 1 (5 PM) → Cassius (Flash) → runs evolve.py → claude CLI (Sonnet) × 50
                      ↑                                    ↑
                   Cheap ($0.001)                    The real work
                   Just babysits                     (strategy reasoning)

  Cron 2 (8 PM) → Cassius (Flash) → reads log → summarizes → Telegram
                      ↑
                   Cheap ($0.001)
                   Just reads + formats
```

---

## Key Decisions

### 1. Model Selection Per Layer

| Layer | Model | Why |
|-------|-------|-----|
| Orchestrator (cron agent) | Gemini Flash | Just runs a command, no reasoning needed |
| Strategy mutator (inside evolve.py) | Claude Sonnet 4 | Needs to understand code, reason about trading strategies, write modifications |
| Reporter (summary cron) | Gemini Flash | Reads text, formats summary — trivial |

### 2. Timeout Strategy

- **Old:** Default cron timeout (~20 min) → both Opus AND Sonnet fallback timed out
- **New:** 3-hour timeout on the run cron, 2-min timeout on the report cron
- The run cron needs headroom because 50 iterations × ~1-2 min each = 50-100 min typical

### 3. Internal Agent Timeout

Inside `evolve.py`, each Claude CLI call has its own timeout:
```python
AGENT_TIMEOUT_SEC = 420  # 7 min per iteration (was 3 min, caused 40% failure rate)
```
The old 3-min timeout was too tight for Sonnet to read files + reason + edit. Bumping to 7 min cut agent errors significantly.

### 4. Model Pinning

Inside `evolve.py`, the Claude CLI call explicitly pins the model:
```python
cmd = [
    "claude",
    "--print",
    "--model", "claude-sonnet-4-20250514",  # Pinned — no accidental Opus
    "--allowedTools", "Read,Edit,Bash",
    "-p", prompt,
]
```
Without this, `claude --print` uses whatever the default model is — which could be Opus, silently burning 10x the tokens.

---

## Results

| Metric | Old (Single Opus Cron) | New (Flash + Split) |
|--------|----------------------|---------------------|
| Orchestrator model | Opus ($15/M input) | Flash (~$0.10/M input) |
| Orchestrator cost per run | ~$6-7 (446K tokens) | ~$0.05 |
| Success rate | 0% (always timed out) | 100% |
| Run duration | N/A (never completed) | ~27 min for 40 iterations |
| Agent error rate (inner) | N/A | ~10% (down from 40% after timeout bump) |

---

## How to Replicate

### Step 1: Disable the old cron
```bash
openclaw cron disable <old-job-id>
```

### Step 2: Create the run cron
```bash
openclaw cron add \
  --name "autoresearch-run" \
  --cron "0 17 * * 1-5" \
  --tz "America/Los_Angeles" \
  --exact \
  --agent <your-agent-id> \
  --session isolated \
  --model "google/gemini-2.5-flash" \
  --message "Run the autoresearch evolution loop. Execute this command and wait for it to complete:

cd <your-repo-path> && poetry run python autoresearch/evolve.py --iterations 50 --backtest-days 10 --agent claude 2>&1 | tee /tmp/autoresearch-latest.log

When the command finishes, save the output. Do NOT summarize — a separate job handles reporting." \
  --timeout-seconds 10800 \
  --no-deliver
```

### Step 3: Create the report cron
```bash
openclaw cron add \
  --name "autoresearch-report" \
  --cron "0 20 * * 1-5" \
  --tz "America/Los_Angeles" \
  --exact \
  --agent <your-agent-id> \
  --session isolated \
  --model "google/gemini-2.5-flash" \
  --message "Read /tmp/autoresearch-latest.log and summarize: experiments ran, best fitness, changes kept vs reverted, top insights. Keep it concise." \
  --timeout-seconds 120 \
  --announce \
  --channel telegram \
  --to <chat-id>
```

### Step 4: Pin the model inside evolve.py
Add `--model claude-sonnet-4-20250514` to the Claude CLI command in `_run_agent_claude()`.

### Step 5: Bump the internal timeout
Set `AGENT_TIMEOUT_SEC = 420` (7 min) in `evolve.py`.

---

## General Principle

**Use the cheapest model that can do the job at each layer:**

- **Shell command execution** → Flash (or any cheap model)
- **File reading + summarization** → Flash
- **Code reasoning + strategy analysis** → Sonnet
- **Architecture decisions + complex reasoning** → Opus

Don't pay Opus prices to run `subprocess.run()`. That's the entire lesson.
