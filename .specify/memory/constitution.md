<!--
SYNC IMPACT REPORT
==================
Version change: [unratified template] → 1.0.0
Bump rationale: Initial ratification. Template placeholders replaced with concrete principles
  governing the Hiive Deal Pipeline Monitoring Agent take-home project.

Modified principles: N/A (initial ratification).

Added sections:
  - Core Principles (I–X), expanded from 5 to 10 principles per project brief.
  - Project Constraints (regulated-industry prototype, scope/time box).
  - Development Workflow (MVP-first sequencing, stretch gating, submission discipline).
  - Governance.

Removed sections: None.

Templates requiring updates:
  ✅ .specify/memory/constitution.md (this file) — populated.
  ⚠ .specify/templates/plan-template.md — verify "Constitution Check" references align with
     Principles I (MVP-first), II (HITL), IV (explainability), V (reliability), VI (evaluation).
  ⚠ .specify/templates/spec-template.md — ensure spec scope section calls out HITL boundaries
     and domain-vocabulary requirement (Principles II, III).
  ⚠ .specify/templates/tasks-template.md — ensure evaluation-harness and assumption-logging
     tasks are categorized as first-class (Principles VI, VIII).
  ⚠ README.md (not yet created) — must document HITL stance, assumptions list, and run/eval
     targets per Principles II, VII, VIII.

Deferred / follow-up TODOs: None.
-->

# Hiive Deal Pipeline Monitoring Agent Constitution

## Core Principles

### I. Depth Over Breadth, via MVP-First Sequencing (NON-NEGOTIABLE)

One deeply built thing beats three shallow things. The MVP slice defined in `BUILD_PLAN.md`
Section 9.1 MUST be built and polished before any stretch work begins. When choosing the next
task, the answer is always "the MVP slice item that unblocks the most downstream MVP work" —
never "the exciting stretch feature." Stretch items carry the hard-start cut-off hours in
`BUILD_PLAN.md` Section 9.2; if an item has not been started by its cut-off, it is abandoned
for this submission. **No stretch item may be partial-shipped.** A half-built Screen 3 or a
half-built fourth intervention is strictly worse than none.

**Rationale:** A 60-hour time box rewards a coherent, polished slice over a broad, broken one.
The cut-off discipline protects the MVP from stretch creep in the final hours.

### II. Human-in-the-Loop Is Non-Negotiable (NON-NEGOTIABLE)

The agent drafts, flags, suggests, prioritizes, and escalates. The agent MUST NOT:
- send external communications (emails, client messages, issuer notices),
- mutate deal status, stage, or any field in a system of record,
- take any irreversible action,

without explicit human approval captured in the analyst interface. Every intervention the
agent produces is a draft awaiting review. Approval is an affirmative, logged human action —
never a default, never a timeout auto-confirm.

**Rationale:** Hiive is a FINRA-member, SEC-registered broker-dealer. This is a product-level
and regulatory constraint that does not bend, even in a prototype.

### III. Domain-Accurate Language

Every prompt, every drafted email, every state name, every schema field, every log line,
every UI label MUST use Hiive's actual vocabulary: *listing, bid, issuer, ROFR, transfer
agreement, accreditation, settlement, breakage, stage, aging, Transaction Services, secondary,
pre-IPO*. Generic business-speak ("the deal is progressing," "please advance the opportunity")
is a **defect**, not a style choice. When in doubt, re-read the domain primer in
`PROJECT_CONTEXT.md` and prefer the narrower Hiive term.

**Rationale:** Reviewers are domain experts. Language accuracy is the fastest signal of
whether the builder understood the business.

### IV. Reasoning Is Explainable and Inspectable

Every risk flag, every drafted intervention, every priority ranking, every severity
assignment MUST carry the reasoning that produced it, attached to the output in a form the
analyst — and a compliance reviewer — can audit. This means structured rationale fields on
every agent output, citations to the underlying evidence (which email, which aging threshold,
which ROFR clock), and a visible chain from input signal to output recommendation. **Black-box
outputs are not acceptable.**

**Rationale:** Explainability is both a product requirement (analysts must trust and verify)
and a regulated-industry expectation (compliance must reconstruct why an action was taken).

### V. Reliability Patterns Are First-Class

Even as a prototype, the system MUST demonstrate production-shape engineering:
- **Schema-validated outputs** (Pydantic or equivalent) on every LLM call,
- **Bounded LLM output spaces** — enums, constrained fields, length caps — not free-form prose
  where structure is required,
- **Timeouts and retries** with explicit policies on every external call (LLM, data store),
- **Idempotency** on the monitoring loop so restarts do not duplicate interventions or drafts,
- **Structured logs** with correlation IDs tying agent runs to deals and interventions.

The Delivery Voice Agent reliability bar on the builder's resume is the reference standard.

**Rationale:** A prototype that looks production-shaped reads as engineering maturity. A
prototype that crashes on restart reads as a demo. We are building the former.

### VI. Evaluation Is Part of the Product

A seeded golden set of deal scenarios with known-correct agent behavior ships **alongside**
the agent, not after. The Tier 1 deterministic harness (15 scenarios for MVP per
`BUILD_PLAN.md` Section 7.1) runs via `make eval` and produces a scorecard. "Did the agent
catch the stall we designed into the data?" MUST be a testable question from the first
engineered-issue deal onward. Evaluation code, fixtures, and expected outputs live in the
repo and are reviewer-runnable. The ICBC LLM evaluation pipeline on the builder's resume is
the reference.

**Rationale:** An evaluation harness is the proof that the agent does what it claims. Without
it, every behavioral claim is a promise; with it, every claim is a test.

### VII. Reviewer-Readable Code

