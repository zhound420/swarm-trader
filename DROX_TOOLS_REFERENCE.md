# DROX TOOLS Reference (from Glorft via AgentMail, 2026-03-11)

Kenny's trading agent. Uses the same swarm-trader codebase but different workspace path.

## Key Differences from Cassius

- **Workspace:** `~/.openclaw/workspace/swarm-trader/` (DROX) vs `~/clawd/projects/swarm-trader/` (Cassius)
- **Order types:** DROX supports stop, oco, trailing_stop IN ADDITION to market, limit, bracket
- **Trade format:** DROX uses `execute_decisions()` dict format: `{"NVDA": {"action": "buy", ...}}`
  Cassius uses `execute_trades.py` list format: `{"trades": [{"ticker": "NVDA", ...}]}`
- **Custom agent:** Both use `apex` analyst

## DROX Order Types (superset of ours)

| Type | Use Case |
|------|----------|
| market | Immediate fill (default) |
| limit | Entry at specific price |
| bracket | Entry + stop-loss + take-profit (atomic) |
| stop | Standalone stop-loss on EXISTING position only |
| oco | Exit-only: stop + take-profit on existing position |
| trailing_stop | Stop that rises with price, locks in gains |

## Intel Exchange Notes

- `intel_exchange.py` shares signals/anomalies only — no positions or portfolio data
- Format compatibility is fine since intel is signal-level, not trade-level
- DROX uses same analyst IDs (warren_buffett, apex, technical_analyst, etc.)
