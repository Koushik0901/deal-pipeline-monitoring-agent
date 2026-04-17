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

The harness runs 17 golden scenarios against an isolated in-memory database per test: 10 detection scenarios (verifying the agent flags the right risk dimensions), 4 prioritization scenarios (verifying severity ordering), and 3 intervention quality checks (verifying draft content against domain accuracy requirements including issuer name, deadline date, and stage context). Each scenario is a YAML fixture describing a seeded deal situation and a set of deterministic assertions.

## One-paragraph reflection

The most interesting tension in this build was between the evaluation harness and the agent's genuine reasoning. A purely deterministic agent would pass every scenario trivially — but the value of LLM reasoning is handling the edge cases that rigid rules miss. The right design: deterministic assertions on outputs (severity, intervention type, body content), not on intermediate reasoning steps. The agentic enrichment loop was the riskiest architectural choice — two rounds of unbounded LLM calls could have blown the cost budget or introduced non-determinism that broke tests. Bounding it at two rounds and testing for enrichment presence (not exact content) gave both the demo value and the testability. If I had another 20 hours, I would expand the evaluation set to cover more ROFR edge cases and add a Tier 2 LLM-as-judge pass on drafted intervention quality — the current intervention quality tests are structural (field presence and content keywords) rather than qualitative (tone, urgency calibration).
