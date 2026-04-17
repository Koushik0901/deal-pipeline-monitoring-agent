# Implementation Plan: Deal Pipeline Monitoring Agent

**Branch**: `claude/angry-perlman-74f887` | **Date**: 2026-04-16 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `specs/001-deal-pipeline-monitor/spec.md`

## Summary

Hiive's Transaction Services analysts each juggle 40–60 live secondary-market deals. This feature ships an always-on monitoring system built around two LangGraph agents: a **Pipeline Monitor** supervisor that screens every live deal on each tick (cheap Haiku pass, attention score per deal, cap of 5 investigations per tick by top score over a configurable threshold), and a **Deal Investigator** per-deal agentic sub-graph with a variable execution path: it observes, runs six risk-dimension evaluations in parallel, then reasons about whether results are sufficient (the `assess_sufficiency` node) before scoring severity. When signals are ambiguous or compounding, it enters a bounded enrichment loop (max 2 rounds) that calls one of three context-fetching tools. Most deals resolve in a single pass; ambiguous deals adapt. For **act**/**escalate** deals it drafts one of three intervention types (outbound nudge, internal escalation, brief entry).

The analyst never sees a black box: every observation carries structured reasoning, the audit trail ties every LLM call to a deal+tick correlation ID, and all drafts remain pending until the analyst explicitly approves, edits, or dismisses them. A dual-mode clock (real-time or simulated, selectable by env var) drives APScheduler; advancing the simulated clock by N days fires N sequential daily ticks. The analyst interface is FastAPI + Jinja + HTMX + Alpine + Tailwind — two MVP screens (Daily Brief and Per-Deal Detail) with debug views behind `?debug=1`. A two-tier evaluation harness ships alongside the agent: Tier 1 (15 deterministic YAML scenarios) is MVP; Tier 2 (LLM-as-judge) is stretch. Everything packages via a `Makefile`: `setup`, `seed`, `run`, `eval`, `demo`, `clean`.

Depth over breadth is the governing discipline: MVP slice (≈55 h, BUILD_PLAN §9.1) ships first, fully polished; stretch items (Screen 3 standalone, 4th intervention type, 40-deal scale, 23-scenario scale, Tier 2 judge) only start after MVP completion with hard-start cut-offs enforced (BUILD_PLAN §9.2).

## Technical Context

**Language/Version**: Python 3.11+ (single language, single repo).
**Primary Dependencies**: FastAPI, LangGraph (+ `langgraph-checkpoint-sqlite`), Anthropic Python SDK, Pydantic v2, APScheduler, Jinja2, HTMX (pinned), Alpine.js (pinned), Tailwind CSS (CLI build), structlog, PyYAML, pytest, uv (dep manager).
**Storage**: SQLite — two separate databases: `domain.db` (deals/events/observations/interventions/issuers/parties/ticks) and `agent_checkpoints.db` (LangGraph checkpointer).
**Testing**: pytest for unit + integration; Tier 1 eval harness is its own runner reading YAML fixtures and asserting structured assertions. Tier 2 LLM-as-judge (stretch) uses the same runner with an added rubric-grading pass.
**Target Platform**: Developer laptop (macOS/Linux/Windows with Python 3.11). Desktop browser for the analyst UI. No Docker.
**Project Type**: Web service (single-process FastAPI + in-process APScheduler + server-rendered HTML) with a sibling eval CLI.
**Performance Goals**: Default real-time tick cadence 60 s; simulated-mode target of 30 simulated days in ~30 s (≈1 s per tick end-to-end when investigation queue is empty or cached). Per-tick LLM budget:
- ~1 Haiku call per live deal for screening
- Up to 5 Investigator runs, each with:
  - **Base case (no enrichment, ~80% of deals)**: five Sonnet dimension calls + one `assess_sufficiency` call + one `score_severity` call + up to one draft call = **5–6 Sonnet calls**
  - **Enrichment case (~20–30% of deals, per FR-AGENT-05 trigger situations)**: base calls + one `assess_sufficiency` call per enrichment round (max 2) + one `enrich_context` tool-selection call per round = **7–8 Sonnet calls**
  - Expected cost increase per tick vs. base case: **~15–20%**

