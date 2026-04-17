# Feature Specification: Deal Pipeline Monitoring Agent

**Feature Branch**: `claude/angry-perlman-74f887`
**Created**: 2026-04-16
**Status**: Draft
**Input**: Agentic monitoring system for Hiive's Transaction Services team — two coordinated agents (Pipeline Monitor supervisor + Deal Investigator per-deal sub-graph), six risk dimensions, four severity levels, three intervention types, analyst interface, dual-mode scheduler, SQLite audit trail, Tier 1 evaluation harness.

> **Note on scope boundary.** This spec reflects pre-decided architectural constraints from `BUILD_PLAN.md`. Some items that appear implementation-specific (e.g., SQLite, YAML fixtures, `make` targets, concrete field identifiers) are intentional fixed constraints of the submission, not spec/plan boundary violations. Subjective quality requirements (tone, voice) are deliberately kept at spec level as user requirements; how they are measured is a `/speckit.plan` and eval-harness concern.

## Clarifications

### Session 2026-04-16

- Q: When a new investigation produces a still-actionable severity for a deal that already has a pending intervention, what does the agent do? → A: Suppress a new draft; emit an observation that links to the open pending intervention.
- Q: How is the per-tick investigation queue sized? → A: Monitor's Haiku screening produces an attention score per deal; deals above a configured threshold are candidates; if more than 5 exceed it, take the top 5 by score; the rest defer to the next tick.
- Q: When the analyst advances the simulated clock by N days, how many ticks fire? → A: One tick per simulated day, run sequentially (advancing N days fires N ticks at successive day boundaries). Preserves the "watch priorities evolve" demo story and lets the eval harness assert day-level timing.
- Q: Once the analyst approves or dismisses a Today's Priorities item, what happens to the row? → A: Item stays visible in the list with a "handled" state badge (approved / edited / dismissed) until the next tick recomputes the brief. In-place status swap, no mid-session re-ranking. Gives the analyst visible "what I've already handled this morning" context.
- Q: How does the Daily Brief view receive updates when a new tick completes? → A: Real-time mode polls every 30 seconds (HTMX `hx-trigger="every 30s"`); simulated mode does not poll — the brief re-fetches automatically when the analyst triggers an "Advance N days" action, since that is a discrete user-initiated event.

## Overview

Hiive's Transaction Services Analysts each juggle 40–60 live secondary-market deals simultaneously, across Salesforce, email, Slack, and documents. Deals stall in stages for reasons that are only visible once an analyst digs in: a ROFR clock running out while issuer counsel is silent, a docs-pending buyer who never returned accreditation, a first-time buyer on a 3x-typical-size deal, an issuer known to be slow. Today the analyst detects these through manual scanning. This feature introduces an always-on monitoring agent that reads the pipeline on every tick, reasons about each deal across six risk dimensions, ranks what needs attention today, drafts interventions in Transaction-Services voice, and surfaces full reasoning that the analyst (and a compliance reviewer) can audit.

The agent **never** sends external communications, **never** mutates deal status in a system of record, and **never** takes irreversible action without explicit analyst approval. Every output is a draft or a flag; the analyst decides.

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Daily Brief (Priority: P1)

As a Transaction Services Analyst, when I start my day, I land on a Daily Brief screen that shows the top 5–7 deals requiring my attention today, ranked by priority, each with a drafted intervention and the reasoning behind it. A secondary "All Open Items" tab on the same screen shows every **act** or **escalate** item across all deals with filters by severity, stage, and issuer.

**Why this priority**: This is the core product value — "tell me what I should do today, in priority order, with the draft already written, and show me why." Without it the system has no user-facing reason to exist. It is the first thing a reviewer sees in the demo.

**Independent Test**: With seeded data present, navigating to the Daily Brief URL renders a ranked list of 5–7 items; each item shows deal ID, issuer, size, severity badge, one-line summary, expandable reasoning, and a drafted intervention, and offers Approve-and-Copy, Edit, and Dismiss actions. Switching to the All Open Items tab shows every act/escalate item across the pipeline and the filter controls function.

**Acceptance Scenarios**:

1. **Given** the pipeline contains at least the five engineered-issue deals and at least one tick has run, **When** the analyst opens the Daily Brief, **Then** the ROFR-cliff Stripe deal appears at or near the top with severity **escalate**, a one-line summary referencing the 48-hour ROFR window, expandable reasoning that cites the relevant deadline and the communication-silence signal, and a drafted outbound-nudge email to issuer counsel that references the deadline.
2. **Given** a Daily Brief item is displayed, **When** the analyst clicks Approve-and-Copy, **Then** the draft text is copied to the clipboard, the intervention's status changes to **approved**, and the item is visibly marked as handled without being removed from the audit trail.
3. **Given** a Daily Brief item is displayed, **When** the analyst clicks Edit, changes the body, and confirms, **Then** the edited text persists as `final_text`, status becomes **edited**, and the audit trail records both the original draft and the final text.
4. **Given** a Daily Brief item is displayed, **When** the analyst clicks Dismiss, **Then** status becomes **dismissed**, the item leaves the Today's Priorities tab, and the dismissal is visible in the per-deal intervention history.
5. **Given** multiple act/escalate items exist, **When** the analyst opens the All Open Items tab and filters by issuer = Anthropic, **Then** only Anthropic items remain; filtering by severity = escalate further narrows the list accordingly.
6. **Given** only **watch**-severity items exist pipeline-wide, **When** the analyst opens the Daily Brief, **Then** the Today's Priorities list is empty (watch items do not surface to the brief) and a clear empty-state message is shown.

