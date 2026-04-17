# Phase 0 Research: Deal Pipeline Monitoring Agent

**Purpose**: Resolve stack-specific unknowns before committing to the architecture in plan.md. Focused scope: only the three areas flagged (LangGraph checkpointer, Claude structured output, HTMX server-rendered partials). No research on tech we aren't using.

## Research areas

### 1. LangGraph checkpointer API shape (with SQLite backend)

**Decision**: Use `langgraph-checkpoint-sqlite` (the `SqliteSaver` class from `langgraph.checkpoint.sqlite`) with a dedicated SQLite file separate from `domain.db`. Instantiate once per process and share across graph compilations. Use `thread_id = tick_id` per tick-run so each tick has its own checkpoint thread, and enable `checkpoint_ns = deal_id` for per-deal scoping within the Investigator sub-graph.

**Rationale**:
- SQLite saver is an officially supported first-party backend — no custom implementation risk.
- Thread-per-tick isolates resume behavior: a crashed tick's partially-completed Investigator runs can resume from the last checkpointed node without re-running completed sibling investigations on other deals.
- `checkpoint_ns` namespacing gives us per-deal sub-graph state without fighting the top-level Monitor state.
- A single `.sqlite` file is trivially reset (`rm agent_checkpoints.db`) and matches the "no external infra" constraint.

**Alternatives considered**:
- **`MemorySaver`**: Rejected — loses state on crash; violates FR-024 idempotency across restarts.
- **Postgres checkpointer**: Rejected — adds an infra dependency that the constitution explicitly excludes.
- **Home-rolled checkpointer over `domain.db`**: Rejected — conflates agent-framework state with business state (Constitution V), and re-implements what LangGraph gives us free.

**Implementation notes**:
- Open the saver with `check_same_thread=False` because APScheduler runs jobs on a different thread than the FastAPI request thread.
- Wrap `compile(checkpointer=saver)` once at app startup. Graphs are compiled lazily but cached.
- When the Monitor fans out to the Investigator, pass `config={"configurable": {"thread_id": tick_id, "checkpoint_ns": deal_id}}` so each Investigator run gets its own isolated thread namespace.

**Risk (BUILD_PLAN §11 #2)**: Checkpointer × simulated-clock interaction is the least-validated assumption. Mitigation: integration test `test_monitor_loop.py::test_crash_restart_in_simulated_mode` to be written in hours 7–12 alongside the scheduler; if it fails, fall back to "restart-from-last-DB-persisted-observation" (re-run Investigators for deals without a completed observation row in the open tick).

---

### 2. Claude API structured-output features

**Decision**: Use the Anthropic SDK's tool-use JSON schema approach for structured output (define a tool with an `input_schema` derived from each Pydantic model, force the model to call that tool via `tool_choice={"type": "tool", "name": "..."}`, parse the tool-use block back into the Pydantic model). On parse failure, send one corrective reprompt that includes the validation error and the original response, requesting a corrected tool call. On a second failure, persist an error observation and move on.

**Rationale**:
- Tool-use forced mode is the Anthropic-recommended pattern for guaranteed JSON output against a schema; it is more reliable than asking for "JSON in your response."
- Pydantic v2's `model_json_schema()` produces a JSON Schema compatible with Anthropic's `input_schema` expectations (subset: `object`, properties with `type`, `enum`, `description`, `maxLength`, required fields).
- Single corrective reprompt per FR-025 matches the published best-practice guidance and bounds cost.

**Alternatives considered**:
- **Free-form JSON with regex/best-effort parsing**: Rejected — brittle under model variation; the constitution requires schema validation.
- **Multiple corrective reprompts**: Rejected — unbounded cost and latency; FR-025 caps at one retry.
- **Constrained decoding via a third-party library**: Rejected — adds dependency; the SDK's tool-use already gives us what we need.

**Implementation notes**:
- `llm/client.py` exposes `call_structured(prompt: str, output_model: type[BaseModel], model: Literal["sonnet", "haiku"], tick_id: str, deal_id: str | None, call_name: str, timeout: float = 30.0) -> BaseModel`.
- Idempotency cache: keyed on `(tick_id, deal_id, call_name)` with the response cached in-memory for the duration of the tick. On restart within the same `tick_id`, the LangGraph checkpointer resumes past the cache-hit nodes anyway — the cache is belt-and-braces.
- Retry policy: exponential backoff on `AnthropicError` (except `BadRequestError`) with max 3 attempts per call; separate from the corrective-reprompt retry.
- Latency logging: emit `llm.call.completed` with `model`, `call_name`, `latency_ms`, `input_tokens`, `output_tokens`, `tick_id`, `deal_id`, `attempt`.

**Model selection rules (applied at call site)**:
- `screening.py` → `claude-haiku-4-5-20251001` (per system prompt: Haiku 4.5).
- All other prompts (risk dimensions, severity, interventions, brief, judge) → latest Sonnet (`claude-sonnet-4-6`).

---

### 3. HTMX best practices for server-rendered HTML + partial updates

**Decision**: Return full HTML pages from GET routes; return HTML fragments (Jinja partials) from POST routes and polling endpoints. Use `hx-target` + `hx-swap="outerHTML"` for list-item replacements on approve/edit/dismiss. Use `hx-trigger="every 30s"` on the Daily Brief container in real-time mode, omitted in simulated mode. Use `hx-push-url="true"` selectively on navigation links to preserve back-button behavior.

**Rationale**:
- Full-page on first load + fragment swaps for interactions is the canonical HTMX pattern and matches how FastAPI + Jinja compose naturally (two route decorators, one template file with `{% if request.headers.get("HX-Request") %}` guards or separate partial templates).
- `outerHTML` swap on the list item lets us update in-place for FR-029a (handled badge swap without list reflow).
- Polling every 30 s (FR-032a) is well within HTMX's built-in `hx-trigger="every Ns"` capability — no custom JS needed.

**Alternatives considered**:
- **Server-sent events / WebSocket push**: Rejected in Q5 clarification — adds complexity without clear value for a single-user prototype.
- **Turbo (Hotwire)**: Rejected — requires Ruby-ecosystem mindset adjustments; HTMX is closer to plain HTML attributes and is already the stack choice.
- **Full-page reloads everywhere**: Rejected — undermines the "HTMX swap in-place" UX that Q4 clarification depends on.

**Implementation notes**:
- Template naming convention: `brief.html` is the full page; `_brief_item.html`, `_observation_row.html`, `_intervention_modal.html`, `_debug_panel.html` are partials prefixed with `_`.
- `web/routes/brief.py` has two decorators per page: one for the full-page GET, one for the HTMX-triggered refresh GET that returns just the inner content.
- CSRF: not required — single-user prototype with no auth, localhost-only. Document in assumptions.
- Out-of-band swaps (`hx-swap-oob`) for status-bar updates on the same response as an intervention action (e.g., "3 items left in today's brief" counter).
- Keep Alpine.js to dropdown state (open/closed), copy-to-clipboard on Approve, and toast dismissal. All three are ~5-line directives.

---

## Open questions

None. Three researched items all have clear decisions; two deferred items in plan.md (APScheduler job store, Tailwind palette hex codes) are confirmed-not-research (trivial in-task decisions). No `NEEDS CLARIFICATION` markers remain.