`make eval` must complete 15 scenarios in under 5 minutes.
**Constraints**: Every timestamp routes through the Clock abstraction (zero direct `datetime.now()` calls in application code — enforced by a grep test). Monitor loop idempotent on `(tick_id, deal_id)`. Every LLM call schema-validated via Pydantic with a single corrective reprompt on parse failure. No autonomous external actions — drafts only.
**Scale/Scope**: MVP seed = 30 deals across 10 issuers, 5 engineered-issue scenarios, ≈150 events, ≈75 communications, 15 golden eval scenarios. Stretch scales to 40 deals, 8 engineered scenarios, 23 golden scenarios + Tier 2 judge on 10–15 of them.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | How the plan honors it | Gate |
|---|---|---|
| **I. Depth Over Breadth, MVP-First** | MVP slice is the primary architecture; stretch items live in §Stretch Queue with cut-off hours and named plug-in points. Decision protocol at hour 40/50 built into the build sequencing. | ✅ |
| **II. Human-in-the-Loop (non-negotiable)** | No code path sends external communication or mutates a system of record. All interventions remain `pending` until the analyst acts. Intervention module exposes only `draft()` + `persist_as_pending()` — no `send()` method by design. | ✅ |
| **III. Domain-Accurate Language** | Stage enum, column names, prompt templates, UI labels all use Hiive vocabulary (`bid_accepted`, `rofr_pending`, `transfer_agreement`, `accreditation`, `settlement`, `breakage`, issuer counsel). FR-033 post-generation assertion ensures drafts cite concrete facts. | ✅ |
| **IV. Explainable & Inspectable Reasoning** | Every observation row persists a structured `reasoning` JSON object with dimensions evaluated, cited facts, severity rationale. Debug view behind `?debug=1` exposes raw LLM prompts/responses with correlation IDs. | ✅ |
| **V. Reliability Patterns First-Class** | Pydantic-validated LLM outputs; timeouts + exponential backoff in LLM client; idempotency on `(tick_id, deal_id)` via compound UNIQUE index; LangGraph checkpointer for mid-tick restart; bounded output spaces (enums + length caps); structlog with correlation IDs. | ✅ |
| **VI. Evaluation Is Part of the Product** | Tier 1 harness ships with ≥5 scenarios by end of USER STORY 1. Full 15 ship with MVP. `make eval` is a first-class command. Scorecard linked from README. | ✅ |
| **VII. Reviewer-Readable Code** | Shallow file tree keyed to domain concerns. Each module single responsibility. Comments only for non-obvious invariants (clock discipline, ROFR mechanics, idempotency). Type hints where they clarify. | ✅ |
| **VIII. Honest About Assumptions** | README + `docs/assumptions.md` document dwell-time baselines, ROFR window defaults, Transaction Services email cadence, "stalled" definition. Inline assumption comments at encoding sites. | ✅ |
| **IX. Synthetic Data Must Be Convincing** | 10 real Hiive issuers with engineered metadata. Voice-calibrated comm generator with ≈10 hand-written exemplars as seed; manual review pass. | ✅ |
| **X. Submission-Shaped From Day One** | `make demo` path polished alongside each user story. README + writeup drafts updated at every milestone. Scorecard auto-linked. | ✅ |

**Initial gate**: ✅ Pass. No principle conflicts. No complexity violations to justify.

## Project Structure

### Documentation (this feature)

```text
specs/001-deal-pipeline-monitor/
├── spec.md                      # /speckit.specify + /speckit.clarify output
├── plan.md                      # This file
├── research.md                  # Phase 0 — stack verification notes
├── data-model.md                # Phase 1 — domain tables, LangGraph state, Pydantic schemas, YAML fixture format
├── quickstart.md                # Phase 1 — make setup / make demo / make eval walkthrough
├── contracts/
│   ├── http-routes.md           # FastAPI route contracts (HTMX partials + JSON)
│   ├── llm-schemas.md           # Pydantic schemas for every LLM output
│   └── eval-scenario-schema.md  # YAML golden-scenario contract
├── checklists/
│   └── requirements.md          # Spec quality checklist
└── tasks.md                     # /speckit.tasks output (NOT created here)
```

### Source Code (repository root)

