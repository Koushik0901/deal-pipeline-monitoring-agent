---
description: "Task list for Deal Pipeline Monitoring Agent (rev 2 — includes Investigator enrichment loop)"
---

# Tasks: Deal Pipeline Monitoring Agent

**Input**: Design documents from `/specs/001-deal-pipeline-monitor/`
**Prerequisites**: [plan.md](./plan.md) (rev 2), [spec.md](./spec.md) (rev 2 — FR-AGENT-01–06), [research.md](./research.md), [data-model.md](./data-model.md), [contracts/](./contracts/)

**Organization**: Tasks grouped by user story for independent implementation and incremental delivery against the 55-hour MVP budget. Revision 2 incorporates the agentic Investigator redesign: `assess_sufficiency` (N3), `enrich_context` (N4, bounded loop), three enrichment tool functions, and `enrichment_chain` audit trail.

**Tests**: Included where plan.md names them as architectural invariants (clock grep test, idempotency integration test, enrichment-loop bound test, LLM schema round-trip, eval harness). Not a full TDD suite.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no blocking dependency)
- **[Story]**: Maps to spec.md user story (US1–US7)

## User-story priority map

- **US1** — Daily Brief (P1, MVP centerpiece)
- **US2** — Per-deal view (P1)
- **US3** — Monitoring loop / Pipeline Monitor (P1)
- **US4** — Per-deal investigation / Deal Investigator (P1, includes enrichment loop)
- **US5** — Simulation controls (P1)
- **US6** — Audit trail & debug (P1)
- **US7** — Evaluation harness (P1, parallel lane)

---

## Phase 1: Setup

**Purpose**: Repo scaffold, tooling, dependencies. Hours 0–2.

- [X] T001 Create directory tree per [plan.md §Project Structure](./plan.md): `src/hiive_monitor/{db,models,llm,agents,web,seed,eval}`, `tests/{unit,integration,smoke}`, `out/`
- [X] T002 Initialize `pyproject.toml` — Python ≥3.11, pinned deps: fastapi, uvicorn, langgraph, langgraph-checkpoint-sqlite, anthropic, pydantic>=2, apscheduler, jinja2, structlog, pyyaml, pytest, pytest-asyncio, httpx
- [X] T003 Run `uv sync`, commit `uv.lock`
- [X] T004 [P] Create `Makefile` with empty recipe stubs: `setup`, `seed`, `run`, `eval`, `demo`, `clean`
- [X] T005 [P] Create `.env.example` — `ANTHROPIC_API_KEY=`, `CLOCK_MODE=simulated`, `PORT=8000`, `DOMAIN_DB_PATH=domain.db`, `CHECKPOINT_DB_PATH=agent_checkpoints.db`, `ATTENTION_THRESHOLD=0.6`
- [X] T006 [P] Configure ruff in `pyproject.toml` (line-length 100, target-version py311, select E/F/I/UP)
- [X] T007 [P] Initialize Tailwind: `npx tailwindcss init`, create `src/hiive_monitor/web/static/input.css` with `@tailwind base/components/utilities`, configure `tailwind.config.js` content glob for templates
- [X] T008 [P] Create `README.md` skeleton with three-command quickstart placeholder

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Clock abstraction, dual SQLite schema, structured logging, Pydantic models, LLM client, app shell. Every user story depends on this phase.

**⚠️ CRITICAL**: No US1–US7 work starts until Phase 2 is green.

### 2.1 Clock abstraction

- [X] T009 Implement `src/hiive_monitor/clock.py` — `Clock` protocol, `RealTimeClock`, `SimulatedClock` (supports `advance(days: int)`), `get_clock()` factory reading `CLOCK_MODE` env var
- [X] T010 [P] Write `tests/unit/test_clock.py` — SimulatedClock advances deterministically; RealTimeClock monotonic; `get_clock()` honors env
- [X] T011 [P] Write `tests/unit/test_no_datetime_now.py` — greps `src/hiive_monitor/**/*.py` for `datetime.now(` or `datetime.utcnow(`; asserts zero hits (FR-020 clock-discipline enforcement)

### 2.2 Config & logging

- [X] T012 [P] Implement `src/hiive_monitor/config.py` — Pydantic `Settings` model reading env vars with typed defaults
- [X] T013 [P] Implement `src/hiive_monitor/logging.py` — structlog configuration, human-readable in dev / JSON when `LOG_FORMAT=json`, correlation-ID context vars (`tick_id`, `deal_id`)

### 2.3 Domain database

- [X] T014 Create `src/hiive_monitor/db/schema.sql` — DDL for 7 tables: `issuers`, `parties`, `deals`, `events`, `ticks`, `agent_observations`, `interventions` per [data-model.md §1](./data-model.md)
- [X] T015 Implement `src/hiive_monitor/db/connection.py` — `get_domain_conn()` and `get_checkpoint_conn()`, both `check_same_thread=False`, WAL mode enabled
- [X] T016 Implement `src/hiive_monitor/db/init.py` — `python -m hiive_monitor.db.init` creates `domain.db` from `schema.sql` and `agent_checkpoints.db` via `SqliteSaver.from_conn_string`
- [X] T017 Implement `src/hiive_monitor/db/dao.py` — typed data-access helpers:
  - CRUD: `get_live_deals()`, `get_deal(deal_id)`, `get_events(deal_id)`, `start_tick(mode)`, `complete_tick(tick_id, stats)`, `insert_observation(...)`, `insert_intervention(...)`, `update_intervention_status(...)`, `has_open_intervention(deal_id)`. All inserts use `INSERT … ON CONFLICT DO NOTHING`.
  - Enrichment tools (read-only, called by N4 `enrich_context` per FR-AGENT-03):
    - `fetch_communication_content(deal_id) -> list[dict]` — queries `events` WHERE `event_type IN ('comm_outbound','comm_inbound')`, returns `{occurred_at, direction, body}` list
    - `fetch_prior_observations(deal_id) -> list[dict]` — queries `agent_observations ORDER BY observed_at DESC LIMIT 5`, returns `{tick_id, severity, reasoning_summary}`
    - `fetch_issuer_history(issuer_id) -> list[dict]` — queries `deals JOIN agent_observations` WHERE `issuer_id=?` AND `stage IN ('settled','broken')`, returns `{deal_id, final_stage, key_signals}`
  - Suppression query: `recent_agent_recommended_comm(deal_id: str, within_ticks: int) -> bool` — returns `True` if a `comm_sent_agent_recommended` event exists for this deal within the last `within_ticks` ticks. Used by Monitor attention scoring for suppression logic (FR-LOOP-02).
  - Approval transaction: `approve_intervention_with_event(intervention_id: str, simulated_timestamp: datetime) -> None` — atomically updates intervention status and inserts the `comm_sent_agent_recommended` event in a single transaction (FR-LOOP-01, FR-LOOP-03). Called by both approve and edit-confirm handlers.
- [X] T018 Wire `make setup` target: `uv sync` + `python -m hiive_monitor.db.init`

### 2.4 Pydantic contract models

