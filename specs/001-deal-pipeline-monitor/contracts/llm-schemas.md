# LLM Output Schemas

Every Claude call is a **tool-use forced** call. The tool's `input_schema` is derived from a Pydantic model via `model.model_json_schema()`. Parse failure triggers one corrective reprompt (FR-025); a second failure persists an error observation.

Canonical Pydantic definitions live in [data-model.md §3](../data-model.md). This file is the per-call contract: which model, which prompt, which output schema, what the call is allowed to say.

## Field naming conventions

These three fields have similar names but different types, locations, and purposes. Do not conflate them.

- **`reasoning`**: the full structured reasoning JSON column in `agent_observations`. Contains all dimension results, sufficiency decisions, enrichment steps, and final severity rationale as a single JSON object.
- **`reasoning_summary`**: human-readable prose summary field in `fetch_prior_observations` tool output. Derived from the `reasoning` column of prior observations. String, not JSON.
- **`reasoning_ref`**: foreign-key-style reference in the `interventions` table pointing to the `agent_observation` row whose reasoning produced this intervention. UUID string, not the reasoning content itself.

## Model routing

| Call name | Model | Rationale |
|---|---|---|
| `screen_deal` | `claude-haiku-4-5-20251001` | Cheap per-deal attention score on every live deal per tick. |
| `evaluate_risk_<dimension>` (×5 LLM dimensions) | `claude-sonnet-4-6` | Reasoned dimension evaluation. |
| `decide_severity` | `claude-sonnet-4-6` | Rubric-driven severity classification. |
| `draft_outbound_nudge` | `claude-sonnet-4-6` | Recipient-appropriate tone, deal-specific facts. |
| `draft_internal_escalation` | `claude-sonnet-4-6` | Internal voice, suggested next step. |
| `draft_brief_entry` | `claude-sonnet-4-6` | Executive-style line for analyst review. |
| `compose_daily_brief` | `claude-sonnet-4-6` | Ranks and narrates the top 5–7 items. |
| `judge_intervention` (Tier 2 stretch) | `claude-sonnet-4-6` | Rubric grader; only loaded if Tier 2 ships. |

Dimension #5 (`counterparty_nonresponsiveness`) is **deterministic** — a Python function over `party.typical_response_days` vs. `days_since_last_comm`. No LLM call.

## Call contracts

### `screen_deal` → `AttentionScore`

**Inputs**: compact `DealSnapshot` (stage, days_in_stage, days_to_rofr, responsible_party, days_since_last_comm, recent-event count). No full history. ~200 tokens in, ~80 tokens out.

**Prompt shape** (pinned in `src/hiive_monitor/llm/prompts/screen.py`):
- System: role = Hiive Transaction Services triage assistant. Vocabulary primer. Output contract: score ∈ [0,1] plus one-sentence reason.
- User: deal facts as structured JSON block.

**Output schema**: `AttentionScore` — `{deal_id: str, score: float ∈ [0,1], reason: str ≤240 chars}`.

**Selection threshold**: score ≥ 0.6 marks a deal "worth deep reasoning." If >5 deals cross threshold, top 5 by score (FR-001a clarification). Ties broken by `days_to_rofr` ascending then `days_in_stage` descending.

### `evaluate_risk_<dimension>` → `RiskSignal`

One call per dimension per selected deal. Dimensions:
- `stage_aging` — is days_in_stage materially over baseline?
- `deadline_proximity` — is ROFR within the escalating band (<10 / <5 / <2 days)?
- `communication_silence` — no outbound/inbound comm vs. expected cadence?
- `missing_prerequisites` — structured blockers still unresolved?
- `unusual_characteristics` — first-time buyer, prior breakage, unusual size, etc.

**Output schema**: `RiskSignal` — `{dimension, triggered: bool, evidence: str ≤400 chars, confidence ∈ [0,1]}`.

**Evidence constraint**: `evidence` MUST reference at least one deal-specific fact (issuer name, stage name, numeric days, deadline, counterparty role, or blocker description). Enforced post-hoc by a substring check against the snapshot; failure triggers the single corrective reprompt.

### `decide_severity` → `SeverityDecision`

**Inputs**: all six `RiskSignal`s + `DealSnapshot`.

**Output schema**: `SeverityDecision` — `{severity: Severity, reasoning: str ≤600 chars, primary_dimensions: list[RiskDimension] ≤3}`.

**Rubric anchors** (in prompt):
- `informational`: no dimension triggered, or only long-horizon unusual-characteristics.
- `watch`: 1–2 dimensions triggered, no deadline within 10 days.
- `act`: deadline within 10 days AND a triggered dimension, OR stage aging >2× baseline, OR counterparty silence >2× typical response.
- `escalate`: deadline within 2 days unresolved, OR multi-dimension hit on a stalled rofr_pending, OR prior_breakage_count > 0 with any act-level trigger.

### `draft_outbound_nudge` → `OutboundNudge`

Only invoked if severity ∈ {`act`, `escalate`} AND the targeted recipient is external (buyer/seller/issuer).

**Output schema**: `OutboundNudge` — `{recipient_type, recipient_name, subject ≤120, body ≤1200, referenced_deadline}`.