```text
pyproject.toml                   # uv-managed deps, Python 3.11+, ruff config
uv.lock                          # Pinned
Makefile                         # setup / seed / run / eval / demo / clean
README.md                        # What, why (in Hiive's language), 3-command quickstart, architecture diagram, screenshot, latest scorecard link, assumptions
.env.example                     # ANTHROPIC_API_KEY, APP_MODE=real_time|simulated, TICK_INTERVAL_SECONDS=60, LOG_FORMAT=human|json, ATTENTION_THRESHOLD=0.5
domain.db                        # Seeded SQLite domain DB (checked in)
agent_checkpoints.db             # Created on first run (gitignored)
input.css                        # Tailwind source

src/hiive_monitor/
├── __init__.py
├── app.py                       # FastAPI app + APScheduler lifecycle + Jinja setup
├── config.py                    # Pydantic Settings (env → typed config)
├── clock.py                     # Clock protocol + RealTimeClock + SimulatedClock + get_clock()
├── logging.py                   # structlog setup (human vs JSON) + correlation-ID contextvars
├── scheduler.py                 # APScheduler wiring; tick dispatcher; simulated-advance helper
├── db/
│   ├── schema.sql               # Domain DDL (deals, events, ticks, agent_observations, interventions, issuers, parties)
│   ├── migrate.py               # Idempotent CREATE-IF-NOT-EXISTS from schema.sql
│   └── dao.py                   # Thin typed data-access helpers
├── models/                      # Pydantic v2 models (domain + LLM I/O)
│   ├── deal.py                  # Deal, DealStage enum, Blocker
│   ├── event.py                 # Event, EventType enum: stage_transition | doc_received | doc_requested | comm_outbound | comm_inbound | comm_sent_agent_recommended
│   ├── observation.py           # AgentObservation, Reasoning
│   ├── intervention.py          # Intervention + OutboundNudge / InternalEscalation / BriefEntry sub-schemas
│   ├── risk.py                  # RiskDimension enum, RiskSignal, SeverityDecision, Severity enum
│   ├── brief.py                 # DailyBrief, BriefItem
│   ├── issuer.py
│   └── party.py
├── llm/
│   ├── client.py                # AnthropicClient wrapper: timeout, retry, structured output, one corrective reprompt, idempotency cache keyed on (tick_id, deal_id, call_name), structured logging
│   ├── prompts/
│   │   ├── screening.py         # Haiku → AttentionScore
│   │   ├── risk_stage_aging.py
│   │   ├── risk_deadline_proximity.py
│   │   ├── risk_communication_silence.py
│   │   ├── risk_missing_prerequisites.py
│   │   ├── risk_unusual_characteristics.py
│   │   ├── severity_rubric.py
│   │   ├── intervention_outbound_nudge.py
│   │   ├── intervention_internal_escalation.py
│   │   ├── intervention_brief_entry.py
│   │   └── daily_brief.py
│   └── deterministic/
│       └── counterparty_responsiveness.py  # Dimension #5 — no LLM
├── agents/
│   ├── monitor.py               # LangGraph supervisor: enumerate_live → screen → select_top_k → fan_out
│   ├── investigator.py          # LangGraph sub-graph: observe → evaluate_risks → decide_severity → (branch) draft_intervention → emit_observation
│   ├── brief_composer.py        # Ranks observations into Today's Priorities (top 5–7 act/escalate)
│   └── graph_state.py           # TypedDict state shapes for LangGraph
├── web/
│   ├── routes/
│   │   ├── brief.py             # GET /, /brief, /brief/all-open-items
│   │   ├── deal.py              # GET /deals/{id}  (+?debug=1)
│   │   ├── intervention.py      # POST /interventions/{id}/approve|edit|dismiss
│   │   ├── simulate.py          # POST /simulate/advance?days=N
│   │   └── status.py            # GET /status
│   ├── templates/
│   │   ├── base.html
│   │   ├── brief.html
│   │   ├── deal.html
│   │   ├── _brief_item.html     # HTMX partial
│   │   ├── _intervention_modal.html
│   │   ├── _observation_row.html
│   │   └── _debug_panel.html
│   └── static/
│       ├── tailwind.css         # Built by make setup
│       ├── htmx.min.js          # Pinned vendored
│       └── alpine.min.js        # Pinned vendored
├── seed/
│   ├── generate.py              # --deal-count (default 30), --engineered-scenario-count (default 5)
│   ├── issuers.yaml             # 10 Hiive issuers + metadata
│   ├── engineered_scenarios.yaml
│   └── comm_voice_examples.md   # Hand-written exemplars for voice calibration
└── eval/
    ├── runner.py
    ├── scorecard.py
    ├── scenarios/               # 15 YAML fixtures (MVP), extensible to 23
    └── judge.py                 # [STRETCH] Tier 2 LLM-as-judge

tests/
├── unit/
│   ├── test_clock.py            # Incl. grep test: no direct datetime.now() in src/
│   ├── test_idempotency.py
│   ├── test_severity_surfacing.py
│   ├── test_intervention_schemas.py
│   └── test_dao.py
├── integration/
│   ├── test_monitor_loop.py     # Tick atomicity + crash-restart
│   ├── test_investigator_branches.py
│   ├── test_simulated_advance.py  # N days → N ticks sequentially
│   ├── test_htmx_partials.py
│   └── test_intervention_suppression.py  # FR-024a
└── fixtures/

eval_results/                    # scorecard_*.md (gitignored except latest)
└── latest.md

docs/
├── architecture.md
└── assumptions.md
```

**Structure Decision**: Single-process web service. Shallow module tree keyed to domain concerns (`clock`, `agents`, `llm`, `db`, `web`, `seed`, `eval`). Justified because: (a) Constitution VII demands reviewer-readable structure — shallow beats hexagonal; (b) there's no second service to split into; (c) the 55-h MVP budget rejects per-layer ceremony.

## Architecture

### Component diagram (prose)

