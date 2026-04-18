# Reflection

## Agent design

The most interesting design choice was the enrichment loop inside the Deal Investigator: rather than running all six risk dimensions and immediately scoring severity, the agent's `assess_sufficiency` node can pause, call a targeted tool (comm history, prior observations, issuer history), and loop back — up to two rounds before being forced to a verdict. This is what makes it genuinely agentic rather than a dressed-up pipeline: the number of steps, the tools called, and the context assembled all vary per deal and per tick based on LLM reasoning. The hardest part to get right was the LangGraph compiled-graph closure semantics — the graph captures function bindings at compile time, which meant integration tests had to call node functions directly rather than relying on post-compile monkeypatching. The second-hardest was the clock abstraction: threading a single simulated-time source through every timestamp read in the system (LangGraph state, DAO queries, scheduler ticks, seed data generation) without any `datetime.now()` leaking through — the static analysis test that scans source files for bare `datetime.now()` calls exists precisely because this kind of leak is invisible until a time-dependent test flakes.

## Eval design

The two-tier eval design ended up being the right call. Tier 1 (deterministic YAML assertions) gives fast, zero-cost, fully reproducible signal on detection and prioritization correctness — it runs in CI without any API cost and catches regressions immediately. Tier 2 (G-Eval LLM-as-judge) is gated behind `make eval-deep` because it costs real money and is non-deterministic; running it only when validating intervention quality before shipping is the right tradeoff.

The `ground_truth` block per fixture was a good investment. All 39 fixtures declare exact expected severity and per-dimension triggered/not_triggered labels, enabling genuine precision/recall computation rather than just pass/fail counts. The confusion matrix and P/R table make it immediately visible if the agent is systematically over-triggering a particular dimension or conflating act with escalate.

`expected_tools` is now populated in adversarial comm scenarios (e.g., `adversarial_conflicting_comm` asserts `fetch_communication_content` must be called), so Tool Correctness is no longer always n/a. Fixture-specifying expected tools is viable when the enrichment choice is deterministic from the signal; it should remain absent in ambiguous cases where the agent legitimately has multiple reasonable options.

The decision to use `deepeval_adapter.py` wrapping `ChatOpenRouter` as a `DeepEvalBaseLLM` was correct — it keeps the judge model consistent with the rest of the project and avoids maintaining a separate OpenAI API key. Using `deepeval`'s env-var approach (OPENAI_BASE_URL trick) would have been simpler to set up but brittle across deepeval version upgrades.

## Eval harness lessons

Running the first eval revealed five harness-level bugs — distinct from agent behavior bugs — that had to be fixed before any meaningful prompt tuning could happen:

1. **Metric directionality:** `HallucinationMetric` scores 0.0 = no hallucination (best). The priority-failures section treated it as higher-is-better, flagging all 39 perfect scores as failures. Any metric with inverted directionality needs an explicit exclusion list in the scorecard generator.

2. **Dimension attribution gap:** `_format_actual_output()` built the triggered-dimensions list from assertion results only — scenarios without explicit dimension assertions showed "dimensions triggered: none" to the judge even when the agent triggered several. The judge scored these at 0.5 (severity right, no dims visible). Lesson: the actual-output formatter must be driven by agent state, not assertion pass/fail.

3. **Rubric scope:** The `task_completion` rubric required evidence that the Monitor's attention-score step had occurred — an internal step invisible at the Investigator output level. The judge always docked for this. Rubric criteria must be scoped to observable outputs, not internal pipeline steps.

4. **Factual grounding scope:** Grounding ran on ALL intervention types. Internal escalation and brief_entry drafts legitimately omit shares/price (they're internal communications). Only outbound nudges should be checked. The metric was 0/29 (0%) before the filter was added.

5. **Suppression test setup:** `edge_suppression_active` required prior completed ticks for the suppression query to function. In each scenario's isolated DB there are no prior ticks → suppression never fires. The fixture needed explicit `prior_ticks` entries in its setup block.

The lesson: the eval harness is code with its own failure modes, especially around metric directionality and output formatting for the LLM judge. Debugging the harness before reading the numbers is worth doing — a 82% pass rate with five harness bugs looks like a 87% pass rate once they're fixed.

## UX polish: moving friction to zero

Two late-cycle enhancements sharpened how the pipeline view feels without changing what it does. First, the `/pipeline` filter stack was moved entirely client-side. The route still accepts `tier`/`stage`/`issuer`/`responsible`/`sort` query params for deep-linking and no-JS fallback, but `pipeline.py` no longer uses them to filter the rendered row set — every live deal ships to the DOM, and a small `window.PF` controller toggles row visibility against `data-*` attributes. Analysts now retriage 50+ deals with keystroke-level latency instead of a round-trip per filter change. Book-wide `counts_by_tier` in the header is computed before any filter, so the triage context never collapses to match the active slice.

Second, the deal row → detail navigation uses the cross-document View Transitions API. The clicked row's severity badge and deal ID morph into the detail-page header using shared `view-transition-name` values (`sev-{deal_id}`, `dealid-{deal_id}`) declared in `_macros.html`, `pipeline.html`, and `deal_detail.html`. The gotcha worth recording: `@view-transition` and the associated `::view-transition-*` pseudo-elements must sit at the top level of `input.css`, not inside `@layer base {}` — Tailwind silently strips them from the layer. Firefox has no cross-document support, so it gets a plain navigation without the morph, which is the correct degradation for this feature.

Both changes are invisible when the interface works and only register as "this feels fast" or "that transition felt right." Linear-style density depends on micro-latency and spatial continuity at least as much as typography and color — they're not decoration; they're how a dense tool earns the analyst's trust on every interaction.