---

### User Story 2 — Per-Deal Detail View (Priority: P1)

As an analyst, I drill into any deal and see the deal's full picture — facts header, chronological event timeline, the agent's observation history across ticks with full reasoning per tick, and the intervention history with status per item.

**Why this priority**: Explainability is non-negotiable (Constitution IV). The Daily Brief earns trust only if the analyst can trace any flag back to its signals. Without Screen 2 the agent is a black box.

**Independent Test**: Navigating to a deal's detail URL renders a header with deal facts, a timeline of stage transitions / documents / communications, a per-tick agent observation log with expandable reasoning, and an intervention history section with status per item.

**Acceptance Scenarios**:

1. **Given** a deal has progressed through at least two stages and at least three ticks of agent observation, **When** the analyst opens the Per-Deal Detail view, **Then** the header shows issuer, buyer/seller party IDs, shares × price (with derived total size), current stage, stage-entered-at time, and `rofr_deadline` when applicable.
2. **Given** the same deal, **When** the event timeline is shown, **Then** stage transitions, documents received, and communications sent/received are listed in chronological simulated-time order with a visual distinction between incoming and outgoing items.
3. **Given** the agent emitted an observation with severity **act** on a specific tick, **When** the analyst expands that observation, **Then** the tick time, the dimensions evaluated, the severity determination, and the full structured reasoning text are visible, and any intervention drafted on that tick is linked.
4. **Given** multiple interventions have been drafted for the deal, **When** the analyst views the intervention history section, **Then** each intervention shows type, status (pending / approved / edited / dismissed), and — for approved/edited — the final text marked as sent.

---

### User Story 3 — Monitoring Loop (Priority: P1)

As the system, on each scheduled tick the Pipeline Monitor reads every live deal, cheaply screens which deals warrant deep reasoning this tick, and dispatches the Deal Investigator to those deals. A mid-tick crash and restart must not cause duplicate observations or duplicate interventions.

**Why this priority**: No monitoring loop, no monitoring agent. Idempotency is called out because unreviewable duplicate outputs would be a regulatory and trust defect.

**Independent Test**: Starting the scheduler in simulated mode and advancing the clock by one tick causes: (a) a tick record written atomically at tick start, (b) the Monitor emitting a per-tick investigation queue, (c) the Investigator running on each queued deal, (d) observations and any drafted interventions persisted with the same tick correlation ID. Killing the process mid-tick and restarting must not produce duplicate rows keyed on `(tick_id, deal_id)`.

**Acceptance Scenarios**:

1. **Given** the scheduler is running and a tick fires, **When** the Monitor finishes screening, **Then** an investigation queue of zero or more deals is produced, and for every queued deal the Investigator runs and emits exactly one observation keyed on `(tick_id, deal_id)`.
2. **Given** a tick is in progress and the process is killed after the Monitor screened but before the Investigator finished, **When** the process restarts, **Then** the resume logic detects the incomplete tick and completes only the unfinished Investigator runs — no deal gets a duplicate observation for that tick.
3. **Given** the Monitor has previously screened a deal, **When** a new tick fires, **Then** the Monitor's deal-selection logic incorporates time since the deal's last deep review, recent aging delta, deadline proximity, and whether new events have arrived since the last tick.
4. **Given** the scheduler is running in real-time mode with the default cadence, **When** 60 seconds elapse, **Then** a new tick fires; **Given** simulated mode, **When** the simulation controller or eval harness requests a tick, **Then** a tick fires on request rather than on wall-clock cadence.

---

### User Story 4 — Per-Deal Investigation (Priority: P1)

As the Deal Investigator, for each deal the Monitor queues, I follow a variable execution path determined by intermediate findings — not a predetermined sequence. I observe the deal's full state, run a parallel dimension evaluation pass, then reason about whether those results are sufficient to make a reliable severity decision. When results are ambiguous or show compounding signals that may interact in non-obvious ways, I call enrichment tools to fetch additional context before scoring severity. Most deals complete in a single pass. Ambiguous deals go through a bounded enrichment loop. The number of steps taken, the tools called, and the context assembled differ per deal based on what I discover. Every observation written to the audit trail carries full structured reasoning, including any enrichment steps taken and what they contributed.

**Why this priority**: This is where the agent actually reasons. Without it there is no product.

**Independent Test**: Given a seeded deal that matches one of the engineered-issue scenarios, invoking the Investigator must produce an observation with the expected severity, the expected risk dimensions flagged, and — when severity ≥ act — a drafted intervention of the expected type with the expected structural properties. When the scenario includes compounding signals that warrant enrichment, the audit trail must record the enrichment decisions made, the tools called, and the context they returned.

**Acceptance Scenarios**:

