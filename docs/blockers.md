# Development Blockers & Known Limitations

Blockers encountered or deferred during development. Each entry notes what was
blocked, what decision was made, and the current status.

---

## Resolved blockers

### LangGraph compiled-graph closure semantics
**What blocked:** Integration tests that tried to monkeypatch node functions
after graph compilation found that the graph had already captured function
references at compile time. Post-compile patches had no effect on graph
execution.

**Resolution:** Integration tests invoke node functions directly rather than
routing through the compiled graph. The runner resets the singleton via
`_investigator_graph = None` and `_monitor_graph = None` between eval scenarios,
forcing a fresh compile that picks up the new temp-DB environment variables.

**Status:** Resolved. Reset pattern works reliably.

---

### Clock leakage via bare `datetime.now()` calls
**What blocked:** Simulated-clock eval scenarios produced non-deterministic
timestamps when any code used `datetime.now()` instead of `clk.now()`, causing
date-based assertions (e.g., ROFR deadline references in intervention bodies)
to fail.

**Resolution:** A static analysis test scans all `.py` source files for bare
`datetime.now()` and `datetime.utcnow()` calls and fails if any are found.
All timestamp reads go through `hiive_monitor.clock.now()`.

**Status:** Resolved. Static analysis test enforces this at CI time.

---

### LangChain-OpenRouter structured output (`with_structured_output`)
**What blocked:** Early versions of `langchain-openrouter` did not reliably
support `method="json_schema"` with `strict=True` for all models on OpenRouter.
Some models returned unstructured text, causing Pydantic validation errors on
every call.

**Resolution:** The LLM client (`llm/client.py`) implements a corrective
reprompt retry on the first parse failure — it appends the validation error to
the conversation and retries once. This handles transient model misbehaviour.
Model selection also matters: models that reliably support structured output are
preferred.

**Status:** Resolved. Corrective reprompt handles the common case.

---

### APScheduler thread safety with SQLite connections
**What blocked:** APScheduler runs ticks on a background thread. SQLite
connections opened on the main thread cannot be used from APScheduler's thread
(`check_same_thread` default is True).

**Resolution:** The SqliteSaver checkpointer connection is opened with
`check_same_thread=False`. Domain DB connections are created per-request via
`get_domain_conn()` rather than held as singletons.

**Status:** Resolved.

---

### `ChatOpenRouter` + `extra_body` incompatibility
**What blocked:** Passing `extra_body={"usage": {"include": True}}` to the `ChatOpenRouter`
constructor caused all LLM calls to fail with `TypeError: Chat.send() got an unexpected keyword
argument 'extra_body'`. The `langchain-openrouter` library places `extra_body` in `model_kwargs`,
which is forwarded verbatim to the underlying provider SDK's `Chat.send()` — a call signature that
no provider (Anthropic, Google, or otherwise) accepts. Both Anthropic Claude and Google Gemma
calls were affected, breaking the entire eval run.

**Resolution:** Removed `extra_body` entirely from `llm/client.py::_get_llm()`. Token counts
are available via `usage_metadata` in the LangChain response object without needing the flag.

**Status:** Resolved.

---

### `max_length` constraints on internal LLM audit fields
**What blocked:** Pydantic `ValidationError` raised at runtime when LLM outputs exceeded
character limits on internal audit fields: `RiskSignal.evidence` (400 chars),
`SeverityDecision.reasoning` (1500), `SufficiencyDecision.rationale` (300), `AttentionScore.reason`
(240). Chain-of-thought prompting causes models to emit detailed reasoning that routinely exceeds
these limits — the very CoT that improves accuracy also blows past the character caps.

**Resolution:** Removed `max_length` from all four internal audit fields in `models/risk.py`.
These are internal reasoning fields consumed only by the evaluation harness and audit trail;
there is no user-facing display constraint that requires a character limit. External-facing
intervention body fields retain their limits.

**Status:** Resolved.

---

## Open limitations

### Tool Correctness metric — partially resolved
**What:** The `expected_tools` field was `[]` in all initial fixtures; Tier 2
Tool Correctness reported n/a. Now populated in adversarial/enrichment scenarios
where the tool choice is deterministic from the signal.

**Current state:** `expected_tools` is set in `adversarial_conflicting_comm`,
`detection_enrichment_issuer_breakage`, and `edge_enrichment_cap`. Tier 2 Tool
Correctness mean is 0.92 (36/39 pass rate) — 3 failures are the same scenarios
where the agent skips mandatory enrichment.

**Remaining gap:** Enrichment tool choice is emergent in ambiguous scenarios;
`expected_tools` should remain absent there to avoid over-specifying agent
strategy. Only add it where the correct tool is unambiguous from the signal
profile (e.g., comm-silence → `fetch_communication_content`).

---

### Tier 2 G-Eval scores are not calibrated
**What:** deepeval G-Eval scores (0–1) represent the judge model's rubric
interpretation. The 0.7 passing threshold is a starting point, not derived
from human annotation.

**To fix:** Sample 20–30 interventions, have a TS analyst rate them on the 4
rubric criteria, and use the distribution to set thresholds per metric.

---

### No real-time data integration
**What:** All deal, party, issuer, and communication data is seeded from
synthetic `seed_data.py` or eval fixtures. There is no live Hiive data
pipeline.

**To fix for production:** Replace `seed_data.py` with a connector to Hiive's
internal deal database. The DAO layer (`db/dao.py`) provides the abstraction
point — swap the seed data source without changing agent logic.

---

### Analyst send action is UI-only (no email dispatch)
**What:** The "Send" button on the per-deal detail view marks the intervention
as `analyst_approved` in the DB but does not actually send any email or
notification.

**Why deferred:** Per spec constitution: "Agent suggests; analyst acts. No
autonomous sends." The send infrastructure (SMTP, Hiive internal messaging)
is a production integration concern outside MVP scope.

---

### Single-deal tick only (no multi-deal fan-out in evals)
**What:** Each eval scenario seeds exactly one deal and runs one tick. The
Monitor's fan-out logic (screening → attention scoring → top-K queue) is
tested end-to-end only with a single deal, which always wins the queue.

**Why deferred:** Multi-deal prioritization scenarios (`pri_01`–`pri_04`)
test the ordering logic, but all seeded-deal scenarios still use a degenerate
queue of size 1.

**To fix:** Extend the fixture schema to support `setup.deals` (multiple deals
in one tick) and update the runner's seeder to handle the list form. The schema
contract already defines this — only the runner seeder and assertion evaluator
need updating.

---

### Langfuse requires Docker for dashboard
**What:** The open-source Langfuse dashboard (`docker-compose.langfuse.yml`)
requires Docker. Without it, Langfuse emission is skipped gracefully but
results are only visible in the markdown scorecard.

**Alternative:** eval_results scorecard files provide the same information in
static form. Langfuse adds run-over-run trend lines and per-metric drill-down,
which are only valuable once there are multiple eval runs to compare.