A senior engineer at Hiive opens this repo and follows it in ten minutes. That requires:
a real README with setup/run/eval/demo instructions; sensible top-level structure; small,
focused modules; types where they clarify intent; comments only where they earn their place
(non-obvious invariants, ROFR timing edge cases, regulatory constraints). **No code-golf, no
speculative abstractions, no cleverness without purpose, no half-finished scaffolding.**
When in doubt, prefer the boring, legible option.

**Rationale:** The submission is evaluated by humans reading code under time pressure. Every
minute spent deciphering cleverness is a minute not spent being impressed.

### VIII. Honest About Assumptions

Any time the build assumes something about how Hiive actually operates — because internal
access is not available — the assumption MUST be documented at the point it is made: in a
code comment, in the README's "Assumptions" section, and (for load-bearing ones) in the
400-word writeup. Examples: ROFR clock mechanics, Transaction Services email cadence,
internal escalation channels, how "stalled" is defined operationally. **Unflagged assumptions
are defects.**

**Rationale:** A good consultant is explicit about the seams between what they know and what
they have inferred. This signals judgment and invites correction rather than hiding gaps.

### IX. Synthetic Data Must Be Convincing

The demo stands or falls on whether the simulated pipeline feels real. Therefore:
- Issuers MUST be real Hiive-listed companies or plausible analogs, not placeholder names.
- Generated emails MUST read like Transaction Services correspondence — appropriate register,
  realistic phrasing, domain-accurate detail — not generic business copy.
- Deadlines, ROFR windows, and aging thresholds MUST reflect realistic mechanics, not
  arbitrary durations.
- Variety is **engineered** (distribution of stages, deal sizes, issues, aging), not random.

The five engineered-issue scenarios in `BUILD_PLAN.md` Section 6.3 are the canonical bar.

**Rationale:** A reviewer who sees a toy data set stops taking the system seriously. A
reviewer who sees a plausible one evaluates the agent on its merits.

### X. Submission-Shaped From Day One

The assignment asks for: a runnable output, a 400-word writeup, a one-paragraph reflection,
and optional coding session transcripts. Every build decision MUST answer the question:
**"does this help or hurt the submission?"** The goal is not the best possible system in
abstract — it is the best possible submission that demonstrates engineering depth in the
available time. Polish the README, the `make demo` path, and the writeup alongside the code,
not after.

**Rationale:** Engineering invisible to the reviewer does not count. Submission shape is a
first-class product concern.

## Project Constraints

**Regulated-industry prototype.** Hiive is a FINRA-member, SEC-registered broker-dealer.
Principle II (HITL) and Principle IV (explainability) are compliance constraints, not product
preferences. The agent's scope is strictly advisory: observe, reason, draft, flag, escalate.

**Time box.** 60 hours total. MVP slice targets hour 55 (per `BUILD_PLAN.md` Section 10.1).
Stretch queue operates under the cut-offs in Section 9.2. Scope valves in Section 9.4 are
contingency, not plan.

**Reference materials.** `PROJECT_CONTEXT.md` (domain primer, problem framing) and
`BUILD_PLAN.md` (scope, sequencing, MVP vs. stretch) are the authoritative scope documents.
When this constitution and those documents disagree, this constitution governs on *principles*
and those documents govern on *scope and sequencing*.

## Development Workflow

**MVP-first sequencing.** No stretch work is started until the MVP slice in `BUILD_PLAN.md`
Section 9.1 is complete and polished. The decision protocol in Section 9.3 (hour-40 and
per-cut-off checks) is followed literally.

**Evaluation alongside implementation.** Each new risk dimension, severity level, or
intervention type ships with at least one golden-set scenario that exercises it (Principle VI).
PRs/commits that add behavior without a corresponding scenario are incomplete.

**Assumption log.** A running list of assumptions lives in `README.md` (or equivalent) and is
updated as assumptions are made (Principle VIII). The 400-word writeup surfaces the
load-bearing ones.

**Domain-language review.** Before merging any prompt, email template, state name, or UI
label, check against Principle III. Generic wording is rewritten in Hiive vocabulary before
shipping.

**Submission checks.** At hours 40, 50, and 55, verify: `make setup`, `make seed`, `make run`,
`make eval`, `make demo`, and `make clean` all work from a clean clone; README is current;
writeup and reflection drafts exist.

## Governance

This constitution supersedes other practices and preferences for this project. All
`/speckit.specify`, `/speckit.plan`, `/speckit.tasks`, and `/speckit.implement` work MUST
operate under these principles.

**Conflict resolution.** When a downstream decision (spec, plan, task, implementation) would
conflict with a principle, the conflict MUST be flagged explicitly in the artifact and
resolved — by adjusting the decision, by narrowing scope, or (rarely) by amending the
constitution — **before** proceeding. Silent compromise is prohibited.

**Amendments.** Amendments require: (1) a written rationale in the amending commit, (2) a
version bump per the policy below, and (3) a Sync Impact Report updating affected templates
and docs. Principle removals or redefinitions require a MAJOR bump and an explicit note in
the writeup if the submission relies on the amended principle.

**Versioning policy (semantic).**
- **MAJOR**: Backward-incompatible principle removal or redefinition.
- **MINOR**: New principle added, or a principle materially expanded.
- **PATCH**: Clarifications, wording fixes, non-semantic refinements.

**Compliance review.** Every agent-produced artifact surfaced to a human reviewer MUST carry
its rationale (Principle IV). Every external action MUST be gated on explicit human approval
(Principle II). Any PR that weakens these gates is rejected.

**Runtime guidance.** Day-to-day build guidance lives in `BUILD_PLAN.md` and `PROJECT_CONTEXT.md`.
This constitution governs *how* decisions are made; those documents govern *what* is built.

**Version**: 1.0.0 | **Ratified**: 2026-04-16 | **Last Amended**: 2026-04-16