1. **Given** the ROFR-cliff Stripe deal (ROFR expires in 48 hours, issuer counsel silent 6 days), **When** the Investigator runs, **Then** the observation's severity is **escalate**, the dimensions triggered include **deadline_proximity** and **communication_silence**, and a drafted **outbound_nudge** email addressed to **issuer_counsel** references the deadline explicitly.
2. **Given** the docs-stalled Anthropic deal (docs_pending 9 days, buyer accreditation missing), **When** the Investigator runs, **Then** severity is **act**, dimensions triggered include **stage_aging** and **missing_prerequisites**, and the drafted outbound_nudge to the buyer references the specific missing document by name.
3. **Given** the large-and-first-time Cerebras deal (3x typical size, first-time buyer, otherwise progressing), **When** the Investigator runs, **Then** severity is **watch**, dimension triggered includes **unusual_characteristics**, and the deal does not appear in the Daily Brief's Today's Priorities tab (watch does not surface).
4. **Given** the clean Databricks control deal, **When** the Investigator runs, **Then** the observation severity is **informational**, no intervention is drafted, and the deal does not appear in the Daily Brief.
5. **Given** an Investigator run returns severity **informational** or **watch**, **When** the sub-graph branches, **Then** the intervention-drafting node is skipped and the observation is still persisted with full reasoning.
6. **Given** the counterparty-non-responsiveness dimension fires, **When** its detection runs, **Then** the detection is deterministic — a lookup against issuer metadata and a templated rationale — not an LLM call.
7. **Given** any LLM call in the Investigator returns output that fails schema validation, **When** the error is caught, **Then** exactly one corrective reprompt is attempted; if that also fails, the failure is logged with correlation IDs and surfaced as an observation error rather than silently dropped.

---

### User Story 5 — Simulation Controls (Priority: P2)

As the analyst (and as the demo viewer), I see the current simulated clock time and can advance it by N days. The pipeline evolves, ticks fire, the agent re-reasons, and the Daily Brief refreshes to reflect new priorities.

**Why this priority**: This is the demo's dynamism. Without it the pipeline is static and the agent's reasoning-over-time story is lost. Dropped one priority below the core agent/UI stories because the MVP still reads as an agent monitor with static data; with simulation it reads as a live system.

**Independent Test**: In simulated mode, clicking "Advance N days" (or calling the equivalent API) changes the displayed simulated clock, triggers the appropriate number of ticks, updates deal ages and deadlines, and the Daily Brief re-renders with priorities that reflect the new state (e.g., a deal whose ROFR was 5 days out is now 1 day out and has risen in priority).

**Acceptance Scenarios**:

1. **Given** the system is in simulated mode at simulated time T, **When** the analyst advances the clock by 3 days, **Then** the displayed clock reads T + 3 days, ticks fire for the elapsed interval at the configured cadence, and deal stage-entry-relative ages reflect the advance.
2. **Given** a deal whose ROFR deadline was 5 days away, **When** the analyst advances 4 days, **Then** the deal's severity on the next Investigator run rises (watch → act or act → escalate as appropriate) and the Daily Brief reorders accordingly.
3. **Given** the system is in real-time mode, **When** the analyst looks for advance-clock controls, **Then** the controls are hidden or disabled (advance-by-N-days applies only to simulated mode).

---

### User Story 6 — Audit Trail & Inspection (Priority: P1)

As an analyst or compliance reviewer, I can trace any agent decision — any risk flag, severity assignment, drafted intervention — back to the tick it was emitted on, the dimensions evaluated, and the structured reasoning that produced it. A debug view (behind a query parameter) exposes raw structured logs for any deal or tick.

**Why this priority**: Non-negotiable per Constitution II and IV. Without inspectability the agent is not deployable in a regulated context, even as a prototype.

**Independent Test**: For any observation row in the audit trail, the Per-Deal Detail view must show the tick correlation ID, simulated timestamp, dimensions evaluated, severity decision, and full reasoning text. Appending `?debug=1` (or equivalent) to any deal or tick URL exposes raw structured log events.

**Acceptance Scenarios**:

1. **Given** an observation in the audit trail, **When** the analyst views it in the Per-Deal Detail view, **Then** tick correlation ID, simulated `observed_at`, real `created_at`, dimensions evaluated, severity, and full structured reasoning are all visible.
2. **Given** an intervention that was drafted on a specific tick, **When** the analyst inspects it, **Then** the originating observation and the tick correlation ID are reachable in one click.
3. **Given** a debug query parameter is present on a deal or tick URL, **When** the analyst loads the page, **Then** raw structured log events (prompt, response metadata, latency, model, correlation IDs) for the LLM calls tied to that deal/tick are visible.

---

### User Story 7 — Evaluation Harness (Priority: P1)

As the builder (and as a reviewer), I run `make eval` and get a scorecard that tells me whether the agent caught the stalls we designed into the data, how well it calibrated severity, and how precise/recall-accurate each risk dimension is.

**Why this priority**: Evaluation is part of the product, not an afterthought (Constitution VI). The scorecard is the single credibility artifact that makes every behavior claim testable.

**Independent Test**: Running `make eval` from a clean state loads all 15 YAML golden scenarios, sets up each deal's state, advances the simulated clock as each scenario requires, invokes the agent, checks the declared assertions, and writes a scorecard markdown file with per-scenario pass/fail, per-dimension precision/recall, and a severity confusion matrix.

**Acceptance Scenarios**:

1. **Given** the 15 Tier 1 scenarios exist under the scenario directory, **When** `make eval` runs, **Then** each scenario's setup is applied to an isolated state store, the agent is invoked, assertions are evaluated, and a scorecard file is written to `eval_results/scorecard_<timestamp>.md`.
2. **Given** a scenario asserts `severity: escalate` and `intervention_recipient_type: issuer_counsel`, **When** the agent produces severity **act** instead, **Then** the scorecard records a fail for that scenario with the specific assertion(s) that failed.
3. **Given** the scorecard is generated, **When** it is rendered, **Then** it contains: pass rate aggregate, per-scenario pass/fail with failure reasons, per-dimension precision and recall, and a severity confusion matrix (predicted × expected).
4. **Given** all 15 scenarios pass in the submission run, **When** the README references the most recent scorecard, **Then** the link resolves to a real file with the aggregated results.

