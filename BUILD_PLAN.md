# Build Plan: Deal Pipeline Monitoring Agent

> **Purpose.** This document is the concrete scope and sequencing plan. It complements `PROJECT_CONTEXT.md` (what we're building and why) and `.specify/memory/constitution.md` (the principles that govern how). Read all three before planning, tasking, or implementing. When a downstream decision needs to resolve a scope question, this document is the source of truth.
>
> **Status.** This is a pre-implementation planning artifact. Some quantities (seed deal count, attention threshold, etc.) drifted during build — the current implementation is the authoritative reference; see [`README.md`](README.md) and [`docs/architecture.md`](docs/architecture.md).

---

## 1. The shape of what we're building

We are building a monitoring agent for Hiive's Transaction Services team. At the core of the system sit **two genuine agents** — reasoning loops with state, not LLM calls pretending to be agents — and a small set of **schema-validated single-purpose LLM calls** that serve them. Around that sits a **server-rendered analyst interface**, a **dual-mode scheduler**, a **persistent state store**, and a **two-tier evaluation harness**.

The rest of this document makes every one of those words concrete.

---

## 2. The two agents

### 2.1 The Pipeline Monitor

**What it is.** A supervisor-style LangGraph agent that runs on every tick of the scheduler. Its job is to decide, for every live deal, whether this deal warrants deep reasoning *this tick*.

**Why it's genuinely agentic.** It makes a loop-shaped reasoning decision — "given what I already know about this deal, and what happened recently, is investing a Sonnet call here worth it?" — rather than blindly invoking deep reasoning for every deal every tick. This is cost-aware, context-aware, and stateful.

**Inputs.** The current state of the pipeline (all live deals, their stages, their aging, their last-tick observations).

**Outputs.** A per-tick work plan: a prioritized list of deal IDs that will get the Deal Investigator's attention this tick.

**Screening logic.** Uses Claude Haiku for cheap pre-screening. For each deal, produces a lightweight "attention score" based on: time since last deep review, aging delta since last tick, approaching-deadline proximity, recent-event presence. Deals above threshold enter this tick's investigation queue.

**State.** Persisted via LangGraph's SQLite checkpointer. On restart, the Monitor resumes from the last completed tick; it does not re-process ticks it has already marked complete.

### 2.2 The Deal Investigator

**What it is.** A per-deal LangGraph sub-graph invoked by the Monitor when a deal enters the investigation queue. It observes the deal's full state, evaluates risks across multiple dimensions, decides which risks are real, scores severity, and — if severity warrants — drafts the appropriate intervention.

**Why it's genuinely agentic.** It's a multi-step graph with branching. The graph nodes represent reasoning steps with real decisions between them: observe → evaluate-risks → decide-severity → (if actionable) draft-intervention → emit-observation. Branching logic means some deals exit the graph early with "no action needed"; others traverse the full path to a drafted intervention.

**Inputs.** A single deal ID and the current simulated clock time.

**Outputs.** One or more structured observations written to the audit trail, and optionally a drafted intervention added to the reviewer queue.

**State.** Ephemeral per-invocation, but every observation it emits is persisted in the domain database. The graph's internal state is checkpointed so a crash mid-investigation does not double-emit.

### 2.3 What the two agents are *not* doing

To be explicit: we are deliberately not creating agents for the following, because they are single-purpose LLM calls with bounded outputs, not reasoning loops:

- Risk dimension evaluation (six separate prompts, each a constrained call)
- Severity scoring (one call with a rubric)
- Intervention drafting (one call per intervention type)
- Daily brief composition (one call that ranks and writes)
- LLM-as-judge evaluation (one call per rubric item)

Calling these "agents" would be cargo-cult agent architecture. They are *components the agents use*.

---

## 3. The six risk dimensions

The Deal Investigator evaluates each deal across six dimensions. Each has explicit detection logic. Five use LLM reasoning; one is deterministic.

| # | Dimension | Detection | Signal examples |
|---|---|---|---|
| 1 | **Stage aging** | LLM reasons against per-stage dwell baselines + this deal's specific context | Deal in "docs pending" 9 days vs. 3-day baseline |
| 2 | **Deadline proximity** | LLM reasons about known deadlines (especially ROFR expiry) + prerequisite state | ROFR expires in 48h, issuer hasn't responded |
| 3 | **Communication silence** | LLM reasons about last outbound comm vs. stage-appropriate response window | 6 days since last outreach to issuer counsel, stage baseline 2 days |
| 4 | **Missing prerequisites** | LLM reasons about what's required for the next stage transition + what's present | Signing stage can't start without signed transfer agreement |
| 5 | **Counterparty non-responsiveness** | **Deterministic lookup** against issuer metadata + template-based reasoning | Known-slow issuer (e.g., SpaceX historical) + elevated aging |
| 6 | **Unusual characteristics** | LLM reasons about deal-specific factors vs. normal distribution | 5x typical deal size, first-time buyer, prior-breakage history |

Dimension #5 is deterministic on purpose. Some knowledge is factual (this issuer historically responds in 4 days) and doesn't benefit from LLM reasoning — it benefits from being a reliable, cheap, templated check. Including it shows judgment: not every component needs an LLM.

---

## 4. Severity and intervention types

### 4.1 Severity levels (four)

| Level | What happens | Example |
|---|---|---|
| **Informational** | Logged to audit trail only, not surfaced | Deal advanced a stage normally |
| **Watch** | Visible in per-deal view, not in Daily Brief | Stage aging at 75% of baseline, not yet critical |
| **Act** | Surfaces in Daily Brief with drafted intervention | Communication silent 5 days when baseline is 3 |
| **Escalate** | Top of Daily Brief, flagged for analyst attention | ROFR expires in 48h, issuer unresponsive |

Severity is determined by a Sonnet call with a rubric that takes the full risk picture as input. Not a formula, not a sum of scores — real judgment, because that's what we're demonstrating.

### 4.2 Intervention types — MVP (three)

Every intervention carries: the specific deal it's about, the reasoning that produced it, references to the specific facts in the deal state (no invented content), and a structured form appropriate to its type.

1. **Outbound nudge** — drafted email to the party who owes the next action. Subject line, body, appropriate tone for the recipient type (buyer, seller, issuer counsel, internal legal). References prior thread when one exists. Specifies the deadline if relevant.

2. **Internal escalation** — short Slack-style message to the analyst's manager or head-of-desk summarizing the risk and the proposed action. Under 50 words. Reads like an internal message, not an email.

3. **Brief entry** — one-line summary for the Daily Brief with the specific action the analyst should take. Written to be scannable.

### 4.3 Intervention types — Stretch (fourth)

4. **Status recommendation** [STRETCH] — suggestion to update the deal's internal risk flag. Not autonomously applied — presented to the analyst as "consider marking this deal as at-risk because [reasoning]." The analyst would manually apply in their source system.

See Section 9 for stretch gating.

---

## 5. The analyst interface (screens)

### 5.1 Screen 1: Daily Brief [MVP]

The analyst's landing page.

**Header**
- Title, current simulated clock time, simulation-control buttons (advance N days, toggle real-time/simulated)

**Main content — Today's Priorities tab**
- Top 5–7 deals requiring attention today, ranked by priority
- Each item shows: deal ID, issuer name, deal size, severity badge, one-line summary, expandable reasoning, drafted message preview, action buttons (Approve & Copy Draft / Edit / Dismiss)

**Secondary tab — All Open Items**
- Every "act" or "escalate" item across all deals
- Filters: severity, stage, issuer
- Same per-item actions

**Sidebar**
- Link to Per-Deal Detail list (browse all deals)
- Link to eval scorecard
- System status indicator (last tick time, deals processed this tick)

### 5.2 Screen 2: Per-Deal Detail [MVP]

The drill-down view. Non-negotiable — this is where the agent's reasoning becomes inspectable.

**Header**
- Deal facts: issuer, parties (buyer ID, seller ID), size (shares × price), current stage, stage entry time, key deadlines

**Left column — Event timeline**
- Chronological list of everything that has happened on this deal
- Stage transitions, documents received, communications sent/received
- Visual distinction between incoming and outgoing items

**Right column — Agent observation history**
- One entry per tick where the agent recorded anything about this deal
- Each entry shows: tick time, dimensions evaluated, severity determination, reasoning, any intervention drafted
- Collapsed by default, expandable for full reasoning text

**Bottom section — Intervention history**
- All interventions the agent has drafted for this deal
- Status: pending / approved / edited / dismissed
- For approved: the final text that was marked-as-sent

### 5.3 Screen 3: Reviewer Queue as standalone page [STRETCH]

If MVP is complete and polished, promote "All Open Items" from a tab to a full dedicated page with:
- Batch operations (batch dismiss, batch mark-handled)
- Saved filter states
- Sort-by options beyond severity (by aging, by value, by time-in-queue)
- Keyboard shortcuts for power-user flow

Gated — see Section 9.

---

## 6. Data model and synthetic data

### 6.1 Database schema

Three domain tables in SQLite, separate from LangGraph's checkpointer database.

**`deals` table**
- `id` — primary key (deal ID like `D-2026-00042`)
- `issuer_id` — FK to issuers table
- `buyer_party_id`, `seller_party_id` — FKs to parties table
- `shares`, `price_per_share` (derived: `total_size`)
- `current_stage`, `stage_entered_at` (simulated time)
- `rofr_deadline` (if applicable, simulated time)
- `is_first_time_buyer` — boolean
- `prior_breakage_count` — integer
- `current_blockers` — JSON list of structured blocker objects
- `created_at` — simulated time

**`events` table** (append-only)
- `id` — primary key
- `deal_id` — FK
- `event_type` — enum (stage_transition, doc_received, comm_sent, comm_received, blocker_resolved, etc.)
- `occurred_at` — simulated time
- `payload` — JSON with type-specific details
- `created_at` — real time (for debugging)

**`agent_observations` table** (the audit trail)
- `id` — primary key
- `deal_id` — FK (or NULL for pipeline-wide observations)
- `tick_id` — correlation ID
- `agent_type` — enum (monitor, investigator)
- `severity` — enum (informational, watch, act, escalate) or NULL for Monitor-level observations
- `dimensions_evaluated` — JSON array of dimension names
- `reasoning` — full structured reasoning object as JSON
- `intervention_id` — FK to interventions table, if one was drafted
- `observed_at` — simulated time
- `created_at` — real time

**`interventions` table**
- `id` — primary key
- `deal_id` — FK
- `intervention_type` — enum (outbound_nudge, internal_escalation, brief_entry, status_recommendation)
- `recipient_type` — enum when applicable (buyer, seller, issuer_counsel, internal_manager, etc.)
- `subject`, `body` — text
- `status` — enum (pending, approved, edited, dismissed)
- `final_text` — the text after analyst edits, if any
- `created_at`, `acted_at` — timestamps

**`issuers` table** (metadata)
- `id` — e.g., `ISS-SPACEX`
- `name` — e.g., `SpaceX`
- `typical_rofr_window_days` — integer
- `response_speed` — enum (fast, normal, slow)
- `multi_layer_rofr` — boolean
- `sector` — enum

**`parties` table**
- `id` — e.g., `PTY-00017`
- `party_type` — enum (buyer, seller, issuer_counsel, internal, etc.)
- `display_name` — e.g., `Buyer #17` or `SpaceX General Counsel`
- `is_first_time_on_platform` — boolean

### 6.2 Seed data — MVP quantities

**30 deals** distributed across stages:
- 3 at bid-accepted/docs-pending (fresh)
- 5 at issuer-notified (ROFR clock running, clean)
- 4 at ROFR-pending with varied time-remaining (5–25 days)
- 3 at ROFR-cleared/signing
- 2 at funding
- 5 at settled (historical, provides dwell-time baselines)
- 3 at broken (historical)
- **5 engineered-issue deals** — the demo's showcase scenarios

**10 issuers** from Hiive's public listings: SpaceX, Stripe, Anthropic, Perplexity, Cerebras, Groq, Databricks, Canva, Rippling, Ramp. Each has engineered metadata (response speed, ROFR window, multi-layer flag) that creates variety.

**~15 simulated parties** across buyer/seller/counsel/internal types.

**~150 events** across all deals (avg 5 per deal, more for actively-progressing ones).

**~75 communication items** — this is where realism matters most. Emails drafted to read like actual Transaction Services analyst outreach. Budget real time on voice calibration here.

### 6.3 The five engineered-issue deals (MVP)

These are the showcase scenarios. Every demo run will highlight at least three of them; the eval harness references all five.

1. **ROFR cliff** — Stripe deal with ROFR expiring in 48h, issuer counsel silent 6 days → should **escalate**
2. **Docs stalled** — Anthropic deal at docs-pending 9 days, buyer hasn't provided accreditation → should **act**
3. **Signing silence** — Perplexity deal at signing stage, seller's counsel silent 4 days → should **act**
4. **Large + first-time** — Cerebras deal at 3x typical size, first-time buyer → should **watch**
5. **Clean deal (control)** — Databricks deal progressing normally on every dimension → should emit **informational** only

Scenario #5 is the false-positive test. If the agent flags it, we have a calibration problem.

### 6.4 Stretch: scale to 40 deals + 3 more engineered scenarios

If time permits (see Section 9): add 10 more deals for volume and add three more engineered scenarios:
6. **Multi-layer ROFR** — primary ROFR expired but secondary holders not notified → **act**
7. **Known-slow issuer + aging** — SpaceX deal aging at 120% of baseline but contextualized → **watch**
8. **Wire confirmation gap** — buyer sent wire instructions, seller hasn't confirmed receipt in 2 days → **act**

---

## 7. Evaluation harness

### 7.1 Tier 1: Deterministic assertions [MVP]

**Scenario format.** YAML fixtures, each describing a seeded deal situation and a set of structured assertions.

**Example assertion shape:**
```yaml
scenario: rofr_cliff_should_escalate
setup:
  deal_id: ISS-STRIPE-00001
  initial_stage: rofr_pending
  rofr_deadline_hours: 48
  last_issuer_response_days_ago: 6
assertions:
  - agent_flags_deal: true
  - severity: escalate
  - dimensions_triggered_includes: [deadline_proximity, communication_silence]
  - intervention_drafted: true
  - intervention_type: outbound_nudge
  - intervention_recipient_type: issuer_counsel
  - intervention_body_contains_deadline: true
  - deal_appears_in_daily_brief_top_n: 3
```

**Scenario inventory — MVP (15 scenarios):**

*Detection (8 — one positive, one negative per dimension × 4 LLM-reasoned dimensions)*
- Stage aging positive / negative
- Deadline proximity positive / negative
- Communication silence positive / negative
- Missing prerequisites positive / negative

*Prioritization (4)*
- ROFR-escalate outranks comm-silence-act in brief
- Two risks on same deal produce one flagged item with both reasons
- Watch severity does not appear in brief when act items exist
- Ties broken by time pressure

*Intervention quality — structural (3)*
- Email to issuer counsel includes deadline reference
- Email to seller references specific missing doc by name
- Internal escalation is under 50 words

**Runner.** A single command (`make eval`) that loads each scenario, sets up the state, advances the simulated clock as the scenario requires, invokes the agent, and checks all assertions. Produces a scorecard.

**Scorecard output.**
- Pass/fail per scenario with failure reason
- Aggregate: pass rate, precision per dimension, recall per dimension
- Severity calibration: confusion matrix of predicted vs. expected severity
- Written to `eval_results/scorecard_<timestamp>.md` and the most recent scorecard referenced in the README

### 7.2 Tier 1 Stretch: 23 scenarios total

If MVP is complete (see Section 9): extend to 23 scenarios by adding:
- Counterparty non-responsiveness positive / negative (2)
- Unusual characteristics positive / negative (2)
- Prioritization: long-aging watch promotes to act when adjacent acts resolve (1)
- Intervention quality: tone differs between Slack and email (1)
- Intervention quality: references prior thread when present (1)
- Intervention quality: does NOT invent facts not in deal state (1)

### 7.3 Tier 2: LLM-as-judge [STRETCH]

**What it evaluates.** Drafted intervention quality — the subjective dimensions that structural assertions can't catch.

**Rubric (5 criteria, each scored 1–5 by Sonnet):**
1. Tone appropriateness for recipient type
2. Factual grounding in specific deal facts (no invented content)
3. Correct reference to relevant deadlines
4. Appropriate urgency level
5. Readability and professional voice

**Scale.** Runs on 10–15 scenarios to keep cost bounded. Target mean score: 4.0+/5 across all rubric items.

**Output.** Appended to the scorecard with per-rubric-item mean scores and per-scenario breakdowns.

Gated — see Section 9.

---

## 8. Scheduler and simulated clock

### 8.1 The clock abstraction

**The single most important architectural detail in the entire build.** Every timestamp in the system reads from a single `Clock` abstraction. Nothing calls `datetime.now()` directly. Ever.

The clock has two implementations:
- **RealTimeClock** — returns wall-clock time, used for live demo
- **SimulatedClock** — returns an injected time that can be advanced programmatically, used for eval runs and demo acceleration

Environment variable or CLI flag selects which one is active at app startup.

**Why this matters.** If we fail to centralize time, some component will read real time while another reads simulated time, and bugs of the "this deal is aging at -7 days" variety will appear at hour 45 and eat four hours to debug. We set this up on day one.

### 8.2 APScheduler integration

- In real-time mode: scheduler ticks on wall-clock cadence (default every 60 seconds for live demo)
- In simulated mode: ticks are driven by the eval harness or by manual "advance N days" UI button
- Both modes go through the same code path — only the clock source differs

### 8.3 Idempotency on restart

- Every tick has a `tick_id` written atomically when the tick starts and updated when complete
- On startup, the Monitor checks the most recent incomplete tick and resumes or skips based on completion state
- Observations and interventions carry their `tick_id` and are checked for duplicates on insert

---

## 9. MVP slice vs. stretch queue — the sequencing rule

### 9.1 What ships no matter what (MVP slice)

Everything in this list is shipped, polished, and demoable at hour 55. No exceptions.

- **Agents:** Pipeline Monitor, Deal Investigator (both)
- **Risk dimensions:** all six (five LLM-reasoned, one deterministic)
- **Severity levels:** all four
- **Intervention types:** three (outbound nudge, internal escalation, brief entry)
- **Screens:** two (Daily Brief with All Open Items tab, Per-Deal Detail)
- **Time modes:** both real-time and simulated
- **Seed data:** 30 deals across 10 issuers, 5 engineered-issue scenarios, realistic communications
- **Evaluation:** Tier 1 with 15 scenarios, scorecard output
- **Packaging:** Makefile with setup, seed, run, eval, demo, clean targets
- **Docs:** README, writeup, reflection, demo recording

### 9.2 Stretch queue with hard-start cut-offs

Stretch items are ordered by expected impact vs. effort. Each has a "do not start after hour N" rule. If the clock passes the cut-off and the item has not been started, it is abandoned for this submission. This rule is enforced strictly.

| # | Item | Est. hours | Do not start after | Rationale |
|---|---|---|---|---|
| 1 | Tier 2 LLM-as-judge on 10–15 scenarios | 3 | **hour 50** | Biggest single signal of evaluation rigor; worth starting earliest |
| 2 | Scale from 15 → 23 golden scenarios | 2 | **hour 50** | Adds statistical weight to scorecard; low-risk extension |
| 3 | Fourth intervention type (status recommendation) | 1 | **hour 52** | Cheap once core infrastructure works; low risk |
| 4 | Scale from 30 → 40 deals + 3 engineered scenarios | 2 | **hour 50** | Makes pipeline feel fuller; synthetic data generator extends easily |
| 5 | Screen 3 as standalone page with batch ops | 3 | **hour 45** | Biggest effort; must start earliest of any stretch to finish |
| 6 | Document collection tracking — system knows required docs per stage, tracks which are received, drafts document-specific requests naming the exact missing document | 3 | **hour 48** | Improves intervention draft quality for the most common stall type |
| 7 | ROFR outcome tracking — when ROFR is exercised, agent opens a distinct workflow for coordinating with issuer/assignee as substitute buyer, drafts communication to original buyer, logs exercise to issuer history for future deals | 4 | **hour 45** | Handles the 18% of Hiive deals where ROFR is exercised — a distinct workflow the MVP treats as a generic event |

Items 6 and 7 were deferred from MVP after evaluating scope. Item 6 improves intervention draft quality for the most common stall type. Item 7 handles the 18% of Hiive deals where ROFR is exercised — a distinct workflow the MVP treats as a generic event. Both require the MVP slice to be complete and polished before starting.

### 9.3 Decision protocol

**At hour 40**, check status of MVP slice:
- If any MVP item is still in progress → complete MVP first, stretch items may fall off
- If MVP slice is complete and polished → begin stretch queue in order (start with #5 Screen 3, since its cut-off is earliest)

**At each stretch cut-off hour**, check:
- If item N has been started but not finished → complete it, move to next
- If item N has not been started → skip it permanently, move to item N+1 if its cut-off hasn't passed yet

**No stretch item gets partial-shipped.** A half-built fourth intervention type or a half-built Screen 3 is worse than none. The cut-off rule exists to prevent this.

### 9.4 Scope valves in reserve (if MVP itself is at risk)

Hopefully not needed, but named for safety. If at hour 50 the MVP slice is still incomplete:

- Reduce eval scenarios to 12 (drop 3 prioritization scenarios — the hardest to author correctly)
- Reduce engineered-issue deals to 4 (drop the "large + first-time" since it's the most nuanced)
- Reduce Daily Brief polish to functional-but-plain (skip final Tailwind refinement pass)

These are contingency valves, not planned cuts.

---

## 10. Budget and estimate

### 10.1 MVP slice estimate (hours)

| Work area | Hours |
|---|---|
| Project setup, Spec Kit flow through `/speckit.analyze` | 4 |
| Clock abstraction + APScheduler integration | 3 |
| SQLite schema + migrations + base models | 2 |
| Synthetic data generator with realistic comms (30 deals, 10 issuers, 5 engineered scenarios) | 4 |
| Pipeline Monitor (LangGraph, with Haiku screening) | 4 |
| Deal Investigator (LangGraph sub-graph) | 6 |
| Five LLM-reasoned risk dimensions (prompts + schemas + calibration) | 5 |
| One deterministic risk dimension | 0.5 |
| Severity scoring (prompt + schema + calibration) | 2 |
| Three intervention drafters (prompts + schemas + voice calibration) | 4 |
| Daily brief composer | 2 |
| FastAPI backend + Jinja templates foundation | 2 |
| HTMX + Tailwind foundation (layout, typography, design tokens) | 3 |
| Screen 1 (Daily Brief + All Open Items tab) | 4 |
| Screen 2 (Per-Deal Detail) | 3 |
| Simulation controls (advance N days, mode toggle) | 2 |
| Tier 1 eval harness + 15 golden scenarios | 5 |
| Observability (structlog, correlation IDs, debug view) | 2 |
| README, writeup, reflection, demo recording | 4 |
| **MVP total** | **~55.5** |

### 10.2 Stretch queue estimate (hours)

| Item | Hours |
|---|---|
| Screen 3 as standalone page | 3 |
| Fourth intervention type | 1 |
| Scale 30 → 40 deals + 3 scenarios | 2 |
| Scale 15 → 23 golden scenarios | 2 |
| Tier 2 LLM-as-judge | 3 |
| **Stretch total** | **~11** |

**Full ambition:** ~66 hours. MVP slice fits comfortably in 55. Stretch queue gives ~10 hours of optional work with hard gates.

---

## 11. Top risks with mitigations

| # | Risk | Mitigation |
|---|---|---|
| 1 | Clock abstraction bugs that hide behind "is it real or simulated" | Build clock primitive in first 3 hours, write a test that catches any direct `datetime.now()` call elsewhere |
| 2 | LangGraph checkpointer + simulated clock interaction untested | Validate this interaction within first 8 hours of build; if broken, fall back to simpler "restart from last DB-persisted state" |
| 3 | Intervention voice sounds like generic LLM output | Budget 2 explicit hours on voice calibration before scaling. Review first 5 generated drafts by hand before approving the prompt. |
| 4 | Synthetic data feels fake | Review first 10 generated comms before generating the rest. Adjust tone prompt if needed. |
| 5 | Claude API costs balloon during development | Haiku for screening from day one; use cassette-based mocking for eval harness; expect $50–100 over 60 hours |
| 6 | Prompt calibration for six risk dimensions eats more than budgeted | Per-dimension hard timebox of 1 hour. If calibration isn't working after that, fall back to a simpler rule and document it as a known limitation. |
| 7 | Screen polish goes over budget | Tailwind foundation hour 25–28 max. If you're still fighting CSS at hour 30, ship what you have. |

---

## 12. How the demo actually lands

Walking through the reviewer's 10 minutes with this plan in hand:

**0:00–0:30** — `make demo` seeds the 30-deal pipeline, starts the app, opens the browser. Daily Brief shows 6 deals needing attention today, ranked. The top one is the ROFR-cliff Stripe deal at "escalate" severity.

**0:30–2:00** — Reviewer clicks the Stripe deal. Per-Deal Detail opens. Event timeline shows the deal's history. Agent observation history shows every tick where the agent recorded reasoning — visible progression of "watch" → "act" → "escalate" as the ROFR deadline approached. The drafted email to issuer counsel is visible with the specific deadline called out.

**2:00–3:30** — Reviewer clicks "Advance 3 days" in the simulation control. Pipeline evolves. A deal that was "watch" yesterday is now "act" because its aging crossed threshold. Another deal has settled and dropped out. The Daily Brief refreshes to the new top priorities.

**3:30–5:00** — Reviewer runs `make eval`. Scorecard prints: "15 scenarios, 14 passed, precision 0.93, recall 0.87. Severity confusion matrix: [matrix]. Failed: rofr_long_horizon_negative — agent flagged a clean deal. Reasoning attached in eval_results/." They inspect the failed scenario in the UI.

**5:00–10:00** — Reviewer reads the README, the writeup, and pokes around `src/`. They see: the two genuine agent files, the dimension prompts, the intervention templates, the eval scenario YAML, the synthetic data generator. Every file has a clear purpose. Comments explain *why*, not *what*. The constitution is visible. The writeup explicitly names the tradeoffs that were made.

That's the submission.
