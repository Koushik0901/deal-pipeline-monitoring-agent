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
`make eval` runs the 15 golden scenarios and prints the scorecard.

## Architecture

```
Analyst Browser (HTMX + Alpine + Tailwind)
   ↕ HTTP partials
FastAPI app (single process)
   ├── APScheduler → run_tick()
   │     ├── Pipeline Monitor (LangGraph StateGraph)
   │     │     └── Deal Investigator × up to 5 (LangGraph sub-graph, agentic)
   │     └── Brief Composer (Sonnet)
   ├── Web routes: /brief, /deals/{id}, /interventions/…, /sim/advance, /status
   └── SQLite: domain.db (7 tables) + agent_checkpoints.db (LangGraph)
```

See [`docs/architecture.md`](docs/architecture.md) for the full Mermaid diagram.

## Assumptions

Operational assumptions (dwell-time baselines, issuer metadata, ROFR defaults) are documented
in [`docs/assumptions.md`](docs/assumptions.md).

## Eval Scorecard

<!-- scorecard-start -->
Run `make eval` to generate the current scorecard.
<!-- scorecard-end -->