---

### Edge Cases

- **Settled or broken deals**: deals in `settled` or `broken` stages are historical and must not be screened into the investigation queue.
- **Deal with no deadlines**: the deadline-proximity dimension must degrade gracefully (no flag) rather than hallucinate a deadline.
- **LLM output fails validation twice**: the failing observation is persisted with an error marker and a correlation ID; the tick is not blocked; the analyst can see the failure in the debug view.
- **Clock moved backward**: advance-by-N-days must reject negative N; no code path should compute negative dwell times.
- **Duplicate tick fires**: concurrent or retried tick starts with the same `tick_id` must resolve to a single logical tick (idempotent tick-start write).
- **Empty pipeline**: with zero live deals, the Monitor emits a pipeline-wide observation and the Daily Brief shows a clean empty state.
- **Analyst edits a dismissed draft**: dismissed interventions are read-only; editing requires undismissing first (or is disallowed — implementation chooses one and is consistent).
- **Engineered-issue control deal gets flagged**: the clean Databricks deal surfacing in the Daily Brief is treated as a calibration defect and must fail the relevant scenario in the eval harness.
- **Multi-layer ROFR** and similar stretch scenarios are **not** required for MVP and must not block MVP acceptance.

## Requirements *(mandatory)*

### Product Requirements

*These requirements describe what the system does for Transaction Services analysts and Hiive reviewers. They are written to be readable without engineering background. The Platform Requirements subsection that follows covers reliability, state, observability, and packaging — engineering readers will find the technical precision there; non-technical readers can stop after this subsection.*

**Agents and reasoning**

- **FR-001**: The system MUST implement a Pipeline Monitor that runs on each scheduled tick, screens all live deals using a cheap per-deal pass, and produces a per-tick investigation queue. Deal-selection inputs MUST include time since the deal's last deep review, aging delta since last tick, deadline proximity, and whether new events have been recorded since the last tick.
- **FR-001a**: The Monitor's screening pass MUST assign each live deal an attention score. Deals whose score exceeds a configurable threshold are candidates for investigation. If more than 5 deals exceed the threshold on a tick, the investigation queue MUST be capped at the top 5 by score; remaining candidates MUST defer to the next tick (and MUST be reflected in the next tick's "time since last deep review" input). This keeps per-tick latency and cost bounded and predictable.

  > **Architecture note**: The Pipeline Monitor is a structured screening pipeline, not an agent. Its scoring approach is fixed and its selection logic is deterministic. This is intentional — screening decisions must be cheap, auditable, and predictable rather than emergent.

- **FR-002**: The system MUST implement a Deal Investigator as a genuinely agentic sub-graph whose per-deal execution path is variable, not predetermined. The Investigator MUST complete a parallel dimension evaluation pass, reason about whether those results are sufficient to make a reliable severity decision, optionally execute a bounded enrichment loop (see FR-AGENT-01 through FR-AGENT-05), then score severity and — when severity ∈ {**act**, **escalate**} — draft an intervention. The intervention-drafting step MUST be skipped when severity is **informational** or **watch**. The number of steps executed, the tools called, and the context assembled MUST differ per deal based on what the agent discovers — not based on rules written in advance.
- **FR-003**: The Deal Investigator MUST evaluate every deal it processes against six risk dimensions: (1) stage aging, (2) deadline proximity, (3) communication silence, (4) missing prerequisites, (5) counterparty non-responsiveness, (6) unusual characteristics.
- **FR-AGENT-01**: After completing the parallel dimension evaluation pass, the Deal Investigator MUST reason about whether the results are sufficient to make a reliable severity decision. This sufficiency check is not a predetermined step — the agent decides based on what it found. When results are clear and consistent, the agent proceeds directly to severity scoring. When results are ambiguous, contradictory, or show compounding signals, the agent MAY initiate an enrichment round.

- **FR-AGENT-02**: When the agent determines that results are ambiguous, contradictory, or show compounding signals that may interact in non-obvious ways, it MAY call one or more enrichment tools to fetch additional context before scoring severity. Enrichment MUST NOT be called on every deal — only when the agent judges it necessary based on intermediate findings.

- **FR-AGENT-03**: Three enrichment tools MUST be available to the agent:
  - `fetch_communication_content(deal_id)`: retrieves actual message text from the communication history, not just counts or timestamps. Used when communication silence is flagged but the reason may already be explained in a prior message.
  - `fetch_prior_observations(deal_id)`: retrieves what the agent has previously said about this deal across prior ticks. Used when current signals may be part of a known pattern or a resolved issue.
  - `fetch_issuer_history(issuer_id)`: retrieves outcomes of prior deals with this issuer. Used when issuer non-responsiveness is flagged and historical pattern would change interpretation of current severity.

- **FR-AGENT-04**: Enrichment iterations MUST be bounded at a maximum of 2 rounds per investigation to prevent runaway cost. After 2 enrichment rounds, the agent MUST proceed to severity scoring with whatever context it has assembled.

- **FR-AGENT-05**: The three specific situations that SHOULD trigger enrichment consideration are:
  - **Communication silence flagged high-severity**: the agent checks whether a prior message already explained the silence (vacation, legal hold, awaiting board approval). If the silence is explained, the agent MUST treat this as mitigating evidence and MAY assign lower severity than the raw flag suggests.
  - **Multiple watch-level signals present simultaneously**: the agent reasons about whether compounding signals together warrant act-level severity even if no single dimension crossed the act threshold individually.
  - **Stage aging elevated on a deal with prior breakage history**: the agent checks whether the current pattern matches what caused the prior break (which would elevate severity) or differs from it (which would not).

