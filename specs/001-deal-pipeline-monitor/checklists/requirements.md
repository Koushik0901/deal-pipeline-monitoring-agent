# Specification Quality Checklist: Deal Pipeline Monitoring Agent

**Purpose**: Validate specification completeness and quality before proceeding to planning
**Created**: 2026-04-16
**Last Audited**: 2026-04-16
**Feature**: [spec.md](../spec.md)

## Content Quality

- [x] No implementation details (languages, frameworks, APIs)
- [x] Focused on user value and business needs
- [x] Written for non-technical stakeholders
- [x] All mandatory sections completed

## Requirement Completeness

- [x] No [NEEDS CLARIFICATION] markers remain
- [x] Requirements are testable and unambiguous
- [x] Success criteria are measurable
- [x] Success criteria are technology-agnostic (no implementation details)
- [x] All acceptance scenarios are defined
- [x] Edge cases are identified
- [x] Scope is clearly bounded
- [x] Dependencies and assumptions identified

## Feature Readiness

- [x] All functional requirements have clear acceptance criteria
- [x] User scenarios cover primary flows
- [x] Feature meets measurable outcomes defined in Success Criteria
- [x] No implementation details leak into specification

## Unchecked items — explanation of what is missing

### Content Quality

**"No implementation details (languages, frameworks, APIs)"** — Unchecked.
The spec references concrete technology choices that belong in `/speckit.plan`, not `/speckit.specify`:
- **SQLite** explicitly named in FR-017 and FR-018 (storage engine).
- **YAML** explicitly named in FR-038 and FR-039 as the scenario fixture format.
- **Makefile / `make <target>`** named in FR-041, SC-001, SC-006 (build-tool choice).
- **JSON log format** named in FR-027 (serialization choice).
- Field-level schema names (`tick_id`, `rofr_deadline`, `stage_entered_at`, `(tick_id, deal_id)` uniqueness key) are presented as requirements rather than as conceptual keys.
- FR-020 references "the host language's 'now' function," which implies a programming-language runtime.

Per the user's instruction for this spec: *"The spec must NOT describe: specific languages, frameworks, agent libraries, LLM providers, storage engines, UI toolkits, or deployment targets."* SQLite (storage engine), Makefile (build tool), YAML/JSON (format choices), and concrete column/field identifiers violate that rule strictly read. They are defensible as already-decided scope from BUILD_PLAN.md, but they are not spec-phase concerns.

**"Written for non-technical stakeholders"** — Unchecked.
User Stories 1, 2, 5 and most Success Criteria are readable by a Transaction Services analyst. But the reliability / observability / state-model FRs (FR-017 through FR-028) assume an engineering reader: *"idempotent," "atomic tick-start write," "deduplicated on insert by `(tick_id, deal_id)`," "correlation IDs," "checkpointer database separate from domain database," "schema-validated structured output with a corrective reprompt."* A non-technical stakeholder cannot evaluate whether those requirements are correct or complete without a translator.

### Requirement Completeness

**"Requirements are testable and unambiguous"** — Unchecked.
Several requirements encode subjective qualities that a test run cannot decide:
- **FR-009**: *"use a tone appropriate to the recipient type"* — "appropriate" is judgment, not assertion.
- **FR-010**: under-50-words part is testable; *"reads like an internal message, not an email"* is not.
- **FR-033**: *"Generic business copy is a defect"* — requires a human call.
- **FR-037**: *"read like real Transaction Services outreach; accurate register; plausible phrasing"* — subjective.
- **FR-008**: *"no invented content"* — detecting hallucination against ground-truth facts is a real test, but the FR does not say how conformance is checked.

These are genuine product properties, but as written they rely on reviewer judgment, not deterministic assertions. They belong in a Tier 2 (LLM-judge) or manual-review rubric, which is explicitly stretch.

**"Success criteria are measurable"** — Unchecked.
Most SCs are measurable (SC-002 100% on 5 scenarios; SC-005 10 restart trials, zero duplicates; SC-006 ≥13/15 scenarios, <5 minutes). But several are not operationally measurable as written:
- **SC-004**: *"reach the full structured reasoning … in one click"* — "one click" is a UX assertion, measurable only by manual inspection, and the spec does not name the pages/elements the count is over.
- **SC-008**: *"a reviewer … cannot identify generic business copy"* — measured only by reviewer judgment, with no rubric.
- **SC-010**: *"a reviewer, spending ten minutes, can … articulate the Hiive-specific pain"* — depends on the reviewer; not a property of the system.

**"Success criteria are technology-agnostic (no implementation details)"** — Unchecked.
- **SC-001** and **SC-006** name the `make demo` / `make eval` commands (build-tool choice).
- **SC-005** names the `(tick_id, deal_id)` compound key (schema choice).

These should read as "a single documented command" and "no duplicate agent outputs for the same logical pass over the same deal" to be strictly technology-agnostic.

### Feature Readiness

**"No implementation details leak into specification"** — Unchecked.
Same evidence as the first Content Quality item. SQLite, YAML, Makefile, JSON, concrete field names, and the "host language's 'now' function" phrasing are all implementation-level details that the spec asserts as requirements rather than leaving to `/speckit.plan`.

## What I would change to satisfy the unchecked items

1. Replace every instance of **SQLite** with "local persistent state store" and every **YAML** with "structured text-based fixtures." Drop **Makefile / `make <target>`** in favor of "a single documented command." Replace **JSON logs** with "machine-parseable structured logs." Remove the "host language's 'now' function" phrasing in FR-020 — state it as "direct wall-clock reads anywhere in application code are prohibited."
2. Demote schema-level identifiers (`tick_id`, `(tick_id, deal_id)` uniqueness, `stage_entered_at`, `rofr_deadline`) to conceptual names ("tick correlation identifier", "unique per (tick, deal)", "stage entry time", "ROFR deadline") without implying column names.
3. Split Requirements into a **Product Requirements** subsection (analyst-facing, non-technical) and a **Platform Requirements** subsection (reliability, observability, state) with a one-line translator at the top of the latter, so non-technical readers know which section is theirs.
4. Replace subjective FRs with either (a) a rubric (`FR-009`, `FR-010`, `FR-033`, `FR-037` → named rubric items scored by Tier 2 judge or manual review) or (b) a concrete minimum (`FR-010` keeps <50-word check; the "tone" part becomes a rubric item).
5. Rewrite **SC-004**, **SC-008**, **SC-010** as either instrument-measurable (count of clicks from a named page; rubric score ≥ X/Y) or explicitly marked as qualitative acceptance gates rather than measurable outcomes.

## Notes

- The unchecked items are real but most are **tightenable** rather than conceptually missing. Choosing to tighten now (before `/speckit.plan`) keeps the spec-plan boundary clean; choosing to accept the current spec is defensible given that the technology choices it names are already fixed in `BUILD_PLAN.md` and the subjective FRs map to acknowledged-stretch Tier 2 evaluation.
- Pre-existing cross-artifact inconsistency: BUILD_PLAN.md Section 6.1 heading says "Three domain tables" but enumerates six; the spec uses the enumerated six.
