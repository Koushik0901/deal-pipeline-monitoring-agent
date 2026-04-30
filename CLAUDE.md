# deal-pipeline-monitoring-agent Development Guidelines

## Active Technologies

Python 3.13.5 · FastAPI · LangGraph (`langgraph-checkpoint-sqlite`) · Pydantic v2 · APScheduler · Jinja2 · HTMX (pinned) · Alpine.js (pinned) · Tailwind CSS (CLI build) · structlog · PyYAML · pytest · uv (dep manager) · OpenRouter (LLM gateway)

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
npx tailwindcss -i ./src/hiive_monitor/web/static/input.css -o ./src/hiive_monitor/web/static/output.css --minify   # rebuild CSS after editing input.css
```

## Code Style

Python 3.11+. Follow standard conventions.

## Environment & Gotchas

- **Test runner:** Always `uv run pytest`, never `python -m pytest` — venv deps require uv
- **Stretch migrations:** `db/migrations.py::stretch_migrations()` runs at startup only; test DBs skip it. Add columns manually in tests that need them (e.g. `conn.execute("ALTER TABLE ticks ADD COLUMN signals TEXT")`)
- **Lazy imports in routes:** `web/routes/main.py` uses lazy imports (`from hiive_monitor import clock as clk` inside handlers) to break circular imports — preserve this pattern, don't hoist to module level
- **`clk.now()` discipline:** Never call `datetime.now()` — enforced by grep test `tests/unit/test_no_datetime_now.py`. Always use `clk.now()` from `hiive_monitor.clock`; capture once per function if used multiple times
- **AllRiskSignals mock pattern:** Integration test mocks branch on `output_model is AllRiskSignals` (identity check), not `hasattr` introspection
- **`ChatOpenRouter` + `extra_body`:** Never pass `extra_body=` to `ChatOpenRouter` constructor — gets forwarded to the provider SDK's `Chat.send()` which rejects it entirely. Token counts are available via `usage_metadata` without it.
- **No `max_length` on internal LLM output fields:** Fields like `evidence`, `reasoning`, `rationale`, `reason` must have no `max_length` — CoT prompting routinely exceeds static limits and raises Pydantic `ValidationError` at runtime.
- **`get_events` ordering:** `dao.get_events()` fetches `ORDER BY occurred_at DESC LIMIT N` then reverses → ASC. So `comm_events[-1]` is the most recent event. Fixture `days_ago` values are relative to `setup.now`.
- **`@view-transition` CSS placement:** Must be at top-level in `input.css`, NOT inside `@layer base {}` — Tailwind silently drops it. Place after the closing `}` of the layer block.
- **Pipeline filter is client-side:** `pipeline.py` no longer applies `tier/stage/issuer/responsible` query params to filter rendered `items` — all deals always render to DOM. `window.PF` (defined in `pipeline.html` scripts block) controls row visibility via `data-*` attributes. Jinja2 uses URL params only to set the initial `hidden` class (no-JS fallback).
- **Jinja2 `.keys` on dicts:** `{% for k in s.keys %}` raises `TypeError: 'builtin_function_or_method' object is not iterable` — Jinja attribute lookup returns the bound `dict.keys` method before falling back to item access. Use `s['keys']` when the key name collides with a dict method.
- **Jinja dict-mutation idiom:** Build a lookup index from a list with `{% for x in xs %}{% set _ = idx.__setitem__(x.key, x) %}{% endfor %}` — no native `update` filter.
- **Alpine.js `x-data` apostrophes:** Never put JS functions containing apostrophes in string literals inside `x-data='...'` — the apostrophe closes the HTML attribute and the rest bleeds as visible text. Move any such function to a `window.xxx` global defined in a `<script>` tag. Current example: `brief.html` defines `window._hfb()` for formatting intervention body text; `_macros.html` calls `x-html="window._hfb(editText)"`.
- **HTMX double-submit prevention:** Add `hx-disabled-elt="this"` + Tailwind `disabled:opacity-50 disabled:cursor-not-allowed` to every action button (approve, confirm-edit, dismiss). HTMX re-enables the element after the response arrives.
- **Barlow Condensed at small sizes:** Barlow Condensed is illegible at 10px uppercase with `tracking-widest`. Don't use it for section labels or metadata text. Barlow (regular weight) works fine at those sizes.
- **`ChatOpenRouter` constructor kwarg is `api_key`, NOT `openrouter_api_key`:** `langchain_openrouter` ≥ 0.2 renamed the field; the old kwarg is silently ignored (Pydantic `extra="ignore"`), the class falls back to `os.environ["OPENROUTER_API_KEY"]`, and Pydantic-Settings does NOT populate `os.environ` — so every call goes out unauthenticated. OpenRouter responds with `'User not found.'` (its way of saying "missing key"), which looks like an auth/account issue but is actually a one-line kwarg bug. See `llm/client.py::_get_llm`.
- **Alpine `x-data` JSON inside double-quoted attributes will break the attribute:** Jinja's `|tojson` emits standard JSON with `"` quotes. If the surrounding attribute is `x-data="..."`, the inner `"` prematurely terminates the attribute and the rest is parsed as new attributes (Alpine then fails with `severity is not defined` etc.). Either single-quote the outer attribute (`x-data='{ ... }'`) and use `"` inside the JS, or HTML-escape the JSON via a custom filter. See `_all_open_list.html` for the fixed pattern.
- **Alpine `x-data` does NOT support object-literal getter syntax (`get foo() { ... }`):** Alpine's expression parser throws `SyntaxError: Unexpected token '}'` and silently breaks every dependent `x-show`/`:class` binding on the page. Use a regular method (`foo() { ... }`) and call it as `foo()` in dependent expressions.
- **Pydantic `max_length` on intervention bodies will fail under the new prompt formats:** `InternalEscalation.body` (no cap) — the 5-section labelled body routinely exceeds any static limit. `OutboundNudge.body` is capped at 1600 to accommodate 3-paragraph emails (was 1200; too tight for paragraph breaks). `InternalEscalation.suggested_next_step` is capped at 400 (was 240; comprehensive multi-step asks blew past the smaller limit). General rule: structural LLM output fields rendered to humans inherit the same "no/loose max_length" discipline as audit fields (CoT prompting routinely exceeds static limits).
- **`get_observations` returns `ORDER BY observed_at ASC, rowid DESC`:** the `rowid DESC` tiebreaker matters because the simulated clock advances by whole days, so multiple ticks share the same `observed_at` timestamp. The deal-detail template does `| sort(attribute='observed_at', reverse=True) | first` (Jinja stable sort), and without the rowid tiebreaker `| first` returned the OLDEST observation in a tied bucket — which surfaced stale severity rationales on the deal-detail page. Don't change the ordering without updating the template too.
- **Brief / Pipeline / Sim pages need `pb-32` (or larger) on the root content container:** the `shortcut_footer` macro is `position: fixed bottom-0` (~56 px tall). Without sufficient bottom padding on the page-root flex container the last content row gets visually cut off behind the footer. The macro's docstring calls this out — current value is `pb-32` (128 px) on `brief.html`'s `<div class="flex flex-col gap-4 ...">`.