- **FR-AGENT-06**: The agent's reasoning about whether to enrich, what tool to call, and what the tool returned MUST be written to the audit trail as part of the observation. The analyst MUST be able to see not just the final severity decision but the agent's explicit decision about whether it had sufficient information and what it did about it.

  > **What makes the Investigator agentic**: The Deal Investigator is agentic because it has a variable execution path determined by intermediate findings, not a predetermined structure. Most deals go through a single pass. Ambiguous deals trigger a bounded enrichment loop. The number of steps, the tools called, and the context assembled differ per deal based on what the agent discovers — not based on rules written in advance.

- **FR-004**: Dimension (5) counterparty non-responsiveness MUST be implemented as a deterministic lookup against issuer metadata plus a templated rationale — NOT an LLM call. Dimensions (1)–(4) and (6) MUST be implemented as LLM reasoning calls.
- **FR-005**: Severity assignment MUST produce exactly one of four levels: **informational**, **watch**, **act**, **escalate**. Severity MUST come from a single rubric-based call that receives the full risk picture; it MUST NOT be a hard-coded formula over dimension flags.
- **FR-006**: Severity surfacing MUST obey: informational = audit-trail-only, watch = visible in Per-Deal Detail only (not in Daily Brief), act = Daily Brief with drafted intervention, escalate = top of Daily Brief with intervention and explicit escalation flag.
- **FR-007**: The system MUST support three MVP intervention types — **outbound_nudge**, **internal_escalation**, **brief_entry** — each with a distinct structured form. A fourth type (**status_recommendation**) is stretch and MUST NOT be required for MVP acceptance.
- **FR-008**: Every intervention MUST carry: the deal it references, the reasoning that produced it, references to the specific facts in the deal state (no invented content), and the type-specific fields (e.g., subject + body + recipient type for outbound nudge; short body for internal escalation; one-line summary for brief entry). *Verifiable by: asserting that each named fact in the intervention body — issuer name, stage name, deadline date, counterparty type — matches a value in that deal's seeded or derived state. Fabricated facts (names, dates, or amounts not present in the deal record) are a test failure.*
- **FR-009**: Outbound nudge drafts MUST use a tone appropriate to the recipient type (buyer, seller, issuer counsel, internal legal) and MUST reference the prior thread where one exists and the deadline where relevant. *Tone appropriateness is a qualitative acceptance gate assessed via manual review or Tier 2 LLM-judge rubric (stretch); it is not a deterministic assertion.*
- **FR-010**: Internal escalation drafts MUST be under 50 words — this is a deterministic assertion. They MUST also read like an internal message rather than a formal email — this is a qualitative acceptance gate assessed via manual review; it is not deterministically assertable.

**Human-in-the-loop and safety**

- **FR-011**: The system MUST NOT send external communications. All drafted interventions remain as drafts until an analyst explicitly approves them via the UI.
- **FR-012**: The system MUST NOT mutate deal status in any external system of record. All state changes happen in the local state store only.
- **FR-013**: Intervention status MUST be exactly one of: **pending**, **approved**, **edited**, **dismissed**. Approval, editing, and dismissal MUST require an explicit analyst action recorded with a timestamp.
- **FR-014**: Approved and edited interventions MUST retain both the original draft text and the final text; edits MUST NOT overwrite the draft.
- **FR-LOOP-01 — Communication loop closure on analyst approval**: When an analyst approves or confirms an edit to a drafted intervention, the system MUST immediately record a "comm sent via agent recommendation" event for that deal with the current simulated timestamp. This event references the approved intervention so the audit trail is complete. The event emission and suppression logic (FR-LOOP-02) apply equally to approve and edit-confirm actions — in both cases the analyst has acted on the agent's recommendation regardless of whether the wording was modified. Without this, the agent re-flags communication silence on the next tick for deals the analyst has already acted on, creating a false alarm loop and degrading Daily Brief quality.
- **FR-LOOP-02 — Attention suppression after analyst action**: Following a "comm sent via agent recommendation" event, the Pipeline Monitor MUST reduce that deal's raw attention score by applying a configurable suppression multiplier (default 0.2) for a configurable number of subsequent ticks (default 3). This prevents the same deal from dominating the Daily Brief immediately after the analyst has handled it. The implementation detail of where in the Monitor scoring pipeline the multiplier is applied belongs to plan.md.
- **FR-LOOP-03 — Audit trail completeness**: The "comm sent via agent recommendation" event MUST be distinguishable in the event history from communications received from external parties. This allows the per-deal timeline in the UI to show "outreach sent via agent recommendation" as a distinct event type with its own visual indicator.

### Platform Requirements

*These requirements describe how the system achieves reliability, observability, and correct time-handling. They are written for engineering readers. The properties that matter to analysts — that the system is always-on, auditable, and never loses data — are expressed in the Product Requirements above; this subsection specifies the mechanisms that deliver those properties.*

**State model**