```text
                           ┌──────────────────────────────────────────────┐
                           │             Analyst's Browser                 │
                           │  HTMX + Alpine + Tailwind-styled Jinja        │
                           └────────────────────┬─────────────────────────┘
                                                │ HTTP + HTMX partials
                                                ▼
  ┌──────────────────────────────────────────────────────────────────────────┐
  │                         FastAPI app (single process)                      │
  │                                                                            │
  │   ┌────────────────────┐      ┌─────────────────────────────────────┐   │
  │   │   Web routes       │      │   APScheduler (in-process)           │   │
  │   │   / , /deals/{id}, │      │   real_time:  tick every 60s         │   │
  │   │   /interventions/…,│      │   simulated:  advance_by(N) → N      │   │
  │   │   /simulate/advance│      │     sequential daily ticks (FR-023a) │   │
  │   │   /status, debug=1 │      └──────────────────┬──────────────────┘   │
  │   └──────┬─────────────┘                          │                      │
  │          │                                        ▼                      │
  │          │     ┌─────────────────────────────────────────────────┐     │
  │          │     │        LangGraph supervisor (Pipeline Monitor)   │     │
  │          │     │                                                   │     │
  │          │     │  1. enumerate live deals (exclude settled/broken) │     │
  │          │     │  2. Haiku screening → raw attention score per deal │     │
  │          │     │     suppression check: if comm_sent_agent_rec'd    │     │
  │          │     │     event within last SUPPRESSION_TICKS ticks →   │     │
  │          │     │     multiply raw score × 0.2 (FR-LOOP-02)         │     │
  │          │     │  3. threshold gate + top-5 cap (FR-001a)          │     │
  │          │     │  4. for each in queue: invoke Investigator        │     │
  │          │     │  5. emit pipeline-wide observation                │     │
  │          │     └──────────────────┬────────────────────────────────┘     │
  │          │                        │                                       │
  │          │                        ▼                                       │
  │          │     ┌─────────────────────────────────────────────────┐     │
  │          │     │     Deal Investigator sub-graph (agentic)        │     │
  │          │     │                                                   │     │
  │          │     │  N1 observe: pull all deal facts from domain DB  │     │
  │          │     │  N2 evaluate_dimensions: 5 Sonnet calls parallel  │     │
  │          │     │     + 1 deterministic → 6 RiskSignals            │     │
  │          │     │  N3 assess_sufficiency (Sonnet): ← THE AGENT NODE│     │
  │          │     │     reads enrichment_count from state             │     │
  │          │     │     if count ≥ 2 → N5 (hard cap, FR-AGENT-04)   │     │
  │          │     │     elif unambiguous → N5 score_severity          │     │
  │          │     │     elif ambiguous/compounding → N4 enrich_context│     │
  │          │     │  N4 enrich_context (conditional, max 2 iters):   │     │
  │          │     │     LLM selects tool + reasons why; calls one of: │     │
  │          │     │       fetch_communication_content(deal_id)        │     │
  │          │     │       fetch_prior_observations(deal_id)           │     │
  │          │     │       fetch_issuer_history(issuer_id)             │     │
  │          │     │     increments enrichment_count; loops → N3       │     │
  │          │     │  N5 score_severity: Sonnet rubric → Severity     │     │
  │          │     │  BRANCH: severity ∈ {act, escalate} AND no open  │     │
  │          │     │          pending intervention (FR-024a)?          │     │
  │          │     │       → N6 draft_intervention                    │     │
  │          │     │     else → N7 directly                           │     │
  │          │     │  N7 emit_observation: persist reasoning object    │     │
  │          │     │     incl. full enrichment chain (FR-AGENT-06)    │     │
  │          │     └──────────────────┬────────────────────────────────┘     │
  │          │                        │                                       │
  │          ▼                        ▼                                       │
  │   ┌───────────────────────────────────────────────────────────────┐    │
  │   │                   LLM client abstraction                       │    │
  │   │  Anthropic SDK → Haiku | Sonnet                                │    │
  │   │  • Pydantic-validated structured output                        │    │
  │   │  • timeout + exp. backoff                                      │    │
  │   │  • one corrective reprompt on validation failure               │    │
  │   │  • idempotency cache keyed on (tick_id, deal_id, call_name)    │    │
  │   │  • structlog with correlation IDs                              │    │
  │   └───────────────────────────────────────────────────────────────┘    │
  │                                                                            │
  │                              Clock abstraction                             │
  │   get_clock() → RealTimeClock | SimulatedClock(injected_now)              │
  │   Every module reads time here. Zero direct datetime.now() calls.          │
  └──────────────────────────────────────────────────────────────────────────┘
              │                                     │
              ▼                                     ▼
     ┌──────────────────┐                 ┌──────────────────────┐
     │   domain.db      │                 │ agent_checkpoints.db │
     │   (SQLite)       │                 │    (SQLite)          │
     │                  │                 │                      │
     │  deals           │                 │  LangGraph           │
     │  events          │                 │  checkpointer        │
     │  ticks           │                 │  (opaque tables)     │
     │  agent_observ.   │                 │                      │
     │  interventions   │                 │  Separate DB —        │
     │  issuers         │                 │  agent and domain    │
     │  parties         │                 │  state never conflated│
     └──────────────────┘                 └──────────────────────┘

   ┌────────────────────── Eval harness ──────────────────────────┐
   │   make eval → eval.runner: for each scenarios/*.yaml           │
   │     1. fresh in-memory domain DB + SimulatedClock              │
   │     2. apply setup (stage, aging, comms, deadlines)            │
   │     3. advance simulated clock as scenario requires            │
   │     4. invoke Monitor+Investigator                             │
   │     5. assert declared assertions                              │
   │   → eval_results/scorecard_<ts>.md                             │
   │   [STRETCH] Tier 2 judge: Sonnet-graded rubric over drafts     │
   └────────────────────────────────────────────────────────────────┘
```

### Data flow per tick (MVP)

1. APScheduler fires `tick()` with the current clock time.
2. Dispatcher opens a transaction, writes `ticks(tick_id, started_at)` atomically. If `tick_id` exists in completed state → early-exit (idempotent restart).
3. Monitor enumerates live deals (not `settled` / `broken`), scores each with Haiku, picks top-≤5 over threshold.
4. Investigator runs per queued deal (sequential in MVP). LangGraph checkpointer snapshots state at each node; mid-tick crash resumes from the last checkpoint.
5. Every LLM call passes through the LLM client: timeout → retry → Pydantic validate → one corrective reprompt → log.
6. Observation written with `(tick_id, deal_id)` compound unique key. Intervention (if drafted) persisted with `intervention_id` referenced by the observation.
7. Dispatcher marks tick complete. Daily Brief composer refreshes the top-5–7 list.
8. In real-time mode, HTMX polls `/brief` every 30 s (FR-032a); in simulated mode, the "Advance N days" POST handler runs N sequential ticks then returns the refreshed partial.

