# deal-pipeline-monitoring-agent Development Guidelines

## Active Technologies

Python 3.11+ · FastAPI · LangGraph (`langgraph-checkpoint-sqlite`) · Pydantic v2 · APScheduler · Jinja2 · HTMX (pinned) · Alpine.js (pinned) · Tailwind CSS (CLI build) · structlog · PyYAML · pytest · uv (dep manager) · OpenRouter (LLM gateway)

## Project Structure

```text
src/hiive_monitor/{agents,db,llm,models,seed,eval,web}/
tests/{integration,unit,smoke,eval}/
specs/001-deal-pipeline-monitor/   # task list + contracts
```

## Commands

```bash
uv run pytest tests/integration/ -v          # always use uv run, not plain pytest
uv run pytest --ignore=tests/eval -v        # skip eval fixtures in fast runs
ruff check .
```

## Code Style

Python 3.11+. Follow standard conventions.

## Environment & Gotchas

- **Test runner:** Always `uv run pytest`, never `python -m pytest` — venv deps require uv
- **Stretch migrations:** `db/migrations.py::stretch_migrations()` runs at startup only; test DBs skip it. Add columns manually in tests that need them (e.g. `conn.execute("ALTER TABLE ticks ADD COLUMN signals TEXT")`)
- **Lazy imports in routes:** `web/routes/main.py` uses lazy imports (`from hiive_monitor import clock as clk` inside handlers) to break circular imports — preserve this pattern, don't hoist to module level
- **`clk.now()` discipline:** Never call `datetime.now()` — enforced by grep test `tests/unit/test_no_datetime_now.py`. Always use `clk.now()` from `hiive_monitor.clock`; capture once per function if used multiple times
- **AllRiskSignals mock pattern:** Integration test mocks branch on `output_model is AllRiskSignals` (identity check), not `hasattr` introspection

## Key Architecture Patterns

- **DAO writes:** All inserts use `INSERT OR IGNORE` for idempotency (FR-024); snooze/approval use explicit transactions via `with conn:`
- **Stretch migrations:** Idempotent `ALTER TABLE` wrapped in `try/except sqlite3.OperationalError` in `stretch_migrations()`, gated by feature flags in `config.py`
- **Feature flags:** Add to `Settings` in `config.py` with `enable_<feature>: bool`; default `True` for analyst-facing UI features
- **LLM combined call:** 5 of the 6 risk dimensions are evaluated in one `AllRiskSignals` call (not 5 separate calls) via `llm/prompts/risk_all_dimensions.py`. The 6th dimension, `counterparty_nonresponsiveness`, is **deterministic** — computed outside the LLM from last-inbound timestamps. `AllRiskSignals` schema does not include it; don't try to add it there.

## Severity & Risk Rules

Source of truth: `src/hiive_monitor/llm/prompts/severity.py` and `src/hiive_monitor/models/risk.py`. When auditing fixtures or writing new ones, cross-check against these:

- **Severity decision tree (in priority order):**
  1. Deadline ≤ 2 days → **escalate**
  2. `prior_breakage_count ≥ 1` AND any act-level trigger (e.g. stage_aging ≥ 2.0×) → **escalate**
  3. Deadline ≤ 10 days + any dimension triggered → **act**
  4. `stage_aging` ratio ≥ 2.0× `typical_response_days` → **act** (high confidence alone)
  5. 2+ dimensions triggered with ≥1 at confidence ≥ 0.85 → **act**
  6. 1–2 low-confidence dims, no deadline → **watch**
  7. 0 dims triggered at confidence ≥ 0.70 → **informational**
- **"3+ dimensions" is NOT sufficient for escalate** — escalate needs either a short deadline, prior_breakage + act-level, or deadline+comm_silence co-trigger. The severity prompt explicitly calls this out.
- **Dimension trigger thresholds:**
  - `stage_aging`: ratio = `stage_entered_days_ago / typical_response_days`; fires at ~1.5× (watch/low conf), ≥2.0× (act/high conf)
  - `communication_silence`: fires when days-since-last-inbound ≥ ~2× `typical_response_days`
  - `unusual_characteristics`: `is_first_time_buyer=true`, `prior_breakage_count ≥ 1`, or `multi_layer_rofr=true` — these are static facts, so confidence is inherently high
  - `missing_prerequisites`: any non-empty `blockers` list
  - `deadline_proximity`: non-null `rofr_deadline_days_from_now ≤ 10`
