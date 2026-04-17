# Quickstart: Deal Pipeline Monitoring Agent

Three commands from a fresh clone to a running demo.

## Prerequisites

- Python 3.11 or newer (`python --version`).
- `uv` installed (`pip install uv` or see https://docs.astral.sh/uv/).
- An Anthropic API key exported as `ANTHROPIC_API_KEY`.
- No Docker. No Postgres. No Redis. SQLite ships with Python.

## 1. `make setup`

Installs pinned dependencies via `uv`, builds Tailwind CSS once, and initializes both SQLite databases with schema.

```bash
make setup
```

What it does:
- `uv sync` against `pyproject.toml` (FastAPI, LangGraph, langgraph-checkpoint-sqlite, Anthropic SDK, Pydantic v2, APScheduler, Jinja2, structlog, PyYAML, pytest).
- `npx tailwindcss -i src/hiive_monitor/web/static/input.css -o src/hiive_monitor/web/static/app.css --minify` (single CLI build).
- `python -m hiive_monitor.db.init` creates `domain.db` and `agent_checkpoints.db` with full schema.

Exit criteria: both `.db` files exist; `uv run pytest tests/smoke` passes.

## 2. `make demo`

Seeds the pipeline, starts the FastAPI app, opens the browser on the Daily Brief, and fast-forwards the first few simulated ticks so the reviewer sees agent reasoning immediately without waiting.

```bash
make demo
```

What it does:
- `python -m hiive_monitor.seed` generates 30 deals across the 10 issuers with the five engineered scenarios from BUILD_PLAN §6.3.
- Starts `uvicorn hiive_monitor.app:app` with `CLOCK_MODE=simulated`.
- Runs three warm-up ticks (simulated day 1, 2, 3) so the Daily Brief is populated before the browser opens.
- Opens `http://localhost:8000/brief` in the default browser.

Exit criteria: browser lands on a Daily Brief with ≥5 ranked items, each with visible severity, one-line summary, expandable reasoning, and a drafted intervention.

From the UI you can:
- Approve / edit / dismiss any drafted intervention (copy-to-clipboard on approve).
- Drill into a deal via its row → per-deal timeline with every observation's full reasoning.
- Advance the simulated clock by N days via the controls in the header; the brief auto-refreshes.
- Append `?debug=1` to any URL for the raw structured-log view.

Real-time mode (optional): `CLOCK_MODE=real_time make demo` uses a 60-second wall-clock tick cadence and `hx-trigger="every 30s"` polling on the brief.

## 3. `make eval`

Runs the 15 Tier-1 golden scenarios and prints the scorecard.

```bash
make eval
```

What it does:
- `python -m hiive_monitor.eval.run --all` iterates `src/hiive_monitor/eval/scenarios/*.yaml`.
- For each scenario: truncates `domain.db`, applies setup, sets `SimulatedClock`, runs one Monitor tick, evaluates assertions.
- Writes `out/scorecard.json` and prints a human-readable summary:
  - pass/fail per scenario,
  - precision/recall per risk dimension,
  - severity confusion matrix (4×4),
  - aggregate pass rate.

Exit criteria: at least 13/15 scenarios pass (SC-006 target). Failure output names the specific assertion that failed per scenario.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `ANTHROPIC_API_KEY not set` on demo start | `export ANTHROPIC_API_KEY=sk-...` |
| Port 8000 in use | `PORT=8001 make demo` |
| Want a clean slate | `make clean` removes both DBs and `out/`, then re-run `make setup` |
| Tailwind classes missing | Re-run `make setup` (Tailwind CLI build is cached) |
| Eval scenario fails with "drifted reasoning" | Inspect `out/scorecard.json` for the offending scenario's assertion; the YAML fixture is the source of truth |

## What you should see in ten minutes

1. `make setup` — completes under 60 s on a warm cache.
2. `make demo` — browser opens on a populated brief; top item names a real issuer (SpaceX, Stripe, etc.), a specific deadline, and a drafted message that reads like Transaction Services outreach.
3. Click the top item's "reasoning" toggle — see the six-dimension risk evaluation with deal-specific evidence, not generic copy.
4. Advance the clock 7 days — watch items move, new items appear, resolved ones drop.
5. `make eval` — scorecard prints with ≥13/15 green.