- **FR-015**: The system MUST model a deal state machine with these stages in this order: `bid_accepted`, `docs_pending`, `issuer_notified`, `rofr_pending`, `rofr_cleared`, `signing`, `funding`, `settled`, `broken`. Each stage MUST have an associated expected dwell-time baseline used for aging computations. The nine stages above represent the standard linear lifecycle. Stretch item #7 (ROFR outcome tracking) introduces a branch stage `rofr_exercised` that forks from `rofr_pending` and rejoins at `signing` — it is not a linear successor to `rofr_cleared` and does not alter the ordering of the nine stages listed here. Stretch stages are additive and do not conflict with this requirement.
- **FR-016**: Per-deal persistent state MUST include: current stage, stage entry timestamp, structured blockers, responsible party for the next action, relevant deadlines (specifically the ROFR deadline when applicable), references to the deal's communication history, and risk factors including the first-time-buyer flag and prior-breakage count.
- **FR-017**: The domain state store MUST persist seven entity types: deals, events, agent observations, interventions, issuers, parties, and ticks. The tick entity is load-bearing for FR-024 idempotency — each tick is recorded at start and marked complete on finish, so the Monitor can resume or skip incomplete ticks on restart without re-emitting observations.
- **FR-018**: Agent workflow/checkpointer state MUST be stored in a database separate from the domain state store. Domain rows and agent-framework rows MUST NOT share a database.
- **FR-019**: The events entity MUST be append-only. Stage transitions, documents received, and communications (sent and received) are recorded as events carrying both a simulated-time timestamp (when the event occurred in the deal timeline) and a real-time creation timestamp (when the system recorded it).

**Time and scheduling**

- **FR-020**: All timestamp reads MUST go through a central Clock abstraction. Two implementations MUST exist: a real-time clock and a simulated clock whose current time can be injected and advanced. Direct wall-clock reads anywhere in application code are prohibited.
- **FR-021**: Clock mode MUST be selectable by environment variable or CLI flag at process start.
- **FR-022**: A scheduler MUST drive the monitoring tick in both modes. The tick cadence MUST be configurable with a default of 60 seconds in real-time mode. In simulated mode, ticks MUST be driven by the simulation controller (analyst UI or eval harness), not by wall-clock cadence.
- **FR-023**: The analyst UI MUST expose the current simulated clock time and, in simulated mode, an "Advance by N days" control. Advancing MUST be rejected for N ≤ 0.
- **FR-023a**: Advancing the simulated clock by N days MUST fire exactly N ticks in sequence, with the simulated clock incremented by one day between each tick. Each intermediate tick MUST run the full monitoring loop (Monitor screening → Investigator branch for queued deals → observation/intervention emission) so that per-day state evolution is visible in the audit trail and assertable by the eval harness. The eval harness MUST be able to drive the same per-day tick sequence programmatically.

**Reliability and observability**

- **FR-024**: The monitoring loop MUST be idempotent. Each tick MUST be recorded atomically at tick start with a unique tick correlation identifier. Observations and interventions MUST be deduplicated on write by (tick, deal) pair. Restart after a mid-tick crash MUST complete outstanding investigations without producing duplicate observations or interventions.
- **FR-024a**: When the Deal Investigator produces a still-actionable severity (**act** or **escalate**) for a deal that already has an intervention in **pending** status, the Investigator MUST NOT draft a new intervention. It MUST still emit the observation with full reasoning, and the observation MUST reference the existing open intervention. A new draft MAY be produced on a later tick only after the pending intervention's status transitions to **approved**, **edited**, or **dismissed**.
- **FR-025**: Every LLM call MUST produce schema-validated structured output. On validation failure the system MUST retry exactly once with a corrective reprompt before surfacing the failure as a logged error.
- **FR-026**: Every LLM call MUST be logged with: prompt, response, latency, model identifier, and correlation IDs tying the call to its originating deal and tick.
- **FR-027**: The system MUST emit structured logs with correlation IDs per deal and per tick. Log format MUST be human-readable in development mode and machine-parseable in production mode.
- **FR-028**: A debug view MUST be accessible behind a query parameter that exposes raw structured log events for a given deal or tick.

**Analyst interface (MVP surface)**

- **FR-029**: The Daily Brief screen MUST show the top 5–7 deals requiring attention today, ranked by priority, with per-item: deal ID, issuer name, deal size, severity badge, one-line summary, expandable reasoning, and a drafted intervention preview with Approve-and-Copy, Edit, and Dismiss actions. Only **act** and **escalate** items are candidates for Today's Priorities.
- **FR-029a**: Once the analyst approves, edits, or dismisses a Today's Priorities item, the item MUST remain visible in the current list view with a "handled" state badge reflecting its new status. The list MUST NOT re-rank or drop the item mid-session. The Today's Priorities list MUST recompute only on the next tick (including simulated-clock advance ticks), at which point handled items may leave the top-5–7 as new priorities rise.
- **FR-030**: The Daily Brief MUST include an "All Open Items" secondary tab listing every act/escalate item across all deals with filters by severity, stage, and issuer. This tab is MVP; a standalone Reviewer Queue page is stretch.
- **FR-031**: The Per-Deal Detail screen MUST include: a facts header (issuer, buyer/seller parties, shares × price and derived total size, current stage, stage entry timestamp, key deadlines); an event timeline in chronological simulated-time order with a visual distinction between incoming and outgoing items; an agent observation history with one expandable entry per tick showing dimensions evaluated, severity, full reasoning, and any linked intervention; and an intervention history section listing type, status, and — for approved/edited — the final text.
- **FR-032**: The UI MUST surface system status: last tick time, deals processed this tick, and a link to the most recent eval scorecard.
- **FR-032a**: The Daily Brief MUST refresh automatically in response to new tick completions. In real-time mode, the brief MUST poll the server every 30 seconds and swap in updated content. In simulated mode, automatic polling MUST be disabled; the brief MUST instead re-fetch immediately after the analyst triggers an "Advance N days" action (i.e., after the N sequential ticks complete). Both modes MUST use partial content swaps rather than full page reloads where practical.

