# System Architecture

## Component Map

```
┌─────────────────────────────────────────────────────────────────────────┐
│  Analyst Browser  (HTMX + Alpine.js + Tailwind CSS, pinned)             │
│  /brief  /pipeline  /queue  /deals/{id}  /sim                           │
└────────────────────────────────┬────────────────────────────────────────┘
                                 │  HTTP partial updates
┌────────────────────────────────▼────────────────────────────────────────┐
│  FastAPI  (single process, Jinja2 server-rendered HTML)                 │
│  web/routes/ → templates/                                               │
│                                                                         │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │  APScheduler  (in-process, two modes)                            │  │
│  │  ┌──────────┐   real_time : IntervalTrigger(TICK_INTERVAL_SECONDS│  │
│  │  │ run_tick │                                     default 60s)  │  │
│  │  │ (job id: │   simulated: manual advance via POST /sim/advance │  │
│  │  │ monitor_ │                                                    │  │
│  │  │ tick)    │                                                    │  │
│  │  └────┬─────┘                                                    │  │
│  └───────│──────────────────────────────────────────────────────────┘  │
│          │                                                              │
│  ┌───────▼──────────────────────────────────────────────────────────┐  │
│  │  Pipeline Monitor  (LangGraph StateGraph)                         │  │
│  │   load_live_deals                                                 │  │
│  │   → screen_with_SLM           (ThreadPoolExecutor × 8, 45 s timeout)│
│  │   → apply_suppression         (SUPPRESSION_TICKS × SUPPRESSION_   │  │
│  │                                MULTIPLIER, defaults 3 × 0.2)      │  │
│  │   → select_investigation_queue(threshold 0.45, cap 12)            │  │
│  │   → fan_out_investigators     (ThreadPoolExecutor × 8, 120 s)     │  │
│  │   → close_tick                → compose_daily_brief() best-effort │  │
│  │   → detect_portfolio_patterns (TS-09, 3-tick rolling baseline)    │  │
│  │                                                                   │  │
│  │   ┌────────────────────────────────────────────────────────────┐ │  │
│  │   │  Deal Investigator  (LangGraph sub-graph, up to 8 parallel)│ │  │
│  │   │   observe                                                   │ │  │
│  │   │   → evaluate_risks   (1 combined LLM call → 5 dims          │ │  │
│  │   │                       + 1 deterministic counterparty call)  │ │  │
│  │   │   → assess_sufficiency ─┐   agentic loop, ≤ 2 rounds        │ │  │
│  │   │       ↕                  │   LLM picks which enrichment     │ │  │
│  │   │      enrich_context ─────┘   tool to call next              │ │  │
│  │   │   → score_severity        (escalate/act/watch/informational)│ │  │
│  │   │   → draft_intervention    (skipped only for informational)  │ │  │
│  │   │   → emit_observation                                        │ │  │
│  │   └────────────────────────────────────────────────────────────┘ │  │
│  └───────────────────────────────────────────────────────────────────┘  │
│                                                                         │
│  ┌──────────────────┐   ┌────────────────────────────────────────────┐ │
│  │  domain.db       │   │  agent_checkpoints.db                      │ │
│  │  (SQLite, WAL)   │   │  (LangGraph SqliteSaver)                   │ │
│  │  issuers         │   │  thread_id = f"{tick_id}:{deal_id}"        │ │
│  │  parties         │   └────────────────────────────────────────────┘ │
│  │  deals           │                                                  │
│  │  events          │                                                  │
│  │  ticks           │   OpenRouter gateway                             │
│  │  agent_observations│   SLM_MODEL ← screen_with_SLM                  │
│  │  interventions   │   LLM_MODEL ← investigator + brief composer     │
│  └──────────────────┘   EVAL_JUDGE_MODEL ← deepeval (Tier 2 only)     │
└─────────────────────────────────────────────────────────────────────────┘
```

## Key Design Decisions

