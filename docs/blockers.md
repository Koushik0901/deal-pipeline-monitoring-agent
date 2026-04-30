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

### `ChatOpenRouter` API key kwarg silently ignored (`'User not found.'` floods)
**What blocked:** Every LLM call was failing with `'User not found.'` from OpenRouter — a
misleading error string that suggested an account/auth-tier issue. The actual cause: the LLM
client at `llm/client.py:_get_llm` was passing `openrouter_api_key=settings.openrouter_api_key`
to the `ChatOpenRouter` constructor, but `langchain_openrouter` ≥ 0.2 renamed the constructor
kwarg to `api_key` (a `SecretStr`). The Pydantic-based class silently dropped the unknown kwarg
(`extra="ignore"`) and fell back to its default `<factory>` for `api_key`, which reads
`os.environ["OPENROUTER_API_KEY"]`. Pydantic-Settings populates only the `Settings` model — NOT
`os.environ` — so the factory found nothing and every request went out unauthenticated.

**Resolution:** Renamed the kwarg at `llm/client.py:_get_llm` from `openrouter_api_key=` to
`api_key=`. Verified end-to-end: a real SLM screening call now returns `'pong'` against the
configured model. Eval and tick paths recovered immediately.

**Status:** Resolved. Future-proofing note: if `langchain_openrouter` upgrades again, run
`uv run python -c "from langchain_openrouter import ChatOpenRouter; import inspect; print(inspect.signature(ChatOpenRouter))"` to verify the kwarg name before debugging an opaque
401-style error.

---

### Alpine `x-data` JSON-attribute quoting + getter syntax broke "All open" tab
**What blocked:** The `_all_open_list.html` tab silently rendered zero rows even though the tab
counter showed the right number. Two stacked bugs: (1) Alpine's expression parser does NOT support
object-literal getter syntax (`get noResults() { ... }`) — it threw `SyntaxError: Unexpected
token '}'` and broke every dependent `x-show`/`:class` binding; (2) Jinja's `|tojson` emits JSON
with `"` quotes that prematurely terminated the surrounding `x-data="..."` attribute, so the rest
of the JS bled out as malformed sibling attributes.

**Resolution:** Replaced the getter with a regular method (`noResults() { ... }`) and switched
the outer attribute delimiter to single-quotes so JSON's `"` is safe inside; switched JS string
literals to `"` to avoid colliding with the new attribute delimiter. Console errors went 31 → 0
on `/brief`.

**Status:** Resolved. Pattern documented in CLAUDE.md gotchas.

---

### DAO `get_observations` ordering surfaced stale severity rationale
**What blocked:** After running multiple ticks (each ticks the simulated clock by one whole day,
so multiple ticks share the same `observed_at` timestamp), the deal-detail page kept showing the
OLDEST tick's severity rationale instead of the latest. The template's
`| sort(attribute='observed_at', reverse=True) | first` is a stable Jinja sort — when keys tie,
it preserves DAO insertion order, which had been ASC-by-rowid. So `| first` after reverse-sorting
returned the oldest insertion in a tied bucket.

**Resolution:** Changed `dao.get_observations()` from `ORDER BY observed_at` to
`ORDER BY observed_at ASC, rowid DESC`. Within a tied `observed_at` bucket, the newest insertion
now comes first; combined with the template's stable reverse-sort, `| first` correctly returns
the latest tick's observation.

**Status:** Resolved. CLAUDE.md notes the contract (don't change ordering without updating the
template).

---

### Watch-tier deals never reached the LLM-derived brief
**What blocked:** The pipeline page showed N watch-tier deals (deterministic from
`pipeline_health.py`), but the brief showed zero — the analyst's natural mental model expects
the brief to be a complete picture of the day, including watch-tier monitoring. Root cause is
data-shape: the SLM screening rubric scores "mild concern" deals at 0.21–0.50 (midpoint ~0.355),
the `attention_threshold` filter excludes them at 0.45, and even when threshold is dropped to
0.20 the cap of 12 investigations per tick is saturated by escalate/act candidates. The LLM
investigator simply never sees most watch-tier deals, and even when it does, the seed data's
signals are strong enough that classifications come back as act/escalate.

**Resolution (Path C — bridge):** `/brief` and `/api/brief-stats` now compute a `watch_list` by
calling `pipeline_health.compute_signals` + `compute_health` on every live deal, filtering to
`tier == 'watch' AND deal_id NOT IN open_iv_deal_ids` (no duplication with the intervention path).
Rendered as a collapsed `<details>` panel at the BOTTOM of the brief's main column (visual
hierarchy: act → monitor). Sidebar `WATCH` count sums LLM-classified + deterministic.

**Status:** Resolved as a UI bridge. The underlying tension between LLM-budgeted investigation
and book-wide coverage remains — but the brief now shows a complete picture without forcing the
analyst to switch to the pipeline tab.

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