**Domain language**

- **FR-033**: All user-visible text — UI labels, email drafts, log messages, state names — MUST use Hiive domain vocabulary (listing, bid, issuer, ROFR, transfer agreement, accreditation, settlement, breakage, stage, aging). Intervention drafts in particular MUST reference deal-specific facts (issuer name, stage, deadline, counterparty type, missing-document name where applicable) rather than generic placeholder language. *Deterministic assertion: intervention body text MUST NOT contain prohibited generic phrases (e.g., "advance this opportunity", "the deal is progressing", "look forward to hearing"). Full domain-voice compliance is a qualitative gate assessed via manual review or Tier 2 LLM-judge rubric (stretch).*

**Seed data**

- **FR-034**: The system MUST seed a pipeline of 30 deals across the 10 issuers: SpaceX, Stripe, Anthropic, Perplexity, Cerebras, Groq, Databricks, Canva, Rippling, Ramp. Deals MUST be distributed across stages per BUILD_PLAN.md Section 6.2 (3 fresh, 5 at issuer-notified, 4 at ROFR-pending with varied remaining windows, 3 at ROFR-cleared/signing, 2 at funding, 5 settled historical, 3 broken historical, plus the 5 engineered-issue deals).
- **FR-035**: The seed data MUST include the five engineered-issue deals: (1) Stripe ROFR-cliff → **escalate**, (2) Anthropic docs-stalled → **act**, (3) Perplexity signing-silence → **act**, (4) Cerebras large-and-first-time → **watch**, (5) Databricks clean control → **informational** only. Scenario #5 is the false-positive test: if the agent flags it, calibration has failed.
- **FR-036**: Issuer metadata MUST include `typical_rofr_window_days`, `response_speed` (fast/normal/slow), `multi_layer_rofr`, and `sector`, engineered to create observable variety.
- **FR-037**: Simulated communications MUST read like real Transaction Services outreach: accurate register, domain-accurate detail, plausible phrasing. Volume target ≈ 75 communication items across the seed. *Voice quality is a qualitative acceptance gate assessed via manual review; it is not a deterministic assertion. The deterministic check is that each communication item references at least one deal-specific fact (issuer name, stage, or deadline value from the seeded deal state).*

**Evaluation harness**

- **FR-038**: A single documented command MUST run 15 Tier 1 structured golden scenarios covering detection (8 — one positive and one negative for each of the four LLM-reasoned dimensions), prioritization (4), and intervention structural quality (3).
- **FR-039**: Each scenario MUST be a structured fixture describing a setup (stage, aging, comm history, deadlines, and any required ROFR deadline offset) and a set of structured assertions covering at minimum: whether the agent flags the deal, expected severity, expected triggered dimensions, whether an intervention is drafted, expected intervention type and recipient type, content-structural assertions (e.g., body contains deadline; body references missing doc by name; internal escalation is under 50 words), and — for prioritization — Daily-Brief rank assertions.
- **FR-040**: The eval runner MUST load each scenario, apply its setup to an isolated state, advance the simulated clock as the scenario requires, invoke the agent, evaluate assertions, and write a timestamped scorecard file. The scorecard MUST include pass/fail per scenario with failure reasons, aggregate pass rate, per-dimension precision and recall, and a severity confusion matrix.

**Packaging**

- **FR-041**: The project MUST expose a single documented entry point for each of: setup, seeding, running, evaluating, demonstrating, and cleanup. The demo command MUST seed the pipeline, start the system, open the browser at the Daily Brief, and fast-forward the simulated clock through the first ticks so the engineered scenarios are visible immediately without additional manual steps.

### Key Entities