- [X] T019 [P] Implement `src/hiive_monitor/models/stages.py` — `Stage` enum + dwell-time baselines dict per [data-model.md §1.3](./data-model.md)
- [X] T020 [P] Implement `src/hiive_monitor/models/risk.py` — `Severity` enum, `RiskDimension` enum, `RiskSignal`, `SeverityDecision`
- [X] T021 [P] Implement `src/hiive_monitor/models/interventions.py` — `OutboundNudge`, `InternalEscalation`, `BriefEntry`, `Intervention` discriminated union
- [X] T022 [P] Implement `src/hiive_monitor/models/brief.py` — `DailyBrief`, `DailyBriefItem`
- [X] T023 [P] Implement `src/hiive_monitor/models/snapshot.py` — `DealSnapshot`, `Blocker`, `EventRef`, `AttentionScore`
- [X] T023a [P] Implement `src/hiive_monitor/models/event.py` — `Event` Pydantic model + `EventType` Literal with exactly these values: `stage_transition`, `doc_received`, `doc_requested`, `comm_outbound`, `comm_inbound`, `comm_sent_agent_recommended` (FR-LOOP-03). Update `schema.sql` CHECK constraint on `events.event_type` to match.
- [X] T024 [P] Write `tests/unit/test_models.py` — round-trip every model through `.model_json_schema()` and `.model_validate(.model_dump())`

### 2.5 LLM client

- [X] T025 Implement `src/hiive_monitor/llm/client.py::call_structured(prompt, output_model, model, tick_id, deal_id, call_name, timeout=30.0)` — tool-use forced mode per [contracts/llm-schemas.md](./contracts/llm-schemas.md): `model_json_schema()` → Anthropic `input_schema`, `tool_choice={"type":"tool","name":…}`, parse tool-use block → Pydantic model
- [X] T026 Add exponential-backoff retry (max 3 attempts) on `AnthropicError` excluding `BadRequestError` in `llm/client.py`
- [X] T027 Add one corrective reprompt on `ValidationError` — include validation error text in follow-up message; second failure → return `None` (caller persists error observation)
- [X] T028 Add in-memory idempotency cache keyed on `(tick_id, deal_id, call_name)` in `llm/client.py`
- [X] T029 Emit `llm.call.completed` structlog event: `model`, `call_name`, `latency_ms`, `input_tokens`, `output_tokens`, `tick_id`, `deal_id`, `attempt`, `parse_ok`

### 2.6 LangGraph graph state

- [X] T030 Implement `src/hiive_monitor/agents/graph_state.py` — two TypedDict state shapes per [data-model.md §2](./data-model.md):
  - `MonitorState`: `tick_id`, `mode`, `tick_started_at`, `candidate_deals`, `attention_scores`, `investigation_queue`, `investigator_results` (Annotated reducer), `errors`
  - `InvestigatorState`: `tick_id`, `deal_id`, `deal_snapshot`, `risk_signals` (Annotated reducer), `severity`, `severity_reasoning`, `enrichment_count` (int, init 0), `enrichment_chain` (Annotated list reducer), `intervention`, `observation_id`
  - `EnrichmentStep` TypedDict: `round`, `tool_called` (Literal of three tool names), `tool_rationale`, `context_summary`

### 2.7 Scheduler + FastAPI app shell

- [X] T031 Implement `src/hiive_monitor/scheduler.py` — APScheduler `BackgroundScheduler`; real-time mode = `IntervalTrigger(60s)`; simulated mode = no auto trigger (driven by `/sim/advance`)
- [X] T032 Implement `src/hiive_monitor/app.py` — FastAPI app factory, startup event initializes scheduler + LangGraph checkpointer, `Jinja2Templates`, static mount
- [X] T033 [P] Write `tests/smoke/test_app_boots.py` — GET `/` → 307; GET `/brief` → 200
- [X] T034 [P] Create `src/hiive_monitor/web/templates/base.html` — Tailwind CSS link, Alpine.js pinned CDN, HTMX pinned CDN, empty content block

**Checkpoint**: Foundation ready. US3 + US4 (Monitor + Investigator) can now build in parallel with US7 (eval runner).

---

## Phase 3: User Story 3 — Monitoring Loop / Pipeline Monitor (Priority: P1)

**Goal**: Pipeline Monitor tick runs end-to-end — screens live deals with Haiku, produces investigation queue, writes `ticks` row atomically, is idempotent on restart.

> **Architecture note**: The Pipeline Monitor is a **structured screening pipeline**, not an agent. Fixed scoring, deterministic top-5 selection. Emergent logic would make cost unpredictable and screening decisions harder to audit.

**Independent Test**: `python -m hiive_monitor.agents.monitor --once` against seeded DB; verify exactly one `ticks` row, deals scored, re-run same `tick_id` produces zero duplicate observations.