- **Watch severity DOES draft interventions.** Don't assume drafting is only for act/escalate. See `investigator.py` `_severity_router` (lines ~316–369): watch + `responsible_party=hiive_ts` → `status_recommendation`; watch + external party → `brief_entry`. Only `informational` skips drafting.

## Eval Fixtures & Langfuse

- **YAML fixtures** live in `eval/fixtures/`. Each file has top-level `id`, `category`, `description`, `setup` (now, issuers, parties, deal, events), `assertions` (what the test enforces), and `ground_truth` (severity, dimensions_triggered, dimensions_not_triggered, expected_tools).
- **Loader entrypoint:** `hiive_monitor.eval.runner.load_scenarios(Path("eval/fixtures"))` — returns a list of scenario dicts. There is **no** `load_fixture` function; don't waste time searching for it.
- **Enrichment cap:** `investigator.py::_MAX_ENRICHMENT_ROUNDS = 2`. The agent's `assess_sufficiency` node uses an LLM call to pick which enrichment tool to run next — it's not a hardcoded priority list. So `ground_truth.expected_tools` lists are LLM-dependent; avoid over-specifying them.
- **Langfuse dataset sync is automatic.** `src/hiive_monitor/eval/langfuse_dataset.py::ensure_dataset()` is called at the start of every eval run from `langfuse_tracer.py`. It upserts every fixture by stable ID (`eval/<scenario_id>`), so editing a YAML and re-running the eval is the whole sync workflow — no separate CLI command.
- **Fixture schema validation:** `uv run python -c "from hiive_monitor.eval.runner import load_scenarios; from pathlib import Path; print(len(load_scenarios(Path('eval/fixtures'))))"` — a fast smoke check that every YAML parses before running full evals.

## Windows / Bash Quirks

- **Backgrounded `pytest` runs can produce empty stdout log files** under Windows Git Bash even when the process exits 0. Exit codes in completion notifications are reliable; empty output ≠ test failure. Prefer `--junitxml` to capture structured results if the text output matters.
- `pgrep` / `ps aux` are not available in the Git Bash shipped with this setup. Use `tasklist //FI "IMAGENAME eq python.exe"` to list processes if you need to.

<!-- MANUAL ADDITIONS START -->

## Design Context

### Users
Transaction Services analysts at Hiive. They are operationally focused, time-constrained, and spend their workday reading dense information: deal pipelines, risk signals, communication histories. They are not designers; they are expert operators who need speed and clarity.

### Brand Personality
**Three words:** Neutral, trustworthy, operational.

The interface should feel like a tool built by people who understand financial operations. No personality, no playfulness — just clarity and speed. The green brand color is a small mark of identity; it should not dominate.

### Aesthetic Direction
**Reference:** Linear.app — dense, minimal, professional.

**Theme:** Both light and dark mode (user toggleable)

**Color Palette:**
- **Brand primary:** #1B4332 (deep forest green) — active nav, primary buttons, informational severity, logo
- **Brand secondary:** #2D6A4F (lighter green) — accents, hover states
- **Semantic colors (override brand):** Escalate=red, Act=amber, Watch=blue, Informational=green
- **Neutrals:** Tinted toward brand hue (OKLCH chroma 0.005–0.01). No pure gray.

### Design Principles
1. **Information density over whitespace.** No excessive padding. Tables compact.
2. **Scannability first.** Severity, deal IDs, action items jump out. Hierarchy through typography weight/size.
3. **No decoration.** No side stripes, gradients, rounded-corner card stacking. Motion only for state changes.
4. **Trust through transparency.** Every risk flag carries visible reasoning. Agent is not a black box.
5. **Analyst control.** Agent suggests; analyst acts. No autonomous sends. Review before action.

<!-- MANUAL ADDITIONS END -->