**Clock abstraction** — every timestamp read goes through `hiive_monitor.clock.now()`. Two modes selected by `CLOCK_MODE` env var (default `simulated`): `RealTimeClock` returns UTC wall-clock; `SimulatedClock` returns an injected instant that `/sim/advance` increments by N days. The eval harness and `make demo` both use simulated mode. A grep-test (`tests/unit/test_no_datetime_now.py`) enforces that no application code calls `datetime.now()` directly.

**Agentic enrichment loop** — the Deal Investigator's `assess_sufficiency` ↔ `enrich_context` loop is bounded at `_MAX_ENRICHMENT_ROUNDS = 2` to prevent runaway LLM cost. On each iteration the LLM inspects the current snapshot and picks one enrichment tool: `fetch_communication_content`, `fetch_prior_observations`, `fetch_issuer_history`, or `fetch_intervention_outcomes`. When the cap is hit the graph forces "sufficient" and proceeds to `score_severity`.

**One combined risk-evaluation call** — five of the six risk dimensions (`stage_aging`, `communication_silence`, `missing_prerequisites`, `deadline_proximity`, `unusual_characteristics`) are evaluated in a single structured-output call against the `AllRiskSignals` schema. The sixth, `counterparty_nonresponsiveness`, is **deterministic** — computed in `src/hiive_monitor/llm/deterministic/counterparty_responsiveness.py` from last-inbound timestamps and the issuer's `typical_response_days`. Results are merged at `investigator.py:~133`. Reasoning: facts the code can compute shouldn't be delegated to an LLM, and collapsing 5 LLM dims into one call cuts per-tick cost ~5×.

**Idempotency** — two layers. (1) The `ticks` table has a PRIMARY KEY on `tick_id`; `start_tick` uses `INSERT OR IGNORE`, so a crashed-and-restarted tick with the same id is a no-op. (2) The `agent_observations` table has UNIQUE on `(tick_id, deal_id)`; double-invoking the investigator for the same deal in the same tick is silently discarded. All DAO writes use `INSERT OR IGNORE` (per FR-024); approve/snooze use explicit `with conn:` transactions.

**Human-in-the-loop** — the agent never sends external communications. All interventions are drafts in `status='pending'`. Approve, edit, and batch-approve are atomic DB transactions that simultaneously flip the intervention status and insert a `comm_sent_agent_recommended` event — that event type is the only code path for marking a communication as sent.

**Suppression** (FR-LOOP-02) — deals that received an agent-recommended communication within the last `SUPPRESSION_TICKS` (default 3) have their attention score multiplied by `SUPPRESSION_MULTIPLIER` (default 0.2). Implemented as the `apply_suppression` graph node, not inlined. This prevents re-nudging a counterparty that was already contacted inside the current suppression window.

**Portfolio-pattern detection** (TS-09) — after `close_tick`, the `detect_portfolio_patterns` node computes a 3-tick rolling baseline per cluster (issuer × stage) and flags anomalies when live counts exceed `2×` the rolling mean. Flags are persisted to the `ticks.signals` column and surfaced in the status bar.

**Pipeline rendering** (`/pipeline`) — the book-of-deals view computes deterministic health tiers from signal shape alone (no LLM call per row) and renders every live deal to the DOM. Filter, sort, and free-text search run client-side via `window.PF` against `data-*` attributes — 0 ms latency, no server round-trips. URL query params (`tier`, `stage`, `issuer`, `responsible`, `sort`) set the initial `hidden` class in Jinja2 so deep links and no-JS clients still work; `counts_by_tier` in the header always reflects the whole pipeline, not the filtered slice.

**View Transitions (cross-document morph)** — deal row → detail navigation uses the View Transitions API. Severity badges share `view-transition-name: sev-{deal_id}`; deal IDs share `dealid-{deal_id}`; both names appear in `_macros.html`, `pipeline.html`, and `deal_detail.html`. Chrome/Safari morph the matched elements between documents; Firefox falls back to plain navigation. The `@view-transition` CSS block lives at the top level of `input.css`, not inside `@layer base` (Tailwind would silently strip it).

**Snooze** (TS-10) — analysts can snooze a deal via `POST /deals/{deal_id}/snooze`; `snoozed_until` and `snooze_reason` columns on `deals` suppress the deal from investigation queues until expiry.