**Analyst approval side-effects (FR-LOOP-01/02)**: When `POST /interventions/{id}/approve` is handled by `web/routes/intervention.py`, the handler executes a single database transaction that: (a) sets `interventions.status = 'approved'` and `interventions.approved_at = clock.now()`, and (b) inserts a row into `events` with `event_type = 'comm_sent_agent_recommended'`, `deal_id` from the intervention, `occurred_at = clock.now()`, and `payload = {"intervention_id": id}`. Both writes are atomic — if the event insert fails, the status update rolls back. The same transaction applies on `POST /interventions/{id}/edit` when the analyst confirms.

### Pipeline Monitor vs Deal Investigator

| | Pipeline Monitor | Deal Investigator |
|---|---|---|
| **Character** | Structured screening pipeline | Agentic sub-graph |
| **Execution path** | Fixed: enumerate → score → cap → fan-out | Variable: determined by intermediate findings |
| **Decision logic** | Deterministic attention-score threshold + top-5 rule | Conditional branching based on LLM reasoning at `assess_sufficiency` |
| **Cost** | Predictable: 1 Haiku call × N live deals | Adaptive: 5–8 Sonnet calls depending on enrichment |
| **Why this design** | Screening must be cheap, auditable, and bounded. Emergent screening logic would make cost unpredictable and decisions harder to explain. | Investigation depth should match deal complexity. A fixed sequence would either over-investigate simple deals or under-investigate ambiguous ones. |
| **LangGraph pattern** | Supervisor with `Send` fan-out | Cycle-capable sub-graph with `enrichment_count` state counter |

### Enrichment tools (Investigator N4 node)

Three read-only functions available to `enrich_context`. All query `domain.db` only. No external calls.

| Tool | Inputs | Output | Tables queried | Trigger situation |
|---|---|---|---|---|
| `fetch_communication_content(deal_id)` | `deal_id: str` | `list[{occurred_at, direction, body}]` — actual message text | `events` WHERE `event_type IN ('comm_outbound','comm_inbound')` | Communication silence flagged: checks whether prior message already explained the silence |
| `fetch_prior_observations(deal_id)` | `deal_id: str` | `list[{tick_id, severity, reasoning_summary}]` — last 5 observations | `agent_observations` ORDER BY `observed_at DESC LIMIT 5` | Compounding watch signals: checks whether pattern is known/resolved or genuinely new |
| `fetch_issuer_history(issuer_id)` | `issuer_id: str` | `list[{deal_id, final_stage, outcome, key_signals}]` — closed deals | `deals JOIN agent_observations` WHERE `issuer_id = ?` AND `stage IN ('settled','broken')` | Issuer non-responsiveness: checks whether current delay matches issuer's historical pattern |

**State shape addition** (`InvestigatorState` — see [data-model.md §2.2](./data-model.md)):
```python
enrichment_count: int          # initialized 0; incremented each N4 invocation
enrichment_chain: Annotated[   # fan-in reducer; each N4 appends one entry
    list[EnrichmentStep], operator.add
]
```
```python
class EnrichmentStep(TypedDict):
    round: int
    tool_called: Literal[
        "fetch_communication_content",
        "fetch_prior_observations",
        "fetch_issuer_history"
    ]
    tool_rationale: str          # LLM's stated reason for choosing this tool
    context_summary: str         # LLM's interpretation of what was returned
```
`emit_observation` serializes `enrichment_chain` into the `reasoning` JSON column so the analyst can see the full enrichment reasoning path (FR-AGENT-06).

### Key architectural invariants

- **Clock discipline**: `src/hiive_monitor/clock.py` is the only module that calls `datetime.datetime.now`. A unit test greps the codebase and fails if any other module references it. This is Principle V lived in a single test.
- **Domain/agent-state separation**: two SQLite files, two connection pools. No cross-database foreign keys. The only link is `tick_id` carried by both (domain owns the canonical `ticks` row).
- **No-send invariant**: the intervention module exposes only `draft()` + `persist_as_pending()`. There is no `send()` method. A test asserts no import of `smtplib`/`requests` targets an external recipient.
- **Idempotency**: every `INSERT` on `agent_observations` and `interventions` uses `INSERT … ON CONFLICT(tick_id, deal_id) DO NOTHING`. LangGraph checkpointer provides per-node resume.
- **Bounded LLM outputs**: enum fields for severity, dimensions, recipient type; length caps via Pydantic `Field(max_length=…)`. Parse failure reprompts once; a second failure persists an error observation (no silent drop).

## Build sequencing

Anchored to BUILD_PLAN §10.1 hours. Clock before scheduler; scheduler before agents; agents before UI; UI before polish. Eval harness ships ≥5 scenarios by end of USER STORY 1.