## Key Architecture Patterns

- **DAO writes:** All inserts use `INSERT OR IGNORE` for idempotency (FR-024); snooze/approval use explicit transactions via `with conn:`
- **Stretch migrations:** Idempotent `ALTER TABLE` wrapped in `try/except sqlite3.OperationalError` in `stretch_migrations()`, gated by feature flags in `config.py`
- **Feature flags:** Add to `Settings` in `config.py` with `enable_<feature>: bool`; default `True` for analyst-facing UI features
- **LLM combined call:** 5 of the 6 risk dimensions are evaluated in one `AllRiskSignals` call (not 5 separate calls) via `llm/prompts/risk_all_dimensions.py`. The 6th dimension, `counterparty_nonresponsiveness`, is **deterministic** — computed outside the LLM from last-inbound timestamps. `AllRiskSignals` schema does not include it; don't try to add it there.
- **View Transitions naming convention:** Severity badges use `view-transition-name: sev-{deal_id}`, deal ID text uses `dealid-{deal_id}`. Names must match across `_macros.html` (Brief rows), `pipeline.html` (pipeline rows), and `deal_detail.html` (page header) for the cross-document morph to fire.
- **`reasoning_parsed.all_signals` vs `dimensions_triggered`:** `all_signals` is the full 6-dimension state (5 LLM + deterministic counterparty merged at `investigator.py:133`); `dimensions_triggered` is only the fired subset. Templates showing "what the agent considered" want `all_signals`; flag-lists want `dimensions_triggered`.
- **Stylized selects:** Use the `.pf-select` component class (defined in `input.css` under `@layer components`) on `<select>` elements — provides SVG chevron, hover/focus states, dark-mode variant. Don't roll a new `appearance-none` dropdown; extend `.pf-select` if needed.
- **Severity row tinting:** Escalate rows use `bg-error/[0.04]` in Brief (`_macros.html`) and Pipeline (`pipeline.html`). Deal detail page header uses severity-conditional wash: `bg-error/[0.03]` / `bg-warning/[0.03]` / `bg-info/[0.03]` via `latest_obs.severity`.
- **Severity-colored deal IDs:** In `_macros.html` Brief rows, deal ID text color is `text-error` for escalate, `text-warning` for act, `text-primary-container` otherwise. Keep consistent if adding deal IDs elsewhere.
- **Font stack:** `tailwind.config.js` uses `Barlow` for `sans`, `Fira Mono` for `mono`. Both loaded from Google Fonts in `base.html`. Barlow Condensed was removed — see gotcha above.
- **Global HTMX error handlers:** `base.html` registers `htmx:sendError` and `htmx:responseError` document listeners that surface failures as toasts via `announceAction()`. Don't add per-element error handling for network/server errors; these catch them globally.
- **UI CSS component classes:** `.escalate-dot` — pulsing opacity animation for the ESCALATE badge live-indicator. `.copy-success` — brand-green color flash on copy confirmation. `.theme-transitioning` — applied for 300ms during theme toggle to smooth color property transitions. All defined in `input.css @layer base` / `@layer components`.
- **Stage humanization is centralized in two places, both required:** `models/stages.py::STAGE_DISPLAY_NAMES` is the source of truth (`rofr_pending` → `"ROFR review"`, `docs_pending` → `"documents pending"`, `issuer_notified` → `"issuer-notification"`). Two Jinja filters in `app.py` consume it: `human_stage` for direct stage values (`{{ deal.stage | human_stage }}`) and `humanize_stage_codes` for free-text fields where the LLM may have echoed a snake_case stage (`{{ item.draft_body | humanize_stage_codes }}`). Apply `human_stage` to all template stage references; apply `humanize_stage_codes` to LLM-produced text fields (draft body, severity rationale, event timeline summaries, risk-dimension evidence cells). All four prompts (severity, outbound, escalation, brief_entry) also receive a pre-translated `stage_display` and `stage_baseline_days` so the LLM never sees the raw enum.
- **`suggested_next_step` flows from prompt → DB column → UI callout → clipboard:** `InternalEscalation.suggested_next_step` is generated by the LLM, stored in the `interventions.suggested_next_step` column (idempotent migration in `db/migrations.py`), rendered as a primary-tinted "Suggested next step" callout below the body in `_macros.html`, and conditionally appended to the clipboard payload via `clipboardText()` only when not already in the body (handles legacy drafts and analyst-edited bodies that strip the ASK section). The escalation prompt requires the body to end with a "The ask" section that mirrors the field verbatim — so on new drafts the field is duplicated in body and panel, but the clipboard merger dedupes.
- **Brief surfaces both LLM-derived and deterministic-watch deals:** the LLM investigator's queue is small (`max_investigations_per_tick`, default 12) and engineered demo data tends to saturate it with escalate/act candidates — so watch-tier deals rarely produce a `watch`-severity observation row and don't reach the brief through the intervention path. To bridge: `web/routes/main.py::/brief` and `/api/brief-stats` both compute a `watch_list` by calling `pipeline_health.compute_signals` + `compute_health` on every live deal, filtering to `tier == 'watch' AND deal_id NOT IN open_iv_deal_ids`. Rendered as a collapsed `<details>` panel at the BOTTOM of the main column in `brief.html` (NOT top — visual hierarchy: act → monitor). Sidebar `WATCH` count sums LLM-classified + deterministic so it stays consistent.
- **Severity rationale is narrative prose, NOT a debug trace:** `severity.py` produces 2–4 sentences of plain-English reasoning (with one parenthetical ROFR gloss, anchored numbers, no dimension codes or `conf=` syntax) ending with a single `Verdict: <severity>.` line that satisfies the `verdict_matches_rationale` validator. The decision-tree LOGIC stays in the prompt for accuracy; the OUTPUT format is for humans. Same field (`reasoning_parsed.severity_rationale`) feeds both the deal-detail "Severity Rationale" panel and the brief's "Agent reasoning" panel. Don't regress to bullet/test-step output — the eval suite still passes against the narrative format.
- **Intervention drafts have type-specific structure:** `internal_escalation` body is 5 labelled sections (`What's blocked` / `How long it's been stuck` / `What we've already tried` / `Why this matters` / `The ask`), blank-line separated. `outbound_nudge` body is 2–3 short paragraphs (Situation / The ask / Door open) — no labelled headers, blank-line separated for inbox readability. `brief_entry` is a 3-field structured payload (headline / one_line_summary / recommended_action). All three prompts gloss "ROFR" parenthetically on first use and forbid snake_case stage codes, dimension codes, and bare `conf=`/`baseline` references.
- **Hover preview tooltip on summary cells:** in `_macros.html`, the summary cell uses `group/summary relative` and contains a child `<div x-show="!expanded" class="invisible group-hover/summary:visible ... pointer-events-none absolute top-full left-0 ...">` showing full why_today + draft subject + body preview + suggested_next_step. Tooltip is `pointer-events-none` so it never blocks the row's click handler, and Alpine's `x-show="!expanded"` suppresses it on already-expanded rows to avoid redundancy. After adding new utilities like `group/summary` or `group-hover/summary:opacity-100`, run `npx tailwindcss -i ./src/.../input.css -o ./src/.../output.css --minify` so they make it into the compiled CSS — Tailwind only includes classes actually present in templates at build time.

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
  - `stage_aging`: ratio = `stage_entered_days_ago / DWELL_BASELINES[stage]` — NOT issuer `typical_response_days`. Key baselines: `docs_pending=3, rofr_pending=20, signing=4` (see `models/stages.py`). Fires at ~1.5× (watch), ≥2.0× (act/high conf).
  - `communication_silence`: threshold is strictly `>` (not `≥`): late stages (rofr_cleared, signing, funding) `>7 days`; any live stage `>14 days`. Set fixture `days_ago` to threshold+1 to ensure triggering.
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
- **`make eval-deep` runtime:** ~34 minutes for 39 scenarios — this is normal. Don't abort early; the rich progress bar shows per-scenario progress.
- **`deepeval` + Python 3.13:** `deepeval>=3.9.0` requires `grpcio>=1.67.1` for cp313 wheels. If `make eval-deep` hangs on startup or fails with a build error, run `uv sync --upgrade-package grpcio` to get a compatible version. `DEEPEVAL_TELEMETRY_OPT_OUT=YES` (already in `.env` and `Makefile`) is sufficient to suppress telemetry — no other flags needed.
- **`deep_scorecard` filename:** The deep scorecard filename uses the Tier 1 results timestamp (`results_{ts}.json`), not the actual run time. Running `make eval-deep` twice on the same Tier 1 results overwrites the same scorecard file.

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