**Mandatory content** (FR-033): body must contain issuer display name, stage context, and — if deadline-driven — the calendar date of the relevant deadline. Validator runs post-parse; failure triggers corrective reprompt.

**Tone**: Transaction Services analyst voice — direct, numeric-grounded, no hedging, no signature block.

### `draft_internal_escalation` → `InternalEscalation`

Invoked when severity = `escalate` AND the next action belongs to Hiive internal (TS lead, legal, ops) rather than a counterparty.

**Output schema**: `InternalEscalation` — `{escalate_to, headline ≤120, body ≤800, suggested_next_step ≤200}`.

### `draft_brief_entry` → `BriefEntry`

Invoked when severity = `watch` and the signal is worth analyst awareness but no external message is warranted. Short, readable, one recommended action.

**Output schema**: `BriefEntry` — `{headline ≤100, one_line_summary ≤160, recommended_action ≤200}`.

### `compose_daily_brief` → `DailyBrief`

Runs once per tick after all investigators complete. Ranks act/escalate items and narrates each.

**Inputs**: list of completed `AgentObservation`s this tick + carry-over open interventions from prior ticks.

**Output schema**: `DailyBrief` — `{tick_id, generated_at, items: list[DailyBriefItem] ≤7}`.

**Ranking heuristic** (encoded in prompt + post-validator):
1. `escalate` before `act` before `watch`.
2. Within severity, ascending `days_to_rofr` (None sorts last).
3. Tie-break descending `days_in_stage`.

Post-validator rejects outputs where the rank ordering violates the heuristic → corrective reprompt.

## Cross-cutting call infrastructure

All calls go through `llm/client.py::call_structured(...)`:
- **Timeout**: 30 s per attempt.
- **Retry**: exponential backoff on `AnthropicError` (not `BadRequestError`), max 3 attempts.
- **Corrective reprompt**: on parse failure OR post-validator failure, single follow-up message including validation error text; second failure → error observation.
- **Idempotency cache**: keyed on `(tick_id, deal_id, call_name)`, in-memory for the tick. The LangGraph checkpointer is the durable layer; this cache is belt-and-braces.
- **Logging**: emits `llm.call.completed` with `{model, call_name, latency_ms, input_tokens, output_tokens, tick_id, deal_id, attempt, parse_ok}` via structlog.

## Contract tests (Phase 2+)

- Every Pydantic model round-trips through `model_json_schema()` → Anthropic tool-use → tool-use block → `model_validate`.
- Every prompt has a snapshot test: given a pinned `DealSnapshot`, the call must produce a parseable response against a recorded fixture (mocked LLM) — catches prompt drift.
- Evidence-substring validator has unit tests covering the false-negative path (valid evidence that happens to use synonyms) so we don't over-reject.

## `assess_sufficiency` (N3 — agentic loop node)

**Input**

```python
class DealContext(BaseModel):
    deal_id: str
    stage: DealStage
    stage_age_days: int
    rofr_deadline: datetime | None
    days_to_rofr: int | None
    enrichment_rounds_used: int  # hard cap = 2 (FR-AGENT-04)

class SufficiencyInput(BaseModel):
    dimensions: list[RiskSignal]   # 6 RiskSignal entries from N2
    context: DealContext
    enrichment_chain: list[EnrichmentStepOutput]  # prior rounds, empty on first pass
```

**Output**

```python
class SufficiencyDecision(BaseModel):
    proceed: bool                             # False → route to N4 enrich_context
    rationale: str = Field(max_length=400)
    suggested_tool: Literal[
        "fetch_communication_content",
        "fetch_prior_observations",
        "fetch_issuer_history",
    ] | None = None                           # required when proceed=False
    trigger_matched: Literal[
        "communication_silence_with_prior_context",
        "novel_deal_shape",
        "dimension_conflict",
    ] | None = None                           # the FR-AGENT-05 trigger that fired
```

`SufficiencyDecision` is a Pydantic `BaseModel` (not a TypedDict) — `call_structured` accepts only `BaseModel` subclasses. Graph routing enforces the `enrichment_rounds_used >= 2` hard cap regardless of LLM output.

## `enrich_context` (N4 — bounded enrichment)

**Input**

```python
class EnrichmentRequest(BaseModel):
    tool_name: Literal[
        "fetch_communication_content",
        "fetch_prior_observations",
        "fetch_issuer_history",
    ]
    deal_id: str
    current_findings_summary: str = Field(max_length=800)  # condensed dims + prior rounds
```

**Output**

```python
class EnrichmentStepOutput(BaseModel):
    tool_called: str
    raw_result: str = Field(max_length=2000)    # trimmed tool payload
    interpretation: str = Field(max_length=600) # what this tells us
    changes_assessment: bool                    # True if this alters any dimension's signal
```

`EnrichmentStepOutput` is the Pydantic `BaseModel` returned by `call_structured`. The graph-state `EnrichmentStep` is a TypedDict (see `agents/graph_state.py`); convert via `EnrichmentStep(**output.model_dump())` before appending to state. This is the explicit bridge between the LLM client contract and the LangGraph state schema.