| Hours | Work area | Gate |
|---|---|---|
| 0–4 | Spec-Kit flow through `/speckit.analyze`, repo scaffold, pyproject, Makefile skeleton, README shell | clock+scheduler ready to begin |
| 4–7 | Clock abstraction + grep test forbidding direct `datetime.now()` in src/ | all subsequent time code uses clock |
| 7–10 | APScheduler + tick dispatcher + idempotent tick-start write | Monitor ready to plug in |
| 10–12 | SQLite domain DDL + DAO + migration | seed generator ready |
| 12–16 | Synthetic data generator (30 deals, 10 issuers, 5 engineered scenarios, ≈75 comms, voice calibration) | first demo has content |
| 16–20 | Pipeline Monitor (Haiku screening + attention score + top-5 cap) | Monitor emits queues |
| 20–26 | Deal Investigator sub-graph skeleton: N1 observe → N2 evaluate_dimensions → N3 assess_sufficiency (loop entry) → N4 enrich_context (conditional, enrichment_count guard) → N5 score_severity → branch → N6 draft_intervention → N7 emit_observation | end-to-end reasoning path alive including enrichment loop |
| 26–31 | 5 LLM-reasoned risk dimension prompts + schemas + calibration (1 h per dimension, BUILD_PLAN §11 risk #6) | severity correct on 3 of 5 engineered scenarios |
| 31–31.5 | Deterministic counterparty-responsiveness dimension | 6th dimension present |
| 31.5–33.5 | Severity rubric prompt + schema + calibration | severity calibrated |
| 33.5–37.5 | Three intervention drafters + voice calibration | drafts sound like Transaction Services |
| 37.5–39.5 | Daily Brief composer (ranking) | brief list renders |
| 39.5–41.5 | FastAPI + Jinja foundation | routes return content |
| 41.5–44.5 | HTMX + Tailwind foundation (design tokens, base.html, layout) | design system in place |
| 44.5–48.5 | Screen 1 (Daily Brief + All Open Items tab + HTMX actions) | US1 demo complete |
| 48.5–51.5 | Screen 2 (Per-Deal Detail + audit trail + debug view) | US2 + US6 complete |
| 51.5–53.5 | Simulation controls (advance N days, mode toggle, auto-refresh on advance) | US5 complete |
| — (incremental) | Tier 1 eval harness + 15 scenarios (build from hour 10; 5 h total across the build) | US7 complete |
| 53.5–55.5 | Observability polish (debug view, correlation IDs in UI) + README + writeup + reflection + demo recording | submission shape |

**Hour-40 checkpoint (BUILD_PLAN §9.3)**: if any MVP item is still in progress, complete MVP before touching any stretch. If MVP is complete, begin stretch queue in cut-off order (Screen 3 first — earliest cut-off at hour 45).

## Stretch Queue (hard-start cut-offs)

Per BUILD_PLAN §9.2. Each item has a named plug-in point in the MVP architecture so adding it later is plumbing, not a rewrite.

| # | Stretch item | Est. h | Do not start after | Plug-in point in MVP |
|---|---|---|---|---|
| 1 | Tier 2 LLM-as-judge (10–15 scenarios) | 3 | **hour 50** | `src/hiive_monitor/eval/judge.py` — reads `intervention_body` + `expected_rubric` from same YAML; extends scorecard with a "judge" section. Scorecard writer already composes sections. |
| 2 | Scale 15 → 23 golden scenarios | 2 | **hour 50** | `eval/scenarios/*.yaml` — runner is scenario-count-agnostic; add 8 more fixtures. |
| 3 | Fourth intervention type (status recommendation) | 1 | **hour 52** | `models/intervention.py::InterventionType` enum adds `status_recommendation`; `llm/prompts/intervention_status_recommendation.py` added; investigator's draft-type selector extended. Severity surfacing unchanged. |
| 4 | Scale 30 → 40 deals + 3 engineered scenarios | 2 | **hour 50** | `seed/generate.py --deal-count 40 --engineered-scenario-count 8`; `seed/engineered_scenarios.yaml` grows from 5 → 8. Generator accepts these flags from MVP. |
| 5 | Screen 3 standalone page with batch ops | 3 | **hour 45** | New route `web/routes/queue.py` + `templates/queue.html`. Reuses `_brief_item.html` partial. Batch-op POST handlers new; single-item POSTs already exist. |

**Stretch never partial-ships** (BUILD_PLAN §9.3). At each cut-off: if not started → skip permanently; if started but not finished → complete it, skip the next.

**Scope valves in reserve** (BUILD_PLAN §9.4 — only if MVP at risk at hour 50): drop 3 prioritization scenarios (15 → 12); drop engineered scenario #4 (Cerebras large+first-time); ship Daily Brief functional-but-plain (skip final Tailwind polish).

## Risks and Mitigations

Top-5 failure modes in a 60-hour build, with timeboxes and fallbacks.

| # | Risk | Detection | Mitigation | Fallback |
|---|---|---|---|---|
| 1 | **Clock abstraction bug** — direct `datetime.now()` sneaks in; every downstream time assertion lies silently | grep test fails on commit | Build clock primitive in hours 4–7, write the grep test immediately | Freeze time discipline to `clock.now()` only; document any residual wall-clock drift in UI |
| 2 | **LangGraph checkpointer × simulated clock** interaction untested; replay uses wall-clock instead of `tick_id` | Integration test: kill process mid-tick in simulated mode, assert resume emits only the remaining observations | Validate by hour 12; if broken, fall back to "restart-from-last-DB-persisted-state" (re-run Investigators for deals without a completed observation in the open tick) | Document as a known limitation in the writeup |
| 3 | **Intervention voice sounds generic** | First 5 drafts manually reviewed before prompt is shipped | Budget 2 h during hour 33.5–37.5 on voice calibration; hand-write exemplars in `seed/comm_voice_examples.md` first | Per-recipient-type templates with LLM filling concrete facts only (tighter bounds) |
| 4 | **Synthetic data feels fake** | Review first 10 generated comms before generating the rest | Hand-write ≈10 exemplars; LLM completes remainder against them; manual pass | Reduce to 40 hand-written comms; ship fewer but higher-quality |
| 5 | **Risk-dimension prompt calibration eats > 5 h** | Per-dimension 1-h timebox | If not calibrated after 1 h, fall back to a simpler rule (e.g., `age_days > 1.5 × baseline → flag`); document as known limitation | — |
| 6 | **UI polish overruns** | Tailwind foundation must finish by hour 44.5 | If still fighting CSS at hour 46, stop polishing | Ship baseline styling |
| 7 | **Claude API cost runs over** | Haiku for screening from day 1; budget $50–100 expected | Cassette-record LLM calls during repeated eval runs (`--use-cassette` flag on runner) | — |

## Cross-Check: Spec ↔ Plan ↔ Constitution ↔ Stack

### Every functional requirement → plan component

| FR | Plan component |
|---|---|
| FR-001 Monitor supervisor | `agents/monitor.py` + APScheduler dispatcher |
| FR-001a Attention-score threshold + top-5 cap | `agents/monitor.py` screening node + queue-selection |
| FR-002 Investigator agentic sub-graph | `agents/investigator.py` LangGraph sub-graph with cycle-capable N3→N4→N3 loop |
| FR-003 Six risk dimensions | `investigator.N2_evaluate_dimensions` dispatches to 5 prompts + 1 deterministic in parallel |
| FR-AGENT-01 Sufficiency check | `agents/investigator.py::N3_assess_sufficiency` — Sonnet call that reads dimension results + enrichment_count and routes conditionally |
| FR-AGENT-02 Enrichment conditional | `agents/investigator.py::N4_enrich_context` + conditional edge from N3; not called on every deal |
| FR-AGENT-03 Three enrichment tools | `db/dao.py::fetch_communication_content`, `fetch_prior_observations`, `fetch_issuer_history` — read-only domain DB queries |
| FR-AGENT-04 Max 2 enrichment rounds | `graph_state.py::InvestigatorState.enrichment_count`; N3 routes to N5 if `count >= 2` regardless of reasoning output |
| FR-AGENT-05 Trigger situations | System prompt for N3 `assess_sufficiency` node encodes the three trigger situations; agent reasons against them |
| FR-AGENT-06 Enrichment in audit trail | `N7_emit_observation` serializes `enrichment_chain: list[EnrichmentStep]` into `agent_observations.reasoning` JSON |
| FR-004 Dimension 5 deterministic | `llm/deterministic/counterparty_responsiveness.py` |
| FR-005 Severity from rubric | `llm/prompts/severity_rubric.py` + `models/risk.py::Severity` |
| FR-006 Severity surfacing rules | `agents/brief_composer.py` (act/escalate in Today's Priorities); `web/routes/deal.py` (watch in Per-Deal) |
| FR-007 Three intervention types | `models/intervention.py` enum + 3 prompt modules |
| FR-008 Intervention carries reasoning + facts | `models/intervention.py::Intervention` requires `reasoning_ref`, `cited_facts` |
| FR-009 Tone per recipient | Per-recipient prompt variants in `intervention_outbound_nudge.py` |
| FR-010 Escalation ≤50 words | Pydantic `Field(max_length=…)` + post-generation length assertion |
| FR-011 No external send | Intervention module has no send method; asserted by test |
| FR-012 No source-of-record mutation | No external-system integrations exist |
| FR-013 Status enum + explicit action | `models/intervention.py::Status` + `web/routes/intervention.py` POSTs |
| FR-014 Original + final retained | `interventions.body` (draft) + `interventions.final_text` columns |
| FR-LOOP-01 Approval emits comm event | `web/routes/intervention.py::approve_handler` — single transaction: status update + `events` insert with `event_type='comm_sent_agent_recommended'` |
| FR-LOOP-02 Attention suppression | `agents/monitor.py` screening node — after Haiku score, query `events` for `comm_sent_agent_recommended` within last `SUPPRESSION_TICKS` ticks; multiply raw score × 0.2 if found |
| FR-LOOP-03 Distinct event type in timeline | `models/event.py::EventType` adds `comm_sent_agent_recommended`; `templates/_timeline_event.html` renders a labelled agent-recommendation indicator for this type |
| FR-015 Stage enum + dwell baselines | `models/deal.py::DealStage` + `seed/issuers.yaml` (per-stage baselines) |
| FR-016 Persistent per-deal state | `deals` table schema |
| FR-017 Seven domain tables (`deals`, `events`, `agent_observations`, `interventions`, `issuers`, `parties`, `ticks`) plus separate LangGraph checkpointer database | `db/schema.sql` |
| FR-018 Separate checkpointer DB | `domain.db` + `agent_checkpoints.db` |
| FR-019 Append-only events | `events` table, insert-only DAO |
| FR-020 Clock abstraction, no direct now() | `clock.py` + grep test |
| FR-021 Mode by env var | `config.py::Settings.app_mode` |
| FR-022 Scheduler + default 60 s | `scheduler.py` APScheduler wiring |
| FR-023 Simulated-clock UI + N>0 | `web/routes/simulate.py` + brief template control |
| FR-023a N advances → N daily ticks | `scheduler.advance_by_days(n)` loops N times, `clock.advance(1 day)` between |
| FR-024 Idempotency on (tick_id, deal_id) | Compound UNIQUE index + ON CONFLICT DO NOTHING |
| FR-024a Suppress re-draft when pending exists | `investigator.draft_intervention` gated on "no open pending intervention" |
| FR-025 Pydantic validation + corrective reprompt | `llm/client.py` |
| FR-026 LLM call logging | `llm/client.py` structlog emission |
| FR-027 structlog human/JSON | `logging.py` by env |
| FR-028 Debug view | `web/routes/deal.py` + `templates/_debug_panel.html` behind `?debug=1` |
| FR-029 Daily Brief shape | `web/routes/brief.py` + `templates/brief.html` + `_brief_item.html` |
| FR-029a Handled item stays until next tick | HTMX swap replaces inner partial; list recompute only on tick |
| FR-030 All Open Items tab | `web/routes/brief.py::all_open_items` + filters |
| FR-031 Per-Deal Detail | `web/routes/deal.py` + `templates/deal.html` |
| FR-032 System status | `web/routes/status.py` |
| FR-032a Polling strategy | `templates/brief.html` `hx-trigger="every 30s"` in real-time; simulated-mode re-fetch on advance response |
| FR-033 Domain-accurate, fact-citing drafts | Prompt templates reference concrete fields; post-generation assertion |
| FR-034 30 deals across 10 issuers | `seed/generate.py` + `seed/issuers.yaml` |
| FR-035 Five engineered-issue scenarios | `seed/engineered_scenarios.yaml` |
| FR-036 Issuer metadata variety | `seed/issuers.yaml` |
| FR-037 Transaction-Services voice | `seed/comm_voice_examples.md` + voice-calibrated generator prompt |
| FR-038 `make eval` runs 15 scenarios | `Makefile::eval` → `eval/runner.py` |
| FR-039 YAML scenario schema | `contracts/eval-scenario-schema.md` |
| FR-040 Scorecard output | `eval/scorecard.py` |
| FR-041 Makefile targets | `Makefile` |

### Every constitution principle → plan decision

See Constitution Check table above. Each principle maps to at least one concrete plan decision.

### Every tech-stack item → integration point

| Tech choice | Integration | Status |
|---|---|---|
| Python 3.11+ | `pyproject.toml::requires-python` | ✅ |
| uv | `Makefile::setup` uses `uv sync`; `uv.lock` committed | ✅ |
| FastAPI | `src/hiive_monitor/app.py` | ✅ |
| LangGraph + SQLite checkpointer | `agents/monitor.py`, `agents/investigator.py`; `agent_checkpoints.db` | ✅ |
| Pydantic v2 | `models/*`, LLM I/O schemas, `config.py` Settings | ✅ |
| Anthropic Claude (Sonnet + Haiku) | `llm/client.py` with per-call-site model routing | ✅ |
| SQLite domain store | `db/schema.sql`, `domain.db` (seeded, committed) | ✅ |
| APScheduler in-process | `scheduler.py` wired into FastAPI lifespan | ✅ |
| Jinja2 | `web/templates/*` | ✅ |
| HTMX (pinned) | `web/static/htmx.min.js` vendored; `hx-*` attrs in templates | ✅ |
| Alpine.js (sparingly) | `web/static/alpine.min.js`; dropdown + copy-to-clipboard + toasts | ✅ |
| Tailwind CSS (CLI build) | `Makefile::setup` runs Tailwind CLI → `web/static/tailwind.css` from `input.css` | ✅ |
| structlog | `logging.py` + correlation-ID contextvars | ✅ |
| PyYAML | `eval/runner.py`, `seed/*.yaml` | ✅ |
| pytest | `tests/*` | ✅ |
| **Not** Docker / Postgres / Redis / MQ / auth / React / real integrations / fine-tuning / RAG | Absent by design — README "What this is not" | ✅ |

### Every stretch item → plug-in point

| Stretch | Plug-in | Cut-off |
|---|---|---|
| Screen 3 standalone | `web/routes/queue.py` + `templates/queue.html`; reuses `_brief_item.html` | hour 45 |
| 4th intervention type | `models/intervention.py::InterventionType` enum extension + new prompt module | hour 52 |
| 40 deals + 3 scenarios | `seed/generate.py --deal-count --engineered-scenario-count` | hour 50 |
| 23 golden scenarios | Add 8 YAML files to `eval/scenarios/` | hour 50 |
| Tier 2 LLM-as-judge | `eval/judge.py` extends runner + scorecard | hour 50 |

### Gaps flagged

None from the spec ↔ plan cross-check. Two deliberate deferrals to `/speckit.tasks`:
- APScheduler job store choice — decision is "memory store; rely on LangGraph checkpointer for cross-restart durability" but will be finalized in the task phase with a code citation.
- Exact Tailwind palette (gray scale, accent, alert hex codes) — decided during hour 41.5–44.5.

## Post-Design Constitution Re-check

After Phase 1 design (data model, contracts, quickstart):

- Principles I–X: still pass. No new violations. The data model preserves domain/agent-state separation (V); LLM schemas bound outputs (V); eval-scenario contract formalizes the golden-set shape (VI); quickstart proves the three-command path (X).

## Complexity Tracking

No Constitution violations require justification.

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| — | — | — |
