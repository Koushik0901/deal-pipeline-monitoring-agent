<div align="center">

# Hiive Deal Pipeline Monitoring Agent

*An agentic early-warning system for private-market deal operations.*

![Python 3.13](https://img.shields.io/badge/python-3.13-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)
![LangGraph](https://img.shields.io/badge/LangGraph-1C3C3C)
![SQLite](https://img.shields.io/badge/SQLite-003B57?logo=sqlite&logoColor=white)
![Tailwind](https://img.shields.io/badge/Tailwind-06B6D4?logo=tailwindcss&logoColor=white)
![HTMX](https://img.shields.io/badge/HTMX-3366CC)
![Claude Code](https://img.shields.io/badge/Claude%20Code-D97757?logo=anthropic&logoColor=white)
![Spec Kit](https://img.shields.io/badge/Spec%20Kit-111827)
![Impeccable](https://img.shields.io/badge/Impeccable-1B4332)
![Eval 89%](https://img.shields.io/badge/Tier%201%20Eval-35%2F39%20(89%25)-brightgreen)

![Daily Brief screen](assets/Daily%20Brief%20Screen.png)

</div>

---

## 💡 Why

Hiive's Transaction Services analysts manage dozens of private-market deals simultaneously. Each deal moves through a sequence of stages (docs, ROFR, signing, funding), each with its own dwell-time expectations, counterparties, and breakage modes. The operational question every morning is the same:

> *"Which deals actually need me today, and what should I do about them?"*

Answering that by hand is slow and error-prone. A deal silent for 9 days in `rofr_pending` looks identical to one silent for 9 days in `signing` until you remember the baselines. A ROFR deadline 3 days out buried under 40 other deals gets missed. The cost of a missed signal is a broken deal — so analysts over-check, which burns the hours they should be spending on the interventions themselves.

## 🎯 What

An agentic monitoring system that, on every tick:

1. **Screens** every live deal with a small LLM to decide which ones warrant deep investigation.
2. **Investigates** the shortlist by evaluating six risk dimensions and deriving a severity (`escalate` / `act` / `watch` / `informational`).
3. **Drafts** the actual intervention text — the email, the status update, the flag — so the analyst reviews a ready-to-send artifact instead of a blank box.
4. **Ranks** the top 5–7 items into a Daily Brief with approve / edit / dismiss in place.

The analyst stays in control: nothing sends autonomously, every flag carries visible reasoning, and snoozing or approving a deal suppresses re-alerts for N ticks to stop the loop from nagging.

> 📄 See [`WRITEUP.md`](WRITEUP.md) for the submission writeup, [`DESIGN.md`](DESIGN.md) for design rationale, and [`assignment.md`](assignment.md) for the original brief.

## 🏗️ How

Two LangGraph agents behind a single FastAPI process, backed by SQLite and rendered through HTMX + Alpine + Tailwind.

- **Pipeline Monitor** — a StateGraph that screens → queues → fans out.
- **Deal Investigator** — a sub-graph with a bounded enrichment loop (≤ 2 rounds) that picks which tools to call before committing to a severity.

Five of the six risk dimensions are evaluated in one combined LLM call; the sixth (`counterparty_nonresponsiveness`) is deterministic from last-inbound timestamps — cheaper and more defensible than asking the LLM for facts it can compute.

Models route through OpenRouter (`SLM_MODEL` for attention screen, `LLM_MODEL` for investigation and brief composition), so either can be swapped in `.env` without code changes. A 39-scenario YAML fixture suite drives Tier 1 deterministic eval; a Tier 2 LLM-as-judge pass (deepeval + optional Langfuse tracing) grades reasoning quality on top.

```text
Analyst Browser  (HTMX + Alpine + Tailwind)
     ↕  HTTP partials
FastAPI  (single process)
     ├─ APScheduler → run_tick()
     │    ├─ Pipeline Monitor (LangGraph StateGraph)
     │    │    ├─ screen_with_SLM              — attention scoring, ThreadPool × 8
     │    │    ├─ select_investigation_queue
     │    │    ├─ fan_out_investigators        — up to 8 parallel
     │    │    │    └─ Deal Investigator sub-graph
     │    │    │         ├─ enrichment loop    (≤ 2 rounds)
     │    │    │         ├─ risk evaluation    (5 LLM dims + 1 deterministic)
     │    │    │         └─ severity routing + intervention drafting
     │    │    └─ close_tick → Brief Composer
     ├─ Routes:  /brief · /pipeline · /deals/{id} · /interventions/… · /sim/advance · /status
     └─ SQLite:  domain.db (7 tables) + agent_checkpoints.db (LangGraph)
```

> Full diagram in [`docs/architecture.md`](docs/architecture.md).

## 🧰 Tech Stack

| Layer | Tool | One-liner |
|---|---|---|
| **Language** | Python 3.13 | Modern, typed, fast enough for an I/O-bound agent loop. |
| **Package manager** | [uv](https://docs.astral.sh/uv/) | Resolves + installs in seconds; lockfile enforced. |
| **Agent framework** | [LangGraph](https://langchain-ai.github.io/langgraph/) | StateGraphs with SQLite checkpointing — resumable agents, not one-shot chains. |
| **LLM gateway** | [OpenRouter](https://openrouter.ai/) | One key, swap any model (Qwen, Gemma, GPT, Claude) via `.env`. |
| **LLM client** | `langchain-openai` | Thin wrapper with structured output + retry. |
| **Schemas** | [Pydantic v2](https://docs.pydantic.dev/) | Validates domain, LLM outputs, and HTTP payloads. |
| **Web framework** | [FastAPI](https://fastapi.tiangolo.com/) | Async routes, Pydantic-native, great Jinja integration. |
| **Scheduler** | [APScheduler](https://apscheduler.readthedocs.io/) | In-process cron for tick loop (no extra infra). |
| **Templates** | [Jinja2](https://jinja.palletsprojects.com/) | Server-rendered partials; HTMX swaps them in. |
| **Frontend** | [HTMX](https://htmx.org/) + [Alpine.js](https://alpinejs.dev/) | Hypermedia-first; no SPA, no build step on the client. |
| **Styling** | [Tailwind CSS](https://tailwindcss.com/) (CLI build) | Utility-first, minified output, dark mode included. |
| **Database** | SQLite (WAL) | Single-file, embedded, plenty for the agent's write load. |
| **Migrations** | Hand-rolled idempotent `ALTER TABLE` | No Alembic; small enough that plain SQL wins. |
| **Logging** | [structlog](https://www.structlog.org/) | Key-value logs, human or JSON render. |
| **Config** | YAML + `python-dotenv` | YAML for fixtures, `.env` for secrets. |
| **Eval (Tier 1)** | pytest-style fixture runner | 39 YAML scenarios, deterministic oracle. |
| **Eval (Tier 2)** | [deepeval](https://github.com/confident-ai/deepeval) | LLM-as-judge metrics on top of Tier 1 traces. |
| **Observability** | [Langfuse](https://langfuse.com/) (optional, self-hosted) | Traces + scores per eval run; no-ops when unset. |
| **Tests** | pytest | Integration, smoke, unit, eval tiers. |
| **Lint** | [ruff](https://docs.astral.sh/ruff/) | Fast lint + format in one. |
| **AI coding** | [Claude Code](https://claude.com/claude-code) | Primary building instrument — specs, prompts, graph code, evals. |
| **Spec-driven workflow** | [Spec Kit](https://speckit.org/) | Machine-checkable contracts and YAML fixtures before any prompt was written. |
| **Design system** | [Impeccable](https://impeccable.style/) | Linear-style density, dark mode, and View Transitions — set the bar for the UI. |

## 🚀 Quickstart

Requires **Python 3.13** (managed via [`uv`](https://docs.astral.sh/uv/)), **Node 18+** for the Tailwind CLI build, and optionally **Docker** for self-hosted Langfuse.

```bash
# 1. Install Python + Node deps, initialise the SQLite DB, build CSS
make setup

# 2. Copy env template and set your OpenRouter key
cp .env.example .env
# edit .env → set OPENROUTER_API_KEY

# 3. Seed 55 deals, fast-forward 3 simulated ticks, start the server
make demo
```

The demo opens the Daily Brief at <http://localhost:8000/brief> with engineered-issue deals ranked and ready for approval.

### Commands

| Command | What it does |
|---|---|
| `make setup` | `uv sync`, init SQLite schema, build Tailwind CSS |
| `make seed` | Reset and reseed with 51 live + 4 historical deals |
| `make run` | Start the FastAPI server (no seeding) |
| `make demo` | Seed → 3 simulated ticks → start server |
| `make eval` | Run 39 Tier 1 deterministic scenarios |
| `make eval-deep` | Run Tier 2 LLM-as-judge pass (deepeval) |
| `make langfuse-up` / `langfuse-down` | Start/stop self-hosted Langfuse (Docker) |
| `make test` | `pytest tests/ -v` |
| `make lint` | `ruff check src/ tests/` |
| `make clean` | Remove `domain.db` and `agent_checkpoints.db` |

> ⚠️ Always prefix Python invocations with `uv run` — the venv deps require it.

## 🖥️ UX

- **Daily Brief** (`/brief`) — top 5–7 ranked items with drafted interventions and in-place approve / copy / dismiss. Each escalation shows a five-section labelled body (*What's blocked / How long it's been stuck / What we've already tried / Why this matters / The ask*) and a primary-tinted "Suggested next step" callout below it. Outbound nudges render as 2–3 short paragraphs (Situation / The ask / Door open). Hovering a row's summary reveals the full untruncated content as a floating preview; collapsed-by-default "Watch list — drifting deals" panel at the bottom surfaces deterministic-watch deals from the pipeline-health computation that the LLM investigator's queue doesn't have budget for.
- **Pipeline** (`/pipeline`) — book-of-deals table with deterministic health tiers; filters, sort, and free-text search run client-side against `data-*` attributes — retriage is keystroke-latency.
- **Deal detail** (`/deals/{id}`) — cross-document View Transitions: severity badge and deal ID morph into the detail header (Chrome / Safari). Firefox falls back to plain navigation. The "Severity Rationale" panel renders the agent's reasoning as a 2–4-sentence narrative in plain English (no dimension codes, no `conf=`/`baseline` jargon, ROFR glossed parenthetically); the "Risk Dimensions · raw" section is a separate transparency view for power users.
- **Simulation controls** (`/sim`) — advance the clock manually, autoplay ticks, or inspect tick status.
- **Stage labels** are humanized everywhere user-facing — `STAGE_DISPLAY_NAMES` is the source of truth (`rofr_pending` → "ROFR review", `docs_pending` → "documents pending", etc.); two Jinja filters (`human_stage`, `humanize_stage_codes`) clean both direct stage values and free-text fields.
- **Keyboard shortcuts** (Brief: `j`/`k` navigate, `Enter` expand, `a` approve, `d` dismiss, `Esc` deselect; Pipeline: same plus `/` to focus search). Form-element guard prevents shortcuts from hijacking typing; modifier-key guard preserves browser shortcuts.

### Screens

<table>
<tr>
<th align="center" width="33%">Daily Brief</th>
<th align="center" width="33%">Pipeline</th>
<th align="center" width="33%">Deal Detail</th>
</tr>
<tr>
<td><img src="assets/Daily%20Brief%20Screen.png" alt="Daily Brief — light mode"></td>
<td><img src="assets/Pipeline%20Screen.png" alt="Pipeline — light mode"></td>
<td><img src="assets/Deal%20Screen.png" alt="Deal detail — light mode"></td>
</tr>
<tr>
<td><img src="assets/Daily%20Brief%20Screen%20-%20Dark%20Mode.png" alt="Daily Brief — dark mode"></td>
<td><img src="assets/Pipeline%20Screen%20-%20Dark%20Mode.png" alt="Pipeline — dark mode"></td>
<td><img src="assets/Deal%20Screen%20-%20Dark%20Mode.png" alt="Deal detail — dark mode"></td>
</tr>
</table>

## 🎯 Six Risk Dimensions

Evaluated per investigation — **5 via one combined LLM call, 1 deterministic**:

| # | Dimension | Trigger |
|---|---|---|
| 1 | `stage_aging` | days in stage vs. `DWELL_BASELINES[stage]` |
| 2 | `communication_silence` | days since last inbound, banded by stage |
| 3 | `missing_prerequisites` | non-empty blocker list |
| 4 | `deadline_proximity` | ROFR deadline ≤ 10 days |
| 5 | `unusual_characteristics` | first-time buyer · prior breakage · multi-layer ROFR |
| 6 | `counterparty_nonresponsiveness` | **deterministic** — from last-inbound timestamps |

The severity decision tree (`escalate` / `act` / `watch` / `informational`) lives in [`src/hiive_monitor/llm/prompts/severity.py`](src/hiive_monitor/llm/prompts/severity.py) and is summarised in [CLAUDE.md](CLAUDE.md#severity--risk-rules).

## 📊 Eval Scorecard

<!-- scorecard-start -->
**Tier 1 (deterministic):** 35 / 39 (89%) — 2026-04-18. Run `make eval` to regenerate.
<!-- scorecard-end -->

Tier 2 (LLM-as-judge) requires `EVAL_JUDGE_MODEL` plus an OpenRouter key. Langfuse is optional — the tracer no-ops gracefully when `LANGFUSE_*` keys are unset.

## 📁 Project Layout

```text
src/hiive_monitor/
  agents/       LangGraph monitor + investigator + brief composer
  db/           SQLite schema, DAO, migrations
  llm/          OpenRouter client, prompts, validators
  models/       Pydantic domain + risk schemas
  seed/         Fixture seeding + demo tick runner
  eval/         Fixture runner, deepeval judge, Langfuse sync
  web/          FastAPI app, routes, Jinja templates, static assets
tests/                                integration · smoke · unit · eval
specs/001-deal-pipeline-monitor/      spec kit artifacts (spec, plan, tasks, contracts)
eval/fixtures/                        39 YAML scenarios
docs/                                 architecture · assumptions · blockers · reflection
```

## 📚 Assumptions & Limits

- **Operational assumptions** (dwell-time baselines, issuer metadata, ROFR defaults) — [`docs/assumptions.md`](docs/assumptions.md)
- **Known limitations & deferred work** — [`docs/blockers.md`](docs/blockers.md)
- **Reflection** — [`docs/reflection.md`](docs/reflection.md)

## 📝 Session Transcripts

Markdown exports of the Claude Code sessions behind this project:

- 🔍 [Audit, Simplify, and Improve](Claude%20Sessions/Claude%20Session%3DAudit%2C%20Simplify%2C%20and%20Improve/session.md)
- 🐛 [Debugging](Claude%20Sessions/Claude%20Session%3DDebugging/session.md)
- 🏗️ [Implement Deal Pipeline](Claude%20Sessions/Claude%20Session%3DImplement%20Deal%20Pipeline/session.md)
- 🎨 [Setup Impeccable](Claude%20Sessions/Claude%20Session%3DSetup%20Impeccable/session.md)
- 📊 [Summarize Evals](Claude%20Sessions/Claude%20Session%3DSummarize%20Evals/session.md)
- 🔧 [Working](Claude%20Sessions/Claude%20Session%3DWorking/session.md)
