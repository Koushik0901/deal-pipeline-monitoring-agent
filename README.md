# Hiive Deal Pipeline Monitoring Agent

An agentic monitoring system for Hiive's Transaction Services team. Two LangGraph agents — a
**Pipeline Monitor** that screens every live deal on each tick and a **Deal Investigator** that
observes, evaluates six risk dimensions, and drafts interventions — help Transaction Services
analysts answer the question: *"Tell me what I should do today, in priority order, with the draft
already written, and show me why you're recommending it."*

## Quickstart

```bash
# 1. Install dependencies and initialise the database
make setup

# 2. Copy environment template and set your API key
cp .env.example .env
# edit .env → set ANTHROPIC_API_KEY

# 3. Seed + start + auto-advance simulated clock (demo mode)
make demo
```

`make demo` opens the browser at the Daily Brief with the five engineered-issue deals visible.
`make eval` runs the 39 Tier 1 deterministic scenarios and prints the scorecard.
`make eval-deep` runs the Tier 2 LLM-as-judge pass (requires Langfuse + `EVAL_JUDGE_MODEL` in `.env`).

## Architecture

```
Analyst Browser (HTMX + Alpine + Tailwind)
   ↕ HTTP partials
FastAPI app (single process)
   ├── APScheduler → run_tick()
   │     ├── Pipeline Monitor (LangGraph StateGraph)
   │     │     └── Deal Investigator × up to 5 (LangGraph sub-graph, agentic)
   │     └── Brief Composer (Sonnet)
   ├── Web routes: /brief, /pipeline, /deals/{id}, /interventions/…, /sim/advance, /status
   └── SQLite: domain.db (7 tables) + agent_checkpoints.db (LangGraph)
```

See [`docs/architecture.md`](docs/architecture.md) for the full diagram.

### UX

- **Daily Brief** (`/brief`) — top 5–7 ranked items with drafted interventions and in-place approve/edit/dismiss.
- **Pipeline view** (`/pipeline`) — book-of-deals table with deterministic health tiers; all filters, sort, and free-text search run client-side against `data-*` attributes, so retriage is keystroke-latency with no server round-trip.
- **Deal detail** (`/deals/{id}`) — navigating from a pipeline or Brief row uses the cross-document View Transitions API: the severity badge and deal ID morph into the detail header (Chrome/Safari). Firefox falls back to a plain navigation with no broken state.

## Assumptions

Operational assumptions (dwell-time baselines, issuer metadata, ROFR defaults) are documented
in [`docs/assumptions.md`](docs/assumptions.md).

## Eval Scorecard

<!-- scorecard-start -->
Latest confirmed result: **35 / 39 (89%)** — Tier 1 deterministic, 2026-04-18.
Run `make eval` to regenerate.
<!-- scorecard-end -->
