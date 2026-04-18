# Deal Pipeline Monitoring Agent — Submission Writeup

## What it is and the problem it solves

Hiive's Transaction Services team manages 40–60 live deals simultaneously across Salesforce, email, and Slack. Each deal passes through eight stages — from bid acceptance through ROFR clearance to signing and funding — with strict deadlines, counterparty-specific quirks, and multiple failure modes. The analyst's daily challenge is triage: which deals need attention right now, what specifically needs to happen, and how do you communicate it? Today that judgment is applied manually across dozens of spreadsheets and email threads.

This agent answers that question automatically. It runs on every scheduled tick, screens all live deals cheaply using Claude Haiku, investigates the top priority deals using Claude Sonnet, and produces a ranked daily brief with drafted interventions ready for analyst review and approval.

## How it works

Two genuine LangGraph agents collaborate on each tick:

**Pipeline Monitor** (supervisor): Screens every live deal with Claude Haiku to assign an attention score. Suppresses deals that received a recently-approved agent recommendation (avoiding re-nudge). Selects the top five deals above a 0.6 threshold for deep investigation.

**Deal Investigator** (per-deal sub-graph): An agentic graph with a real reasoning loop. Evaluates six risk dimensions — stage aging, ROFR deadline proximity, communication silence, missing prerequisites, counterparty non-responsiveness (deterministic), and unusual characteristics. If the initial signals are ambiguous, the graph loops back to enrich context (fetching communication history, prior observations, or issuer history) before scoring severity. Severity determines intervention type: outbound nudge for act-level, internal escalation for escalate-level.

Every observation, risk flag, severity decision, and drafted intervention is written to the audit trail with structured reasoning. Nothing is sent externally — all drafts surface as pending interventions for analyst review.

The system uses a clock abstraction throughout, enabling true simulated-time mode for the demo and evaluation harness. No `datetime.now()` calls exist in application code.

## Evaluation

The harness runs 39 scenarios across an isolated in-memory database per test, organized into six categories: detection (verifying the agent flags the right risk dimensions for specific signal profiles), prioritization (verifying severity ordering), intervention quality (verifying draft content against domain accuracy requirements), adversarial calibration (conflicting signals, legal holds, communication that appears silent but isn't), edge cases (suppression, enrichment cap, unusual deal structures), and regression fixtures (preventing re-introduction of fixed bugs). Each scenario is a YAML fixture describing a seeded deal situation and a set of deterministic assertions.

The harness has two tiers: Tier 1 runs deterministic assertions (severity, triggered dimensions, enrichment tool calls, intervention content) at near-zero cost. Tier 2 (`make eval-deep`) runs an LLM-as-judge pass using deepeval G-Eval metrics (task completion, tool correctness, argument correctness, hallucination, answer correctness) via the same OpenRouter gateway.

Current Tier 1 result: **35/39 (89%)** across three runs. Four scenarios remain open: two enrichment-trigger failures (`adversarial_conflicting_comm`, `edge_enrichment_cap`), one severity boundary case (`detection_enrichment_issuer_breakage`), and one unusual-characteristics scoring miss (`edge_first_time_large_deal`).

## One-paragraph reflection

The most interesting tension in this build was between the evaluation harness and the agent's genuine reasoning. A purely deterministic agent would pass every scenario trivially — but the value of LLM reasoning is handling the edge cases that rigid rules miss. The right design: deterministic assertions on outputs (severity, intervention type, body content), not on intermediate reasoning steps. The agentic enrichment loop was the riskiest architectural choice — two rounds of unbounded LLM calls could have blown the cost budget or introduced non-determinism that broke tests. Bounding it at two rounds and testing for enrichment presence (not exact content) gave both the demo value and the testability. Running the Tier 2 LLM-as-judge pass surfaced a lesson that applies to the harness itself: eval code has bugs just like agent code. The first eval run had five harness-level defects — a metric with inverted directionality (hallucination 0.0 = perfect, treated as failure), dimension attribution data not forwarded to the judge, rubric criteria referencing internal steps invisible at the output level, grounding checks running on wrong intervention types, and a suppression test that needed tick history to function. Fixing those before tuning prompts was essential. Two scenarios remain failing (`det_04_communication_silence` and `edge_enrichment_cap`) — the enrichment cap case reflects a genuine tension between the sufficiency prompt's "proceed when verdict is unchangeable" shortcut and the test's intention to exercise the enrichment path even for obviously-escalate signals.
