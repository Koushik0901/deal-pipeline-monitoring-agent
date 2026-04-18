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
- **LLM combined call:** 5 risk dimensions evaluated in one `AllRiskSignals` call (not 5 separate calls) via `llm/prompts/risk_all_dimensions.py`

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