- [X] T035 [US3] Implement `src/hiive_monitor/llm/prompts/screening.py` — Haiku prompt returning `AttentionScore` per [contracts/llm-schemas.md#screen_deal](./contracts/llm-schemas.md)
- [X] T036 [US3] Implement `src/hiive_monitor/agents/monitor.py` — LangGraph `StateGraph` with `MonitorState`; nodes: `load_live_deals`, `screen_with_haiku` (fan-out per deal), `apply_suppression` (see T036a), `select_investigation_queue` (threshold 0.6, top-5 cap per FR-001a), `fan_out_investigators` (LangGraph `Send` API), `close_tick`
- [X] T036a [US3] Implement `apply_suppression(state)` node in `agents/monitor.py` — for each deal in `attention_scores`, query `events` for any row where `deal_id=?` AND `event_type='comm_sent_agent_recommended'` AND tick-distance ≤ `settings.SUPPRESSION_TICKS` (default 3); if found, multiply raw score × 0.2 (FR-LOOP-02). Use `dao.py::recent_agent_recommended_comm(deal_id, within_ticks)` — new DAO helper to add alongside existing queries in T017.
- [X] T036b [US3] Add `SUPPRESSION_TICKS: int = 3` field to `src/hiive_monitor/config.py::Settings` (env var `SUPPRESSION_TICKS`)
- [X] T036c [US3] Write `tests/integration/test_monitor_suppression.py` — seed a deal with raw Haiku score 0.8; insert a `comm_sent_agent_recommended` event at current tick-1; run `run_tick`; assert deal's post-suppression score is 0.16 (0.8 × 0.2) and deal does NOT appear in `investigation_queue`. Also assert that 4 ticks later (past suppression window) the same deal WOULD be investigated. (FR-LOOP-02)
- [X] T037 [US3] Wire Monitor graph compile with `SqliteSaver.from_conn_string(CHECKPOINT_DB_PATH)`, `check_same_thread=False`, `thread_id=tick_id`
- [X] T038 [US3] Implement `run_tick(mode)` in `agents/monitor.py` — atomic `INSERT INTO ticks` at start (idempotency anchor FR-024); invoke graph; `UPDATE ticks SET tick_completed_at` at end
- [X] T039 [US3] Register `run_tick` with `scheduler.py` for real-time mode (60s IntervalTrigger)
- [X] T040 [US3] Write `tests/integration/test_monitor_loop.py::test_single_tick_happy_path` — seeded DB, one tick, `ticks` has one row, no duplicate observations on re-run
- [X] T041 [US3] Write `tests/integration/test_monitor_loop.py::test_crash_restart` — kill mid-tick after 2 of 5 Investigators emit; restart; assert 5 unique `(tick_id, deal_id)` pairs (plan §risk #2 mitigation)

**Checkpoint**: US3 standalone — Monitor ticks and is idempotent.

---

## Phase 4: User Story 4 — Per-Deal Investigation / Deal Investigator (Priority: P1)

**Goal**: Deal Investigator agentic sub-graph — 7-node variable-path graph with bounded enrichment loop. Observes deal, evaluates 6 dimensions, reasons about sufficiency (`assess_sufficiency`), optionally calls enrichment tools (max 2 rounds), scores severity, drafts intervention when severity ∈ {act, escalate}, persists observation with full reasoning + enrichment chain.

> **What makes it agentic**: The N3 `assess_sufficiency` node creates a genuine loop (N3→N4→N3) with branching based on LLM reasoning, not rules. The number of steps, tools called, and context assembled differ per deal.

**Independent Test**: Invoke Investigator on a seeded "rofr_pending, 7 days to deadline, 14-day comm silence" deal; assert severity=`act`, `deadline_proximity` + `communication_silence` triggered, outbound_nudge drafted. Invoke on a "communication silence with prior message explaining legal hold" deal; assert enrichment round fires (`fetch_communication_content` called), severity downgraded from initial watch estimate.

### 4.1 Dimension evaluators (all parallel after T030)

- [X] T042 [P] [US4] Implement `src/hiive_monitor/llm/deterministic/counterparty_responsiveness.py` — pure Python lookup against issuer `typical_response_days` vs. `days_since_last_comm`; returns `RiskSignal` (FR-004, no LLM)
- [X] T043 [P] [US4] Implement `src/hiive_monitor/llm/prompts/risk_stage_aging.py` — Sonnet prompt + `RiskSignal` output schema
- [X] T044 [P] [US4] Implement `src/hiive_monitor/llm/prompts/risk_deadline_proximity.py`
- [X] T045 [P] [US4] Implement `src/hiive_monitor/llm/prompts/risk_communication_silence.py`
- [X] T046 [P] [US4] Implement `src/hiive_monitor/llm/prompts/risk_missing_prerequisites.py`
- [X] T047 [P] [US4] Implement `src/hiive_monitor/llm/prompts/risk_unusual_characteristics.py`
- [X] T048 [US4] Implement `src/hiive_monitor/llm/validators.py::assert_evidence_references_deal(signal, snapshot)` — post-parse validator enforcing FR-033: evidence must contain at least one of {issuer name, stage name, numeric days, deadline date, counterparty role, blocker description}; triggers corrective reprompt on failure

### 4.2 Severity scoring

- [X] T049 [US4] Implement `src/hiive_monitor/llm/prompts/severity_rubric.py` — Sonnet prompt with rubric anchors per [contracts/llm-schemas.md#decide_severity](./contracts/llm-schemas.md); returns `SeverityDecision`

### 4.3 assess_sufficiency node (N3 — the agentic node)

- [X] T050 [US4] Implement `src/hiive_monitor/llm/prompts/assess_sufficiency.py` — Sonnet prompt that receives all 6 `RiskSignal`s + `enrichment_count` + `enrichment_chain` so far; returns a structured decision:
  ```python
  class SufficiencyDecision(BaseModel):
      verdict: Literal["proceed", "enrich"]
      reasoning: str = Field(max_length=400)
      suggested_tool: Literal[
          "fetch_communication_content",
          "fetch_prior_observations",
          "fetch_issuer_history",
          None
      ] = None
      tool_rationale: str | None = None
  ```
  `SufficiencyDecision` MUST be a Pydantic `BaseModel` (not a `TypedDict`) — `llm/client.py::call_structured` only accepts `BaseModel` subclasses because it routes outputs through `model_json_schema()` → Anthropic tool-use → `model_validate`. System prompt encodes the three FR-AGENT-05 trigger situations explicitly. When `enrichment_count >= 2` the node MUST route to `proceed` regardless of LLM output (FR-AGENT-04 hard cap enforced in graph routing, not in the prompt).
- [X] T051 [US4] Implement `N3_assess_sufficiency` node in `src/hiive_monitor/agents/investigator.py` — calls `assess_sufficiency.py` prompt; reads `state["enrichment_count"]`; emits verdict; routing edge: `verdict=="proceed"` → N5, `verdict=="enrich"` → N4 (only if `enrichment_count < 2`; otherwise force → N5)

### 4.4 enrich_context node (N4 — bounded loop)

- [X] T052 [US4] Implement `src/hiive_monitor/llm/prompts/enrich_context.py` — Sonnet prompt that receives `SufficiencyDecision.suggested_tool` + raw tool output; returns an `EnrichmentStepOutput` (Pydantic `BaseModel`, see [contracts/llm-schemas.md#enrich_context](./contracts/llm-schemas.md)). **Bridge note**: `call_structured` returns `EnrichmentStepOutput` (Pydantic `BaseModel`). Before appending to graph state, convert to the `EnrichmentStep` `TypedDict` via `EnrichmentStep(**output.model_dump())`. This is the explicit type bridge between the LLM client contract and the LangGraph state schema — the client layer is `BaseModel`-only, the graph-state layer is `TypedDict`-only.
- [X] T053 [US4] Implement `N4_enrich_context` node in `src/hiive_monitor/agents/investigator.py`:
  - Dispatch to the tool function named in `suggested_tool` via `dao.py` (FR-AGENT-03: `fetch_communication_content`, `fetch_prior_observations`, `fetch_issuer_history`)
  - Call `enrich_context.py` Sonnet prompt with tool output → get `EnrichmentStep`
  - Increment `enrichment_count` in state; append `EnrichmentStep` to `enrichment_chain`
  - Loop edge: → N3 `assess_sufficiency`
- [X] T054 [US4] Write `tests/integration/test_investigator.py::test_enrichment_loop_bounded` — state with `enrichment_count=2` at N3 entry; assert N3 routes to N5 without calling N4 (FR-AGENT-04)
- [X] T055 [US4] Write `tests/integration/test_investigator.py::test_enrichment_fires_on_comm_silence` — deal with 14-day silence + prior comm explaining legal hold; assert `fetch_communication_content` called, enrichment_chain non-empty in observation

### 4.5 Intervention drafters (all parallel after T048)

- [X] T056 [P] [US4] Implement `src/hiive_monitor/llm/prompts/intervention_outbound_nudge.py` — Sonnet prompt; post-parse validates deal-specific-fact presence via `validators.py`
- [X] T057 [P] [US4] Implement `src/hiive_monitor/llm/prompts/intervention_internal_escalation.py`
- [X] T058 [P] [US4] Implement `src/hiive_monitor/llm/prompts/intervention_brief_entry.py`

### 4.6 Full Investigator graph (7 nodes)

- [X] T059 [US4] Implement complete `src/hiive_monitor/agents/investigator.py` LangGraph `StateGraph` with all 7 nodes wired:
  - N1 `observe` → builds `DealSnapshot` from `dao.py`
  - N2 `evaluate_dimensions` → 6 parallel branches (5 LLM + 1 deterministic) → `risk_signals`
  - N3 `assess_sufficiency` → conditional edge to N4 or N5
  - N4 `enrich_context` → loop edge back to N3; increments `enrichment_count`
  - N5 `score_severity` → `SeverityDecision`
  - Conditional branch: severity ∈ {act, escalate} AND `has_open_intervention(deal_id)` is False → N6; else → N7
  - N6 `draft_intervention` → `OutboundNudge | InternalEscalation | BriefEntry`
  - N7 `emit_observation` → persists `agent_observations` row with `reasoning` JSON including `enrichment_chain` serialized (FR-AGENT-06); links `intervention_id` if drafted
- [X] T060 [US4] Compile Investigator with `SqliteSaver`, `thread_id=tick_id`, `checkpoint_ns=deal_id` (per-deal isolation)
- [X] T061 [US4] Wire suppression logic in N6 — skip draft when `has_open_intervention(deal_id)` is True; emit observation linking existing `intervention_id` (FR-024a)
- [X] T062 [US4] Wire Monitor's `fan_out_investigators` node to invoke Investigator per queued deal via LangGraph `Send` API
- [X] T063 [US4] Write `tests/integration/test_investigator.py::test_rofr_deadline_7d` — matches `scenarios/detection_rofr_7d.yaml`; asserts dimensions + severity=act + outbound_nudge
- [X] T064 [US4] Write `tests/integration/test_investigator.py::test_early_exit_informational` — healthy deal; assert no intervention drafted, no enrichment triggered

**Checkpoint**: US3 + US4 = complete agent. Observations, enrichment chains, and interventions land in DB with full reasoning. UI not yet wired.

---

## Phase 5: User Story 1 — Daily Brief (Priority: P1) 🎯 MVP Landing

**Goal**: Analyst opens `/brief`, sees 5–7 ranked items with severity, summary, expandable reasoning (incl. enrichment chain if any), drafted intervention; can approve, edit, dismiss.

**Independent Test**: With seeded DB + one completed tick, GET `/brief` returns HTML with ≥1 and ≤7 items; POST approve transitions status to `approved` and returns swapped fragment.

### Brief composer

- [X] T065 [US1] Implement `src/hiive_monitor/llm/prompts/daily_brief.py` — Sonnet ranking prompt per [contracts/llm-schemas.md#compose_daily_brief](./contracts/llm-schemas.md); post-validates ranking heuristic (escalate before act, nearer deadline first)
- [X] T066 [US1] Implement `src/hiive_monitor/agents/brief_composer.py::compose_daily_brief(tick_id) -> DailyBrief` — reads completed observations + carry-over open interventions; invokes Sonnet
- [X] T067 [US1] Wire `brief_composer` call into Monitor `close_tick` node

### Routes + templates

- [X] T068 [US1] Implement `src/hiive_monitor/web/routes/brief.py` — GET `/` redirect; GET `/brief` full page or fragment (HX-Request header); GET `/brief/all-open` with severity/stage/issuer filter query params
- [X] T069 [US1] Implement `src/hiive_monitor/web/routes/interventions.py` — POST `/interventions/{id}/approve|edit|dismiss` per [contracts/http-routes.md](./contracts/http-routes.md); returns `_brief_item.html` fragment + OOB status-bar swap. **Approve and edit-confirm handlers MUST execute a single atomic transaction** (per plan.md "Analyst approval side-effects"): (1) `UPDATE interventions SET status=?, approved_at=clock.now(), final_text=?`, (2) `INSERT INTO events (deal_id, event_type, occurred_at, payload) VALUES (?, 'comm_sent_agent_recommended', clock.now(), json('{"intervention_id": ?}'))` — both within `conn.execute("BEGIN") … conn.execute("COMMIT")`; event insert failure rolls back status update (FR-LOOP-01). Dismiss does NOT emit an event.
- [X] T069a [US1] Add `dao.py::approve_intervention_atomic(intervention_id, final_text=None)` — implements the two-statement transaction used by T069; mirror helper for `edit_intervention_atomic`. Keeps route thin and makes the transactional invariant testable in isolation.
- [X] T069b [US1] Write `tests/integration/test_approval_loop_closure.py` — (1) seed a `pending` intervention for deal D-0042; POST approve; assert exactly one `events` row with `event_type='comm_sent_agent_recommended'`, `deal_id='D-0042'`, `payload` referencing the `intervention_id`, `occurred_at` equal to current simulated clock. (2) Simulate event-insert failure (monkey-patch); assert intervention status remains `pending` (transaction rollback). (3) Dismiss path emits zero events. (FR-LOOP-01)
- [X] T070 [P] [US1] Create `src/hiive_monitor/web/templates/brief.html` — full page; sim-controls placeholder; includes `_brief_list.html`
- [X] T071 [P] [US1] Create `src/hiive_monitor/web/templates/_brief_list.html` — ordered list of `_brief_item.html` partials
- [X] T072 [P] [US1] Create `src/hiive_monitor/web/templates/_brief_item.html` — severity badge, issuer + size, one-line summary, `<details>` for reasoning (incl. enrichment chain section when `enrichment_chain` non-empty), intervention body, approve/edit/dismiss with `hx-post` + `hx-target` + `hx-swap="outerHTML"`
- [X] T073 [P] [US1] Create `src/hiive_monitor/web/templates/_intervention_edit.html` — inline form for editing draft body
- [X] T074 [US1] Add Alpine.js copy-to-clipboard on approve + toast dismissal in `_brief_item.html`
- [X] T075 [US1] Add "All Open Items" tab content: `_all_open_list.html` partial + filter chip rendering
- [X] T076 [US1] Add "handled" badge rendering in `_brief_item.html` when status ∈ {approved, edited, dismissed} (FR-029a)
- [X] T077 [US1] Real-time polling: `hx-trigger="every 30s"` on brief container conditional on `CLOCK_MODE=real_time` (FR-032a)
- [X] T077a [US1] Implement `src/hiive_monitor/web/routes/status.py` and `templates/partials/_status_bar.html`. Route returns `last_tick_at`, `deals_processed_this_tick`, and `scorecard_url` as a rendered partial. Included in `base.html` via HTMX OOB swap triggered by the tick-completion event. Depends on T069 and T037. Maps to FR-032.
- [X] T078 [US1] Write `tests/smoke/test_brief_route.py` — GET /brief, POST approve, POST dismiss; assert fragment responses

**Checkpoint**: US1 + US3 + US4 = end-to-end demo path.

---

## Phase 6: User Story 5 — Simulation Controls (Priority: P2)

**Goal**: POST `/sim/advance {days:N}` advances clock by N days, fires N sequential ticks, returns refreshed brief.

**Independent Test**: POST `/sim/advance` with `days=7` creates 7 `ticks` rows, returns `_brief_list.html` fragment. 400 in real-time mode.

- [X] T079 [US5] Implement `src/hiive_monitor/web/routes/sim.py` — POST `/sim/advance` with `days: int`; 400 if `CLOCK_MODE != simulated`; loops `for _ in range(days): clock.advance(1); run_tick("simulated")`; returns refreshed brief fragment (FR-023a)
- [X] T080 [US5] Create `src/hiive_monitor/web/templates/_sim_controls.html` — current simulated time display, number input, "Advance N days" button with `hx-post="/sim/advance"` + `hx-target="#brief-list"`; renders only in simulated mode
- [X] T081 [US5] Include `_sim_controls.html` in `base.html` header slot
- [X] T082 [US5] Write `tests/smoke/test_sim_advance.py` — POST days=3 → 3 ticks; 400 in real_time mode

**Checkpoint**: Demo loop complete — advancing clock re-reasons and re-ranks the brief.

---

## Phase 7: User Story 2 — Per-Deal View (Priority: P1)

**Goal**: GET `/deals/{deal_id}` returns full deal picture: facts header, event timeline, agent observation history with enrichment-chain visibility, intervention history with status.

**Independent Test**: GET `/deals/D-0042` returns HTML with facts header, timeline, observation list (each with expandable reasoning + enrichment chain if triggered), intervention list with status badges.

- [X] T083 [US2] Implement `src/hiive_monitor/web/routes/deals.py` — GET `/deals/{deal_id}` loads deal, events, observations (ordered by tick), interventions; full page or HTMX fragment
- [X] T084 [P] [US2] Create `src/hiive_monitor/web/templates/deal_detail.html` — facts header, three-section layout: event timeline, observation history, intervention history
- [X] T085 [P] [US2] Create `src/hiive_monitor/web/templates/_observation_row.html` — expandable reasoning including: dimensions evaluated, severity, full reasoning text, enrichment chain (collapsible sub-section: "Agent sought additional context" with per-round tool + rationale + summary — visible only when `enrichment_chain` non-empty, per FR-AGENT-06)
- [X] T086 [P] [US2] Create `src/hiive_monitor/web/templates/_event_row.html` and `_intervention_row.html`. `_event_row.html` MUST branch on `event_type`: `comm_sent_agent_recommended` renders with a distinct agent-recommendation badge/icon and the labelled text "outreach sent via agent recommendation" (with a link to the referenced `intervention_id` from the payload), visually distinguishing it from organic `comm_outbound` / `comm_inbound` rows (FR-LOOP-03).
- [X] T086a [P] [US2] Write `tests/smoke/test_timeline_comm_sent_agent.py` — seed a deal with one `comm_outbound` and one `comm_sent_agent_recommended` event; GET `/deals/{id}`; assert the HTML contains the agent-recommendation indicator class/label exactly once and it attaches to the correct event row (FR-LOOP-03).
- [X] T087 [US2] Wire deal-row links in `_brief_item.html` and `_all_open_list.html` to `/deals/{id}` with `hx-push-url="true"`
- [X] T088 [US2] Write `tests/smoke/test_deal_detail.py` — GET /deals/{id}; assert observations + interventions present

**Checkpoint**: US1 + US2 + US3 + US4 + US5 = all three analyst screens.

---

## Phase 8: User Story 6 — Audit Trail & Debug View (Priority: P1)

**Goal**: Every agent output persisted with reasoning + correlation IDs. `?debug=1` shows raw structured logs. Enrichment chain readable from the deal view.

**Independent Test**: GET `/debug/tick/{tick_id}?debug=1` returns log events; `_observation_row.html` for an enriched deal shows the enrichment chain section.

- [X] T089 [US6] Add JSONL log sink in `logging.py` — writes to `out/logs.jsonl` alongside stdout; every LLM call event includes `tick_id`, `deal_id`, `call_name`, `latency_ms`; enrichment events include `tool_called`, `enrichment_count`
- [X] T090 [US6] Implement `src/hiive_monitor/web/routes/debug.py` — GET `/debug/tick/{tick_id}` and `/debug/deal/{deal_id}`; reads `out/logs.jsonl`, filters by correlation ID
- [X] T091 [US6] Create `src/hiive_monitor/web/templates/debug.html` — table of log records; filter chips by call_name
- [X] T092 [US6] Add conditional "Open debug view" link on `_brief_item.html` and `deal_detail.html` when `?debug=1` is present on parent page
- [X] T093 [US6] Write `tests/smoke/test_debug_view.py` — after one tick, debug route returns log records tied to that tick

**Checkpoint**: All six analyst-facing stories (US1–US6) wired and demo-ready.

---

## Phase 9: User Story 7 — Evaluation Harness (Priority: P1, parallel lane)

**Goal**: `make eval` runs 15 YAML golden scenarios, prints scorecard.

**NOTE**: Per Constitution VI — this lane starts alongside Phase 3/4. The runner (T094–T098) and first 5 scenarios (T099–T103) must exist by end of Phase 5 (when US1 is first demo-able). Remaining scenarios (T104–T113) complete before Phase 11 polish.

**Independent Test**: `make eval` exits 0 on ≥13/15; `eval_results/scorecard_<timestamp>.md` written.

### Harness runner

- [X] T094 [US7] Implement `src/hiive_monitor/eval/loader.py::load_scenario(path) -> Scenario` — Pydantic model per [contracts/eval-scenario-schema.md](./contracts/eval-scenario-schema.md)
- [X] T095 [US7] Implement `src/hiive_monitor/eval/runner.py::run_scenario(scenario) -> ScenarioResult` — truncates domain tables, applies setup, sets SimulatedClock, runs one Monitor tick, evaluates assertions
- [X] T096 [US7] Implement `src/hiive_monitor/eval/assertions.py` — evaluators for: `risk_signals`, `severity`, `intervention`, `brief_rank`, `no_intervention`, `enrichment_fired` (new: asserts whether any enrichment round occurred — useful for FR-AGENT-05 scenarios); substring + `re:`-prefix regex support
- [X] T097 [US7] Implement `src/hiive_monitor/eval/scorecard.py` — aggregate precision/recall per `RiskDimension`, 4×4 severity confusion matrix; writes Markdown scorecard to `eval_results/scorecard_<timestamp>.md` and prints human summary
- [X] T098 [US7] Implement `src/hiive_monitor/eval/__main__.py` — `python -m hiive_monitor.eval --all` CLI; wire `make eval`

### Golden scenarios — detection (8 scenarios, all parallel after T094–T098)

- [X] T099 [P] [US7] Author `src/hiive_monitor/eval/scenarios/detection_rofr_7d.yaml` — `rofr_pending`, 7 days to deadline, 14-day silence → severity=act, `deadline_proximity` + `communication_silence` triggered, outbound_nudge
- [X] T100 [P] [US7] Author `detection_rofr_2d.yaml` — 2 days to deadline → severity=escalate, internal_escalation
- [X] T101 [P] [US7] Author `detection_stage_aging_docs.yaml` — `docs_pending` >2× baseline dwell → `stage_aging` triggered
- [X] T102 [P] [US7] Author `detection_comm_silence.yaml` — `counterparty_nonresponsiveness` deterministic check triggered
- [X] T103 [P] [US7] Author `detection_missing_prereq.yaml` — structured blocker unresolved → `missing_prerequisites` triggered
- [X] T104 [P] [US7] Author `detection_unusual_first_time_buyer.yaml` — `is_first_time=true` + 3× typical size → `unusual_characteristics` triggered
- [X] T105 [P] [US7] Author `detection_enrichment_comm_silence_explained.yaml` — comm silence flagged, but `fetch_communication_content` returns message explaining legal hold → `enrichment_fired=true`, severity lower than raw silence would imply. Tests FR-AGENT-05 trigger #1.
- [X] T106 [P] [US7] Author `detection_healthy_negative.yaml` — no dimensions triggered → severity=informational, `no_intervention=true`

### Golden scenarios — prioritization (4 scenarios)

- [X] T107 [P] [US7] Author `prioritization_escalate_over_act.yaml` — two deals; escalate must rank above act
- [X] T108 [P] [US7] Author `prioritization_nearer_deadline_first.yaml` — tie on severity; nearer `rofr_deadline` must rank higher
- [X] T109 [P] [US7] Author `prioritization_brief_top_3.yaml` — target deal at rank ≤3 among 5 deals
- [X] T110 [P] [US7] Author `prioritization_open_carryover.yaml` — prior-tick open intervention appears in brief

### Golden scenarios — intervention quality (3 scenarios)

- [X] T111 [P] [US7] Author `intervention_outbound_references_deadline.yaml` — `body_must_contain` issuer name + deadline date; `body_must_not_contain` TODO/placeholder
- [X] T112 [P] [US7] Author `intervention_internal_has_next_step.yaml` — `suggested_next_step` non-empty and issuer-specific
- [X] T113 [P] [US7] Author `intervention_brief_entry_has_action.yaml` — `recommended_action` present and deal-specific

### Golden scenarios — adversarial calibration (4 scenarios)

**Purpose**: Test the agent's handling of uncertainty, conflicting signals, and prior history. These don't test detection — they test *calibration*. The agent should hold a measured middle position, not collapse to either extreme.

- [ ] T113a [P] [US7] Author `adversarial_strong_single_signal.yaml` — deal with strong `deadline_proximity` (2 days to ROFR) but four otherwise healthy signals (responsive issuer, on-stage, no blockers, no silence); assert `severity_lte: act` — one strong signal should not produce escalate when the surrounding picture is clean
- [ ] T113b [P] [US7] Author `adversarial_conflicting_comm.yaml` — 14-day comm silence as the surface signal, but `fetch_communication_content` reveals an earlier message explicitly explaining a legal hold; assert `enrichment_tool_called: fetch_communication_content` and `severity_lte: watch` — enrichment context must suppress the raw silence signal
- [ ] T113c [P] [US7] Author `adversarial_prior_breakage_healthy_now.yaml` — buyer party with `prior_breakage_count: 3` but deal is currently on-stage, within deadline, and recently responsive; assert `severity_lte: watch`, `no_intervention: true` — historical counterparty risk alone should not trigger an intervention when present signals are clean
- [ ] T113d [P] [US7] Author `adversarial_balanced_opposing_signals.yaml` — `stage_aging` triggered (dwell at 2× baseline) but `deadline_proximity` fine, recent `comm_inbound` present; assert `severity_gte: watch` AND `severity_lte: act` — agent must hold a calibrated middle, not over-escalate on aging alone nor dismiss the signal entirely

**Checkpoint**: US7 standalone — `make eval` runs ≥13/15 green; enrichment scenario (T105) is one of the 15.

---

## Phase 10: Seed Data + Demo Flow

**Purpose**: 30 convincing synthetic deals per Constitution IX. `make demo` opens a populated brief in under 10 s.

- [X] T114 Implement `src/hiive_monitor/seed/issuers.yaml` + loader — 10 real Hiive-listed issuers (SpaceX, Stripe, Anthropic, Perplexity, Cerebras, Groq, Databricks, Canva, Rippling, Ramp) with engineered `response_days`, `rofr_window`, `multi_layer` flags
- [X] T115 [P] Implement `src/hiive_monitor/seed/parties.py` — realistic buyer/seller names; engineered `is_first_time` + `prior_breakage_count`
- [X] T116 Implement `src/hiive_monitor/seed/deals.py::generate_deals(count: int = 30)` — distributed across 9 stages; `count` param is stretch plug-in for 40-deal scale
- [X] T117 Implement `src/hiive_monitor/seed/events.py` — stage-transition + comm events anchored to `stage_entered_at`; ≈75 communications in Transaction Services voice
- [X] T118 Implement five engineered-issue deals in `seed/deals.py` — Stripe ROFR-cliff, Anthropic docs-stalled, Perplexity signing-silence, Cerebras large+first-time, Databricks clean control
- [X] T119 Implement `src/hiive_monitor/seed/__main__.py` — `python -m hiive_monitor.seed` truncates + reseeds `domain.db`
- [X] T120 Wire `make seed` and `make demo` — demo = seed + 3 warm-up ticks + `uvicorn` + open browser at `/brief`
- [X] T121 [P] Manual voice review pass — read 10 generated comm bodies; fix any that sound generic (Constitution IX)

---

## Phase 11: Polish & Cross-Cutting

**Purpose**: Hour 48–55 polish. Design pass, README, writeup.

- [X] T122 Tailwind CLI final build in `make setup`: `npx tailwindcss -i static/input.css -o static/app.css --minify`
- [X] T123 [P] Design pass — run `/impeccable teach` to establish design context in `.impeccable.md`, then apply `/layout` + `/typeset` + `/polish` to `brief.html` and `deal_detail.html`
- [ ] T123a [P] Auto-expand escalate items in `brief.html` `intervention_row` macro — change Alpine `x-data` `expanded` initial value from `false` to `{{ 'true' if item.severity == 'escalate' else 'false' }}` so the analyst sees reasoning and action buttons immediately for the most urgent items without an extra click
- [X] T124 [P] Empty states: "no items in today's brief", "no observations yet for this deal", "no enrichment performed" (when enrichment_chain empty in observation row)
- [X] T125 [P] Error states: LLM-error observation rendering, 404 deal, 400 sim-advance in real_time mode toast
- [X] T125a [US5] Timeout for crashed background ticks — `/api/tick/{tick_id}/status` in `web/routes/main.py` polls every 2s with no upper bound; if `run_tick` crashes before `dao.start_tick()` inserts the tick row (or during the graph run), the client polls forever because the status endpoint keeps returning the "running" fragment. Add server-side detection: track tick-dispatch time (in-memory dict or a `ticks.dispatched_at` column) and, after ~30s with no completion, return a terminal "tick failed — check logs" div with no `hx-trigger` so the HTMX polling loop terminates. Alternative: cap client polling via `hx-trigger="every 2s, count:30"` and render a timeout state on final swap.
- [X] T126 README — what/why (Hiive language), 3-command quickstart, screenshot, architecture diagram (Mermaid), current scorecard snapshot
- [X] T127 [P] `docs/architecture.md` — Mermaid source including Investigator 7-node graph with enrichment loop shown
- [X] T128 [P] `docs/writeup.md` — 400-word submission writeup; must mention enrichment-loop design as the "genuinely agentic" property
- [X] T129 [P] `docs/reflection.md` — 1-paragraph reflection
- [X] T130 `make clean` target — removes `*.db`, `out/`, `__pycache__`
- [ ] T131 Full end-to-end validation: `make setup && make eval && make demo` from clean clone; fix any breakage. Targets: SC-001 (setup+demo <3 min), SC-006 (≥13/15 eval including enrichment scenario T105).

---

## Stretch Queue (start only after Phase 11; hard-start cut-offs per plan.md)

- [ ] TS01 [STRETCH] Fourth intervention type `status_recommendation` — add to `models/interventions.py` discriminated union + new prompt + severity-rubric branch. Cut-off: hour 48.
- [ ] TS02 [STRETCH] Scale to 40 deals + 23 scenarios — `generate_deals(count=40)` already parametric (T116); add 8 YAML fixtures. Cut-off: hour 50.
- [ ] TS03 [STRETCH] Screen 3 standalone "Reviewer Queue" page — add `web/routes/queue.py` + `templates/queue.html`; reuses `_all_open_list.html`. Cut-off: hour 51.
- [ ] TS04 [STRETCH] Tier 2 LLM-as-judge — `eval/judge.py` + `judge_rubric:` block in 3 intervention_quality YAMLs + Sonnet grader. Cut-off: hour 49.
- [ ] TS05 [STRETCH] Simulated-mode autoplay (SSE stream advancing N days over M seconds). Cut-off: hour 52.
- [ ] TS06 [STRETCH] Document collection tracking (BUILD_PLAN §9.2 #6) — add `required_documents_by_stage` map to `models/stages.py`; add `documents_received` column to `deals` via `ALTER TABLE deals ADD COLUMN documents_received TEXT DEFAULT '[]' NOT NULL` (SQLite errors on duplicate-column re-run, so wrap the `ALTER` in a try/except catching `sqlite3.OperationalError` and treating it as a no-op — idempotent migration). Add this `ALTER` to a `stretch_migrations()` function in `db/schema.py` that is called only when the TS06 feature flag is enabled. Enrich `DealSnapshot` with `missing_documents` derived field; specialize `intervention_outbound_nudge.py` prompt so drafts targeting a `docs_pending` deal name the exact missing document by filename from `missing_documents`. Estimated 3h. Cut-off: **hour 48**.
- [ ] TS07 [STRETCH] ROFR outcome tracking (BUILD_PLAN §9.2 #7) — extend `Stage` enum with `rofr_exercised` (between `rofr_pending` and `signing`); add `models/rofr_exercise.py` (`RofrExercise` with `assignee_party_id`, `original_buyer_party_id`, `exercised_at`); new Investigator branch node `handle_rofr_exercised` that drafts two interventions in one tick — (a) outbound nudge to issuer-assignee coordinating substitute-buyer signing, (b) internal internal_escalation notifying TS of original-buyer fallout needing comms; log the exercise event to `issuers` history so `fetch_issuer_history` surfaces exercise rate for future deals. Add YAML fixture `detection_rofr_exercised.yaml` to golden set. Estimated 4h. Cut-off: **hour 45**.
- [ ] TS08 [STRETCH] Intervention outcome tracking — close the learning loop: after an intervention is approved, track whether `stage_transition` or `comm_inbound` events followed on the same deal within 7 days; surface the outcome stats in `assess_sufficiency` reasoning as e.g. "of 8 prior outbound nudges on this issuer, 7 resulted in a response within 7 days." Implementation: new `dao.py` function `get_intervention_outcomes(issuer_id, intervention_type)` joining approved `interventions` to subsequent `events` within a time window; add a fourth enrichment tool `fetch_intervention_outcomes` alongside the existing three (update `SufficiencyDecision.suggested_tool` Literal); engineer 3–4 historical settled deals in `seed/deals.py` with prior approved interventions + follow-on events; add one eval scenario `detection_outcome_history.yaml` asserting `enrichment_tool_called: fetch_intervention_outcomes` and that the intervention body references the outcome stat. Attribution is correlation not causation — phrase as "following a nudge, X of Y cases saw response" not "nudge caused response." Estimated 4h. Cut-off: **hour 50**.
- [ ] TS09 [STRETCH] Portfolio-level pattern detection — a tick-level pattern detector at portfolio scope, complementing the per-deal Investigator. Add `detect_portfolio_patterns(state)` node in `agents/monitor.py` running after `close_tick`; groups live deals by `(issuer_id, stage)`, compares cluster size to a 3-tick rolling average derived from prior `agent_observations` counts, flags any cluster where current count exceeds 2× rolling average; stores results as JSON in a new `signals` column on `ticks` (idempotent `ALTER TABLE` in `stretch_migrations()`); renders as a collapsible "Portfolio signals" section at the top of `brief.html` when `tick.signals` is non-empty. Detection is purely deterministic — no LLM call. This is intentionally agentic at a different scope than the per-deal Investigator: the per-deal agent sees deal-level risk; the portfolio detector sees systemic issuer-level stalls that no single deal observation can surface. Estimated 5h. Cut-off: **hour 52**.
- [ ] TS10 [STRETCH] Deal snooze — analyst can suppress a deal from the monitoring loop for N hours with a required reason, acknowledging out-of-band knowledge the agent cannot see. Schema: add `snoozed_until TEXT, snooze_reason TEXT` columns to `deals` via idempotent `ALTER TABLE` in `stretch_migrations()`; add `'snooze_created'` to `events.event_type` CHECK constraint; update `get_live_deals()` to filter `AND (snoozed_until IS NULL OR snoozed_until < current_simulated_time)`. New route `POST /deals/{deal_id}/snooze` with `hours: int = Form(48)` and `reason: str = Form(...)` — inserts `snooze_created` event to `events` (audit trail). UI: snooze button with reason textarea in brief expanded panel and deal detail header; "Snoozed until X — {reason}" badge on compact brief row when active. Snooze is semantically distinct from dismiss: dismiss is permanent and reason-free; snooze is temporary and reason-required. Estimated 4h. Cut-off: **hour 51**.
- [ ] TS11 [STRETCH] Eval failure mode analysis — extend `write_scorecard` in `eval/runner.py` with cross-scenario diagnostics so the harness explains *why* scenarios failed, not just that they did. Add: (1) per-`RiskDimension` precision/recall table — for each dimension, count scenarios where it was expected (appears in assertions) vs. correctly triggered (assertion passed); (2) 4×4 severity confusion matrix (expected row × actual column) across all scenarios; (3) per-failure root-cause hint — when a `severity` assertion fails, correlate with which dimension assertions also failed in the same scenario and emit e.g. "severity under-estimated; likely missed dimension: deadline_proximity (also failed)." All computed from the existing `assertion_results` tuples — no new API calls, no schema changes. Estimated 2.5h. Cut-off: **hour 53**.
- [ ] TS12 [STRETCH] Batch approve Watch items — add "Approve all Watch" button in the All Open tab header of `_all_open_list.html`, rendered only when ≥1 pending watch-severity intervention exists; new route `POST /interventions/batch-approve` with `severity_filter: str = Form('watch')` that loops `approve_intervention_atomic` for each matching pending intervention and returns a refreshed list fragment; `hx-confirm` on the button shows the count of items that will be affected. Scope is strictly Watch-severity: act/escalate items require individual review. Estimated 2h. Cut-off: **hour 53**.

---

## Dependencies & Execution Order

### Phase dependencies

| Phase | Depends on | Blocks |
|---|---|---|
| Phase 1 Setup | — | Phase 2 |
| Phase 2 Foundation | Phase 1 | All US phases |
| Phase 3 US3 Monitor | Phase 2 | Phase 5 (needs run_tick) |
| Phase 4 US4 Investigator | Phase 2; can start same time as Phase 3 | Phase 5 (needs observations) |
| Phase 5 US1 Brief | Phase 3 + Phase 4 | — |
| Phase 6 US5 Sim | Phase 3 (needs run_tick) | — |
| Phase 7 US2 Per-deal | Phase 3 + Phase 4 (needs observations/interventions) | — |
| Phase 8 US6 Audit | Phase 2 logging; alongside Phase 3 | — |
| Phase 9 US7 Eval | Phase 2; runner lands at Phase 3/4 time; 5 scenarios by Phase 5 | — |
| Phase 10 Seed | Phase 2 schema; seed data usable from hour 8 | Phase 5 demo |
| Phase 11 Polish | All MVP phases | — |

### Critical path within Phase 4 (Investigator)

T030 (graph_state) → T042–T047 parallel (dimensions) → T048 (validator) → T049 (severity) → T050 (assess_sufficiency prompt) → T051 (N3 node) → T052 (enrich_context prompt) → T053 (N4 node) → T054+T055 (enrichment tests) → T056–T058 parallel (intervention drafters) → T059 (full 7-node graph) → T060 (compile) → T061 (suppression) → T062 (fan-out wire) → T063+T064 (integration tests)

### Parallel opportunities summary

- **Phase 2 models** (T019–T023, T023a): all 6 parallel
- **Phase 3 Monitor** (T035–T039 + T036a/T036b/T036c): sequential per dependency chain except T036b (config) and T036c (test) which can land in parallel once T036a exists
- **Phase 3 Monitor critical path**: T035 → T036 → T036a (suppression multiplier) → T037 → T038 → T039. T036a MUST complete before T037 because the tick-completion logic reads the suppression state.
- **Phase 4 dimension prompts** (T042–T047): all 6 parallel
- **Phase 4 intervention drafters** (T056–T058): 3 parallel
- **Phase 5 templates** (T070–T073): 4 parallel
- **Phase 7 templates** (T084–T086 + T086a): 4 parallel
- **Phase 9 scenarios** (T099–T113 plus the two enrichment-trigger fixtures added for C11): all 17 parallel after runner lands
- **Phase 10 seed**: T114+T115 parallel; T116–T119 sequential

---

## Parallel Execution Examples

### Phase 2 models (launch all at once)
```text
Task: "Implement src/hiive_monitor/models/stages.py"
Task: "Implement src/hiive_monitor/models/risk.py"
Task: "Implement src/hiive_monitor/models/interventions.py"
Task: "Implement src/hiive_monitor/models/brief.py"
Task: "Implement src/hiive_monitor/models/snapshot.py"
```

### Phase 4 dimension evaluators (launch after T030 + T048 requirements known)
```text
Task: "Implement src/hiive_monitor/llm/deterministic/counterparty_responsiveness.py"
Task: "Implement src/hiive_monitor/llm/prompts/risk_stage_aging.py"
Task: "Implement src/hiive_monitor/llm/prompts/risk_deadline_proximity.py"
Task: "Implement src/hiive_monitor/llm/prompts/risk_communication_silence.py"
Task: "Implement src/hiive_monitor/llm/prompts/risk_missing_prerequisites.py"
Task: "Implement src/hiive_monitor/llm/prompts/risk_unusual_characteristics.py"
```

### Phase 9 scenarios (launch all after T094–T098 land)
```text
Task: "Author scenarios/detection_rofr_7d.yaml"
Task: "Author scenarios/detection_enrichment_comm_silence_explained.yaml"
... (all 15 in parallel)
```

---

## Implementation Strategy

### MVP-first (Constitution I, BUILD_PLAN §9)

1. Phase 1 (2h) → Phase 2 (6h) = Foundation by hour 8
2. Phase 3 + Phase 4 in parallel (with Phase 9 runner alongside) = Agent reasoning by hour 26
3. Phase 5 = Daily Brief demo by hour 36
4. Phase 6 + Phase 7 + Phase 8 = Full three-screen + audit by hour 48
5. Phase 10 + Phase 11 = Demo polish + eval ≥13/15 by hour 55
6. Stretch queue from hour 48 only, with hard cut-offs

### Incremental checkpoints

| Hour | Milestone |
|---|---|
| 8 | Foundation green; clock + DB + LLM client + Pydantic models working |
| 16 | Seed data ready; Monitor screening emitting queues |
| 26 | Full Investigator including enrichment loop reasoning correctly on engineered scenarios |
| 36 | `make demo` opens a live Daily Brief |
| 48 | All three analyst screens + audit trail + sim controls |
| 55 | Polished, README-complete, eval ≥13/15, submission-ready |

---

## Notes

- `[P]` = different files, no blocking dependency. Safe to parallelize.
- Every LLM call routes through `llm/client.py` — the single reliability chokepoint.
- No module exposes a `send()` method for interventions (Constitution II — enforced by absence).
- No code calls `datetime.now()` — enforced by grep test T011.
- Domain DB and checkpointer DB are separate files — never merge.
- `enrichment_count` lives in `InvestigatorState`, initialized to 0. The N3→N4→N3 loop is what makes the Investigator a genuine agent, not a workflow.
- The hard cap (route to N5 when `enrichment_count >= 2`) is enforced in the N3 graph routing logic, not in the prompt — the LLM cannot override it.
- **Communication loop closure** (FR-LOOP-01/02/03): analyst approval writes a `comm_sent_agent_recommended` event atomically with the status update (T069); Monitor reads that event during screening to apply a 0.2× suppression multiplier for `SUPPRESSION_TICKS` ticks (T036a); the per-deal timeline renders the event with a distinct agent-recommendation indicator (T086). The distinct `event_type` is the single source of truth for all three behaviors.
- Commit after each task or logical cluster. Stop at any checkpoint to validate independently.