**Typography:** Barlow (body, 400/500/600/700) + Fira Mono (deal IDs, code). Loaded from Google Fonts. No Barlow Condensed.

**Color Palette:**
- **Brand primary:** #1B4332 (deep forest green) — active nav, primary buttons, informational severity, logo
- **Brand secondary:** #2D6A4F (lighter green) — accents, hover states
- **Semantic colors (override brand):** Escalate=red, Act=amber, Watch=blue, Informational=green
- **Neutrals:** Tinted toward brand hue (OKLCH chroma 0.005–0.01). No pure gray.

**Severity coloring applied to:**
- Severity badges (all pages)
- Deal ID text in Brief rows (error/warning/primary-container)
- Row background tint: `bg-error/[0.04]` for escalate rows in Brief and Pipeline
- Deal detail page header: subtle `bg-{color}/[0.03]` wash by severity
- ESCALATE badge: pulsing dot + larger text (`text-[0.75rem]`) vs other badges (`text-[0.6875rem]`)

### Design Principles
1. **Information density over whitespace.** No excessive padding. Tables compact.
2. **Scannability first.** Severity, deal IDs, action items jump out. Hierarchy through typography weight/size.
3. **No decoration.** No side stripes, gradients, rounded-corner card stacking. Motion only for state changes.
4. **Trust through transparency.** Every risk flag carries visible reasoning. Agent is not a black box.
5. **Analyst control.** Agent suggests; analyst acts. No autonomous sends. Review before action.

<!-- MANUAL ADDITIONS END -->