## Two-Tier LLM Strategy (OpenRouter)

| Env var | Default (in `.env.example`) | Used by | Cost profile |
|---|---|---|---|
| `SLM_MODEL` | `google/gemma-4-31b-it` | `screen_with_SLM` (attention score per deal per tick) | Low — one call per live deal per tick |
| `LLM_MODEL` | `anthropic/claude-sonnet-4.6:exacto` | `evaluate_risks`, `assess_sufficiency`, `enrich_context`, `score_severity`, `draft_intervention`, `compose_daily_brief` | Medium — only for the ≤ 12 deals that clear the attention threshold |
| `EVAL_JUDGE_MODEL` | `google/gemini-3.1-pro-preview:exacto` | Tier 2 `deepeval_runner` only | Run-time only during `make eval-deep` |

### Why these models

**`SLM_MODEL` — Gemma 4 31B Instruct.** The attention-screen step runs once per live deal per tick (≈ 50 calls/tick at steady state), so unit cost dominates. The call is a narrow triage decision — *"does this deal warrant deep investigation?"* — with a short prompt, a small structured output, and a ground-truth signal the model only needs to approximate. A mid-size open-weight instruct model is plenty: it's cheap, fast, and the Tier 1 eval catches drift if a cheaper model starts under-triaging. Gemma 4 31B hit the sweet spot of low cost + high enough recall on the attention ground truth during pre-flight sweeps.

**`LLM_MODEL` — Claude Sonnet 4.6 (`:exacto` variant).** The investigator does the actual reasoning: merging six risk signals, running the enrichment loop, picking a severity, and drafting analyst-ready interventions whose tone, precision, and domain accuracy matter. This is where model quality shows up as eval score and as analyst trust. Sonnet 4.6 consistently beat the earlier Qwen default on intervention-quality and factual-grounding metrics in Tier 2 runs, with enough headroom in reasoning quality to support the combined 5-dimension call without confusion. The `:exacto` variant enforces strict structured output — important because every investigator call uses a Pydantic schema and a format miss would crash the node. Cost is bounded by the attention threshold and the ≤ 12 investigations/tick cap; typical production load stays well inside budget.

**`EVAL_JUDGE_MODEL` — Gemini 3.1 Pro Preview (`:exacto` variant).** The Tier 2 deepeval judge needs to be (a) strong at structured scoring against criteria, (b) different enough from the agent model to avoid self-grading bias, and (c) reliable with structured output across many metrics. Gemini 3.1 Pro is a different lineage than Sonnet, has top-tier reasoning for eval-style rubrics, and the `:exacto` flag keeps metric outputs parseable. Running it only during `make eval-deep` means cost is bounded to intentional eval runs, not continuous load.

All calls go through `ChatOpenRouter` (`src/hiive_monitor/llm/client.py`). Models are env-swappable without code changes; structured-output calls use `call_structured()` with Pydantic schemas. `LLM_MAX_TOKENS` is optional (unset by default).

## Storage

### `domain.db` — 7 tables (`src/hiive_monitor/db/schema.sql`)

| Table | Purpose | Notable columns |
|---|---|---|
| `issuers` | Issuer metadata | `typical_response_days` baseline |
| `parties` | Buyers/sellers/counsel | `prior_breakage_count` |
| `deals` | Core deal state | stage, blockers, `documents_received` (TS-06), `snoozed_until`/`snooze_reason` (TS-10) |
| `events` | Append-only event log | `stage_transition`, `doc_received`, `doc_requested`, `comm_outbound`, `comm_inbound`, `comm_sent_agent_recommended` |
| `ticks` | One row per monitor tick | PRIMARY KEY `tick_id`, mode, `tick_completed_at`, errors, `signals` (TS-09) |
| `agent_observations` | One per investigation | UNIQUE `(tick_id, deal_id)`; links to drafted intervention |
| `interventions` | Drafted/approved/dismissed comms | types: `outbound_nudge`, `internal_escalation`, `brief_entry`, `status_recommendation` |

