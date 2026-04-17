# System Architecture

## Component Map

```
┌─────────────────────────────────────────────────────────────────┐
│  Analyst Browser  (HTMX 2.0 + Alpine.js + Tailwind CSS)        │
│  /brief  /deals/{id}  /queue  /sim                              │
└──────────────────────────┬──────────────────────────────────────┘
                           │ HTTP partial updates
┌──────────────────────────▼──────────────────────────────────────┐
│  FastAPI  (single process, Jinja2 server-rendered HTML)         │
│  web/routes/  → templates/                                      │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  APScheduler  (in-process, two modes)                   │   │
│  │  ┌──────────┐  real_time: IntervalTrigger(60s)          │   │
│  │  │ run_tick │  simulated: manual advance via /sim/advance│   │
│  │  └────┬─────┘                                           │   │
│  └───────│─────────────────────────────────────────────────┘   │
│          │                                                       │
│  ┌───────▼─────────────────────────────────────────────────┐   │
│  │  Pipeline Monitor  (LangGraph StateGraph)                │   │
│  │  load_live_deals → screen_with_haiku → apply_suppression │   │
│  │  → select_investigation_queue → fan_out_investigators    │   │
│  │  → close_tick                                            │   │
│  │                                                          │   │
│  │  ┌──────────────────────────────────────────────────┐   │   │
│  │  │  Deal Investigator × up to 5  (LangGraph sub-graph│   │   │
│  │  │  observe → evaluate_risks → assess_sufficiency   │   │   │
│  │  │  ↕ agentic loop (max 2 rounds)                   │   │   │
│  │  │  enrich_context → assess_sufficiency             │   │   │
│  │  │  → score_severity → [draft_intervention]         │   │   │
│  │  │  → emit_observation                              │   │   │
│  │  └──────────────────────────────────────────────────┘   │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                 │
│  ┌──────────────────┐   ┌──────────────────────────────────┐   │
│  │  domain.db       │   │  agent_checkpoints.db            │   │
│  │  (SQLite)        │   │  (LangGraph SqliteSaver)         │   │
│  │  deals           │   │  Per (tick_id, deal_id) thread   │   │
│  │  events          │   └──────────────────────────────────┘   │
│  │  agent_observations│                                        │
│  │  interventions   │                                          │
│  │  issuers         │                                          │
│  │  parties         │                                          │
│  │  ticks           │                                          │
│  └──────────────────┘                                          │
│                                                                 │
│  Claude Haiku ← screening (cheap, per-deal)                    │
│  Claude Sonnet ← risk evaluation, severity, intervention draft │
└─────────────────────────────────────────────────────────────────┘
```

## Key Design Decisions

**Clock abstraction**: Every timestamp read goes through `hiive_monitor.clock.now()`. Two modes selectable by `CLOCK_MODE` env var: `real_time` (wall clock) and `simulated` (injected, advanceable). The eval harness and `make demo` both use simulated mode. No `datetime.now()` calls anywhere in application code.

**Agentic loop**: The Deal Investigator's N3→N4→N3 loop (assess_sufficiency → enrich_context → assess_sufficiency) is bounded at 2 rounds to prevent runaway LLM costs. The LLM decides which enrichment tool to call (communication history, prior observations, or issuer history). If the max rounds are reached, the graph forces "sufficient" and proceeds to severity scoring.

**Idempotency**: Two layers. (1) The `ticks` table has UNIQUE on `tick_id` — `start_tick` uses `INSERT OR IGNORE`. If a tick crashes and restarts with the same tick_id, it's a no-op. (2) The `agent_observations` table has UNIQUE on `(tick_id, deal_id)` — double-invocation of the investigator for the same deal in the same tick is silently discarded.

**Human-in-the-loop**: The agent never sends external communications. All interventions are drafts in `status='pending'`. Approve and edit both use atomic DB transactions that simultaneously update the intervention status and insert a `comm_sent_agent_recommended` event — this is the only path to marking a communication as sent.

**Suppression**: Deals that received an agent-recommended communication within the last 3 ticks have their attention score multiplied by 0.2. This prevents re-nudging a party that was already contacted this tick window.

## Two-Tier LLM Strategy

| Model | Used for | Cost profile |
|-------|----------|-------------|
| Claude Haiku | Per-deal attention scoring (screen_with_haiku) | Low — one call per live deal per tick |
| Claude Sonnet | Risk evaluation (5 calls), severity scoring, intervention drafting, enrichment sufficiency | Medium — only for top-5 deals per tick |

## Evaluation Architecture

```
eval/fixtures/*.yaml  →  runner.py
  ├── _seed_from_scenario()  builds isolated in-memory DB
  ├── SimulatedClock set to scenario.setup.now
  ├── run_tick() invokes full Monitor + Investigator
  └── evaluate_assertions() checks observations + interventions
        → scorecard_{timestamp}.md
```

17 scenarios across 3 categories:
- **detection** (10): verifies the agent flags the right risks
- **prioritization** (4): verifies severity ordering
- **intervention_quality** (3): verifies draft content against domain-accuracy requirements