- **Deal**: A single secondary-market transaction under management. Has a stage, a stage entry timestamp, optional ROFR deadline, structured blockers, buyer and seller parties, issuer, shares × price, and risk factors (first-time-buyer flag, prior-breakage count). The entity on which every agent decision hangs.
- **Event**: An append-only record of something that happened on a deal — stage transition, document received, communication sent or received, blocker resolved. Carries a simulated-time timestamp and a typed payload.
- **Agent Observation**: One audit-trail record per (tick, deal) pair where the agent recorded anything. Carries a tick correlation identifier, severity (nullable for Monitor-level pipeline observations), dimensions evaluated, and the full structured reasoning object. Optionally linked to an intervention it generated.
- **Intervention**: A drafted action the agent proposes — outbound nudge, internal escalation, brief entry, or (stretch) status recommendation. Carries recipient type where applicable, subject/body, status, and — for approved/edited — a final text.
- **Issuer**: Metadata about a company whose shares are being transacted. Carries name, typical ROFR window, response-speed classification, multi-layer-ROFR flag, sector.
- **Party**: A buyer, seller, counsel, or internal participant. Carries type, display name, and a first-time-on-platform flag.
- **Tick**: A single run of the monitoring loop. Carries a correlation ID used to tie every log event, observation, and LLM call emitted during the run.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001 — One-command demo**: A reviewer can run the submission from a clean clone with a single documented command and reach the Daily Brief in under 3 minutes on a typical developer laptop, with the engineered scenarios visible without any additional manual setup.
- **SC-002 — Detects every designed stall**: On the five engineered-issue deals, the agent produces the expected severity on 100% of them (Stripe escalate, Anthropic act, Perplexity act, Cerebras watch, Databricks informational) within three ticks of simulated time from seed.
- **SC-003 — Daily Brief priority correctness**: When an escalate-severity deal and an act-severity deal exist simultaneously, the escalate item ranks above the act item in Today's Priorities on 100% of demo runs.
- **SC-004 — Reasoning inspectability**: For every observation surfaced in the Daily Brief or Per-Deal Detail, the analyst can reach the full structured reasoning (dimensions evaluated, severity rationale, referenced facts) in one UI interaction (expansion toggle or page navigation) from the surface where the observation appears. No observation is surfaced without reachable reasoning. *Verifiable by: navigating from each surfaced observation to its reasoning display without encountering missing data, loading errors, or additional navigation steps.*
- **SC-005 — Idempotency under restart**: Killing the process mid-tick and restarting produces zero duplicate observations or interventions for the same (tick, deal) pair across 10 consecutive restart trials.
- **SC-006 — Eval harness credibility**: A single documented command runs all 15 Tier 1 scenarios end-to-end and produces a scorecard file in under 5 minutes. The target for submission is ≥ 13/15 scenarios passing; the Databricks clean-control false-positive test is one of the 15 and MUST pass.
- **SC-007 — Human-in-the-loop invariant holds**: Across any demo or eval run, zero external communications are sent and zero system-of-record mutations occur. 100% of interventions remain drafts until an explicit analyst action in the UI changes their status.
- **SC-008 — Domain-language compliance**: No generated email draft or UI label contains the prohibited generic phrases defined in FR-033 (deterministic assertion). Overall domain-voice quality — accurate register, deal-specific grounding, Hiive vocabulary throughout — is a qualitative acceptance gate assessed by reviewer inspection. *Automated assertion covers the prohibited-phrases list; reviewer judgment covers the rest.*
- **SC-009 — Simulation responsiveness**: Advancing the simulated clock by N days (for any N in {1, 3, 7, 14}) causes the Daily Brief to re-render with correctly updated priorities within the time it takes to fire the elapsed ticks (i.e., no stuck caches).
- **SC-010 — Ten-minute reviewer test**: A Hiive reviewer, spending ten minutes with the submission, can (1) run it in one command, (2) articulate the Hiive-specific pain it addresses using the system's own language, (3) watch the agent detect a designed stall and read the reasoning, (4) advance the clock and watch priorities change, (5) open the eval scorecard, (6) trace any agent decision to its signals, and (7) read the 400-word writeup. *This is a qualitative acceptance gate, not an automated assertion. The system MUST make tasks (1)–(7) mechanically achievable in ten minutes — meaning no additional setup steps beyond the documented commands, no broken navigation paths, and no missing documentation that would force a reviewer to read source code to understand what the system does.*

## Assumptions

- **Internal Hiive mechanics are inferred from public context**, not internal access. Specifics of ROFR timing, Transaction-Services email cadence, internal escalation channels, and operational definitions of "stalled" are documented as assumptions in the README and, where load-bearing, in the 400-word writeup (Constitution VIII).
- **External systems are mocked**. Salesforce, Front, Gmail, Slack are represented as local data sources and local message sinks. No live integration is in scope.
- **Single simulated analyst**. No authentication, no multi-tenancy, no user management. The UI assumes one operator.
- **No autonomous external action**. The agent never sends messages or mutates source systems; all external actions are drafts awaiting human approval (Constitution II).
- **No model fine-tuning or training**. All reasoning uses hosted LLMs via the Claude API.
- **Desktop web UI is sufficient**. Mobile and responsive polish are out of scope.
- **No external infrastructure dependencies** beyond Python + SQLite + in-process scheduler — no Docker, Postgres, Redis, or message queues.
- **No retrieval layer**. Per-deal state provides all context for reasoning; there is no vector store or RAG layer.
- **Issuer metadata is engineered**, not scraped. Response-speed and ROFR-window values are chosen to create observable variety in the demo and eval harness.
- **Dwell-time baselines are engineered defaults** derived from plausible Transaction-Services norms; they are documented as assumptions.
- **Stretch items are gated by BUILD_PLAN.md Section 9**: the standalone Reviewer Queue page, the fourth intervention type (status recommendation), scaling to 40 deals / 23 scenarios / 8 engineered scenarios, and Tier 2 LLM-as-judge evaluation are all out of MVP scope and only begin after the MVP slice is complete and polished.
- **Engineered assumptions** (dwell-time baselines, issuer metadata, ROFR-window defaults, synthetic communication voice calibration) are documented in [docs/assumptions.md](../../docs/assumptions.md). This document is created as task T128a and linked from the README.

## Dependencies

- This spec depends on the principles codified in `.specify/memory/constitution.md` — in particular Principles I (MVP-first), II (HITL, non-negotiable), III (domain-accurate language), IV (explainable reasoning), V (reliability patterns), VI (evaluation as product), and VIII (honest about assumptions).
- Scope, sequencing, and quantitative targets are anchored to `BUILD_PLAN.md` Sections 3 (risk dimensions), 4 (severity and interventions), 5 (screens), 6 (data model and seed), 7 (evaluation), 9 (MVP vs. stretch gating), and 12 (success criteria).
- Domain vocabulary and the user profile are anchored to `PROJECT_CONTEXT.md`.