### Stretch migrations (`src/hiive_monitor/db/migrations.py`)

Idempotent `ALTER TABLE` blocks wrapped in `try/except sqlite3.OperationalError`, run once at startup (skipped in test DBs):

- **TS-06:** `deals.documents_received TEXT NOT NULL DEFAULT '[]'`
- **TS-09:** `ticks.signals TEXT`
- **TS-10:** `deals.snoozed_until TEXT`, `deals.snooze_reason TEXT`

### `agent_checkpoints.db` — LangGraph persistence

`SqliteSaver` connected with `check_same_thread=False`; thread id per investigation is `f"{tick_id}:{deal_id}"`. Path controlled by `CHECKPOINT_DB_PATH` (default `agent_checkpoints.db`).

## Web Route Inventory

Grouped by file under `src/hiive_monitor/web/routes/`:

### `main.py` — brief, interventions, sim, status APIs
- `GET  /` · `GET  /brief`
- `POST /interventions/{id}/approve` · `POST /interventions/{id}/dismiss` · `POST /interventions/{id}/confirm-edit` · `POST /interventions/batch-approve`
- `POST /deals/{deal_id}/snooze`
- `GET  /deals/{deal_id}` (HTML)
- `GET  /sim` · `POST /sim/advance`
- `GET  /api/tick/{tick_id}/status` · `GET /api/brief-stats` · `GET /api/status-bar` · `GET /api/clock` · `POST /api/tick`

### `pipeline.py`
- `GET  /pipeline` · `GET /queue`

### `simulation.py`
- `GET  /autoplay`

### `deals.py`
- `GET  /deals/{deal_id}` (JSON API)

### `debug.py`
- `GET  /debug/tick/{tick_id}` · `GET /debug/deal/{deal_id}` (HTML debug views)

## Evaluation Architecture

```
eval/fixtures/*.yaml  (39 scenarios)
       │
       ▼
src/hiive_monitor/eval/
├── runner.py              Tier 1 entrypoint
│     ├── load_scenarios(Path("eval/fixtures"))
│     ├── _seed_from_scenario()        isolated in-memory DB per scenario
│     ├── SimulatedClock ← scenario.setup.now
│     ├── run_tick()                   full Monitor + Investigator
│     └── evaluate_assertions()        observations + interventions
│           → eval_results/results_{ts}.json + scorecard
│
├── deepeval_runner.py     Tier 2 entrypoint (consumes Tier 1 JSON)
├── deepeval_metrics.py    5 metrics: Answer Correctness, Task Completion,
│                          Tool Correctness, Factual Grounding,
│                          Dimension Precision/Recall
├── deepeval_adapter.py    OpenRouterJudge wrapper for deepeval
├── deepeval_cases.py      build_test_case / build_expected_tools
├── langfuse_dataset.py    upserts fixtures as a Langfuse dataset
│                          (every fixture keyed by stable id eval/<scenario_id>)
└── langfuse_tracer.py     EvalTracer; no-ops when LANGFUSE_* env vars unset
```

### Fixture categories (39 total)

| Category | Count | Purpose |
|---|---:|---|
| `detection` | 11 | Agent flags the right risks for specific signal profiles |
| `edge_case` | 11 | Suppression, enrichment cap, unusual deal structures |
| `intervention_quality` | 9 | Draft content against domain-accuracy requirements |
| `prioritization` | 4 | Severity ordering |
| `adversarial_calibration` | 4 | Conflicting signals, legal holds, apparent-silence counterexamples |

Tier 1 is fully deterministic (no LLM judge). Tier 2 runs deepeval metrics on top of saved Tier 1 traces; Langfuse tracing is opt-in via `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` and the tracer no-ops gracefully when unset.

## Scheduler

`src/hiive_monitor/scheduler.py`:

- `start_scheduler(tick_fn)` registers job id `monitor_tick`.
- **`real_time`** mode — `IntervalTrigger(seconds=TICK_INTERVAL_SECONDS)` (default 60 s).
- **`simulated`** mode — no auto-trigger; ticks are driven by `POST /sim/advance` or `POST /api/tick`.
