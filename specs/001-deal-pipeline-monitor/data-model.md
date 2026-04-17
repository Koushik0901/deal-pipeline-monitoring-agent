# Phase 1 Data Model: Deal Pipeline Monitoring Agent

**Scope**: Domain SQLite schema (6 tables + `ticks`), LangGraph state shapes, Pydantic contract models, YAML fixture format for golden scenarios. DDL is illustrative — canonical DDL lives in `src/hiive_monitor/db/schema.sql` once implementation begins.

---

## 1. Domain database (`domain.db`)

Single SQLite file, seeded and checked in. Separate from `agent_checkpoints.db` (LangGraph internal state).

### 1.1 `issuers`
Real Hiive-listed companies (SpaceX, Stripe, Anthropic, Perplexity, Cerebras, Groq, Databricks, Canva, Rippling, Ramp) with engineered response characteristics.

```sql
CREATE TABLE issuers (
  issuer_id          TEXT PRIMARY KEY,          -- e.g., 'spacex'
  display_name       TEXT NOT NULL,
  typical_response_days  INTEGER NOT NULL,      -- baseline responsiveness
  rofr_window_days   INTEGER NOT NULL,          -- default ROFR clock length
  multi_layer_rofr   INTEGER NOT NULL DEFAULT 0,-- 0/1 flag
  notes              TEXT                       -- engineered-variety notes
);
```

### 1.2 `parties`
Buyers and sellers (counterparties on deals). Synthetic but realistic.

```sql
CREATE TABLE parties (
  party_id           TEXT PRIMARY KEY,
  display_name       TEXT NOT NULL,
  party_type         TEXT NOT NULL CHECK (party_type IN ('buyer','seller')),
  is_first_time      INTEGER NOT NULL DEFAULT 0,
  prior_breakage_count INTEGER NOT NULL DEFAULT 0,
  typical_response_days INTEGER NOT NULL,
  notes              TEXT
);
```

### 1.3 `deals`
Current canonical state per live deal. One row per deal.

```sql
CREATE TABLE deals (
  deal_id            TEXT PRIMARY KEY,          -- e.g., 'D-0042'
  issuer_id          TEXT NOT NULL REFERENCES issuers(issuer_id),
  buyer_id           TEXT NOT NULL REFERENCES parties(party_id),
  seller_id          TEXT NOT NULL REFERENCES parties(party_id),
  size_usd           INTEGER NOT NULL,
  stage              TEXT NOT NULL CHECK (stage IN (
                       'bid_accepted','docs_pending','issuer_notified',
                       'rofr_pending','rofr_cleared','signing','funding',
                       'settled','broken')),
  stage_entered_at   TEXT NOT NULL,             -- ISO8601, clock-provided
  rofr_deadline      TEXT,                      -- ISO8601, nullable
  responsible_party  TEXT NOT NULL CHECK (responsible_party IN (
                       'buyer','seller','issuer','hiive_ts')),
  blockers_json      TEXT NOT NULL DEFAULT '[]',-- structured blocker list
  risk_factors_json  TEXT NOT NULL DEFAULT '{}',-- is_first_time_buyer, etc.
  last_deep_review_tick TEXT,                   -- FK-ish to ticks.tick_id
  created_at         TEXT NOT NULL,
  updated_at         TEXT NOT NULL
);
CREATE INDEX idx_deals_stage ON deals(stage);
CREATE INDEX idx_deals_rofr_deadline ON deals(rofr_deadline);
```

**Stage dwell-time baselines** (seed constants, used for FR-004 aging calc):
`bid_accepted:2, docs_pending:3, issuer_notified:5, rofr_pending:30, rofr_cleared:3, signing:3, funding:5`. Stored in code (`src/hiive_monitor/models/stages.py`), not the DB — these are reference values, not per-deal state.

### 1.4 `events`
Append-only log of everything that happens to a deal.

```sql
CREATE TABLE events (
  event_id           INTEGER PRIMARY KEY AUTOINCREMENT,
  deal_id            TEXT NOT NULL REFERENCES deals(deal_id),
  event_type         TEXT NOT NULL CHECK (event_type IN (
                       'stage_transition','doc_received','doc_requested',
                       'comm_outbound','comm_inbound','agent_observation_ref')),
  payload_json       TEXT NOT NULL,
  occurred_at        TEXT NOT NULL,             -- ISO8601, clock-provided
  created_at         TEXT NOT NULL
);
CREATE INDEX idx_events_deal_time ON events(deal_id, occurred_at DESC);
```

### 1.5 `ticks`
Written atomically at tick start — the idempotency anchor.

```sql
CREATE TABLE ticks (
  tick_id            TEXT PRIMARY KEY,          -- UUID v4
  mode               TEXT NOT NULL CHECK (mode IN ('real_time','simulated')),
  tick_started_at    TEXT NOT NULL,             -- clock time when tick began
  tick_completed_at  TEXT,                      -- null until complete
  deals_screened     INTEGER,
  deals_investigated INTEGER
);
```

### 1.6 `agent_observations`
Every observation produced by the Deal Investigator. Full reasoning persisted.

```sql
CREATE TABLE agent_observations (
  observation_id     TEXT PRIMARY KEY,          -- UUID v4
  tick_id            TEXT NOT NULL REFERENCES ticks(tick_id),
  deal_id            TEXT NOT NULL REFERENCES deals(deal_id),
  severity           TEXT NOT NULL CHECK (severity IN (
                       'informational','watch','act','escalate')),
  risk_signals_json  TEXT NOT NULL,             -- list[RiskSignal]
  reasoning          TEXT NOT NULL,             -- structured reasoning
  intervention_id    TEXT REFERENCES interventions(intervention_id),
  observed_at        TEXT NOT NULL,
  UNIQUE(tick_id, deal_id)                      -- idempotency per FR-024
);
```

### 1.7 `interventions`
Drafts awaiting analyst review. Never auto-sent.

```sql
CREATE TABLE interventions (
  intervention_id    TEXT PRIMARY KEY,          -- UUID v4
  tick_id            TEXT NOT NULL REFERENCES ticks(tick_id),
  deal_id            TEXT NOT NULL REFERENCES deals(deal_id),
  intervention_type  TEXT NOT NULL CHECK (intervention_type IN (
                       'outbound_nudge','internal_escalation','brief_entry')),
  draft_json         TEXT NOT NULL,             -- typed per intervention_type
  status             TEXT NOT NULL CHECK (status IN (
                       'pending','approved','edited','dismissed')),
  analyst_edit_json  TEXT,                      -- populated on edit
  decided_at         TEXT,
  created_at         TEXT NOT NULL,
  UNIQUE(tick_id, deal_id, intervention_type)   -- idempotency
);
CREATE INDEX idx_interventions_status ON interventions(status);
```

**Invariant**: there is no column for "sent" or "delivered." The module exposes no `send()` method. HITL is enforced by absence.

---

## 2. LangGraph state shapes

LangGraph uses Python `TypedDict` with annotated reducers. Two graphs.

### 2.1 `MonitorState` (supervisor graph)

```python
class MonitorState(TypedDict):
    tick_id: str
    mode: Literal["real_time", "simulated"]
    tick_started_at: str               # ISO8601
    candidate_deals: list[str]         # all live deal_ids
    attention_scores: dict[str, float] # deal_id -> Haiku score [0,1]
    investigation_queue: list[str]     # top-K selected deal_ids
    investigator_results: Annotated[
        list[InvestigatorResult], operator.add  # fan-in reducer
    ]
    errors: Annotated[list[ErrorRecord], operator.add]
```

**Nodes**: `load_live_deals` → `screen_with_haiku` → `select_investigation_queue` → `fan_out_investigators` → `compose_daily_brief` → `close_tick`.

**Checkpointer config**: `thread_id = tick_id`, checkpoint namespace defaults.

### 2.2 `InvestigatorState` (per-deal sub-graph)

```python
class InvestigatorState(TypedDict):
    tick_id: str
    deal_id: str
    deal_snapshot: DealSnapshot        # Pydantic, frozen state at observation
    risk_signals: Annotated[list[RiskSignal], operator.add]
    severity: Severity | None
    severity_reasoning: str | None
    intervention: Intervention | None
    observation_id: str | None
```

**Nodes**: `observe` → `evaluate_risks` (fans out 6 dimension nodes in parallel) → `decide_severity` → conditional edge → `draft_intervention` (only if severity ∈ {act, escalate}) → `emit_observation`.

**Checkpointer config**: `thread_id = tick_id`, `checkpoint_ns = deal_id` — per-deal isolation so one deal's crash doesn't invalidate siblings.

---

## 3. Pydantic contract models

All live under `src/hiive_monitor/models/`. Every LLM structured output is one of these.

### 3.1 Core value objects

```python
class Stage(str, Enum):
    BID_ACCEPTED = "bid_accepted"
    DOCS_PENDING = "docs_pending"
    ISSUER_NOTIFIED = "issuer_notified"
    ROFR_PENDING = "rofr_pending"
    ROFR_CLEARED = "rofr_cleared"
    SIGNING = "signing"
    FUNDING = "funding"
    SETTLED = "settled"
    BROKEN = "broken"

class Severity(str, Enum):
    INFORMATIONAL = "informational"
    WATCH = "watch"
    ACT = "act"
    ESCALATE = "escalate"

class RiskDimension(str, Enum):
    STAGE_AGING = "stage_aging"
    DEADLINE_PROXIMITY = "deadline_proximity"
    COMMUNICATION_SILENCE = "communication_silence"
    MISSING_PREREQUISITES = "missing_prerequisites"
    COUNTERPARTY_NONRESPONSIVENESS = "counterparty_nonresponsiveness"
    UNUSUAL_CHARACTERISTICS = "unusual_characteristics"
```

### 3.2 LLM output schemas

```python
class AttentionScore(BaseModel):                 # Haiku screening
    deal_id: str
    score: float = Field(ge=0.0, le=1.0)
    reason: str = Field(max_length=240)

class RiskSignal(BaseModel):                     # one per dimension
    dimension: RiskDimension
    triggered: bool
    evidence: str = Field(max_length=400)        # deal-specific facts
    confidence: float = Field(ge=0.0, le=1.0)

class SeverityDecision(BaseModel):
    severity: Severity
    reasoning: str = Field(max_length=600)
    primary_dimensions: list[RiskDimension] = Field(max_length=3)

class OutboundNudge(BaseModel):
    recipient_type: Literal["buyer","seller","issuer"]
    recipient_name: str
    subject: str = Field(max_length=120)
    body: str = Field(max_length=1200)           # addresses deal-specific facts
    referenced_deadline: str | None              # ISO8601 if deadline-driven

class InternalEscalation(BaseModel):
    escalate_to: Literal["ts_lead","legal","ops"]
    headline: str = Field(max_length=120)
    body: str = Field(max_length=800)
    suggested_next_step: str = Field(max_length=200)

class BriefEntry(BaseModel):
    headline: str = Field(max_length=100)
    one_line_summary: str = Field(max_length=160)
    recommended_action: str = Field(max_length=200)

class Intervention(BaseModel):
    intervention_type: Literal[
        "outbound_nudge","internal_escalation","brief_entry"
    ]
    payload: OutboundNudge | InternalEscalation | BriefEntry

class DailyBrief(BaseModel):                     # Sonnet composer output
    tick_id: str
    generated_at: str
    items: list[DailyBriefItem] = Field(max_length=7)

class DailyBriefItem(BaseModel):
    deal_id: str
    rank: int = Field(ge=1, le=7)
    severity: Severity
    one_line_summary: str = Field(max_length=160)
    reasoning: str = Field(max_length=600)
    intervention_id: str | None
```

### 3.3 Non-LLM contract models

```python
class DealSnapshot(BaseModel):                   # frozen read passed to graph
    deal_id: str
    issuer_id: str
    stage: Stage
    stage_entered_at: datetime
    days_in_stage: int                           # derived via Clock
    rofr_deadline: datetime | None
    days_to_rofr: int | None
    responsible_party: Literal["buyer","seller","issuer","hiive_ts"]
    blockers: list[Blocker]
    risk_factors: dict[str, Any]
    recent_events: list[EventRef]                # last N events
    days_since_last_comm: int | None

class Blocker(BaseModel):
    kind: Literal["missing_doc","pending_signature","awaiting_response","other"]
    description: str
    since: datetime

class EventRef(BaseModel):
    event_type: str
    occurred_at: datetime
    summary: str
```

---

## 4. YAML fixture format (Tier 1 golden scenarios)

Location: `src/hiive_monitor/eval/scenarios/*.yaml`. One file per scenario.

```yaml
# scenarios/rofr_deadline_7d.yaml
id: rofr_deadline_7d
category: detection                 # detection | prioritization | intervention_quality
description: >
  Deal in rofr_pending with 7 days to deadline and no recent communication.
  Agent must flag deadline_proximity + communication_silence, severity=act,
  draft an outbound_nudge referencing the specific deadline.

setup:
  now: "2026-04-16T09:00:00Z"       # simulated clock start
  issuers:
    - issuer_id: spacex
      typical_response_days: 7
      rofr_window_days: 30
      multi_layer_rofr: false
  parties:
    - party_id: buyer_a1
      party_type: buyer
      is_first_time: false
    - party_id: seller_s1
      party_type: seller
  deal:
    deal_id: D-TEST-001
    issuer_id: spacex
    buyer_id: buyer_a1
    seller_id: seller_s1
    size_usd: 500000
    stage: rofr_pending
    stage_entered_at: "2026-03-27T09:00:00Z"   # 20 days ago
    rofr_deadline: "2026-04-23T09:00:00Z"      # 7 days away
    responsible_party: issuer
    blockers: []
    risk_factors: {is_first_time_buyer: false}
  events:
    - event_type: comm_outbound
      occurred_at: "2026-04-02T10:00:00Z"      # 14 days ago — silence
      summary: "Sent ROFR notice to SpaceX"

assertions:
  risk_signals:
    - dimension: deadline_proximity
      triggered: true
    - dimension: communication_silence
      triggered: true
  severity:
    equals: act
  intervention:
    type: outbound_nudge
    recipient_type: issuer
    body_must_contain:                          # substring or regex hints
      - "SpaceX"
      - "April 23"                              # or regex: "\\bApril 23\\b"
    body_must_not_contain:
      - "TODO"
      - "placeholder"
  brief_rank:
    max: 3                                      # must appear in top 3 today
```

**Categories × counts** (MVP target, 15 scenarios):
- `detection` × 8 — one per (dimension × trigger condition) pair plus negatives.
- `prioritization` × 4 — multi-deal setups checking brief ranking.
- `intervention_quality` × 3 — structural checks (recipient correct, deadline referenced, no placeholders).

**Runner behavior**: load fixture → truncate `domain.db` → apply setup → set `SimulatedClock` to `setup.now` → invoke one Monitor tick → evaluate assertions → emit scorecard row.

---

## 5. Field-name mapping to spec entities

| Spec entity (spec.md §Key Entities) | DB table(s) | Pydantic model(s) |
|---|---|---|
| Deal | `deals` | `DealSnapshot` |
| Event | `events` | `EventRef` |
| Agent Observation | `agent_observations` | (persisted form of) `RiskSignal[]` + `SeverityDecision` |
| Intervention | `interventions` | `Intervention` + `OutboundNudge`\|`InternalEscalation`\|`BriefEntry` |
| Issuer | `issuers` | — (seed only) |
| Party | `parties` | — (seed only) |
| Tick | `ticks` | (correlation id threaded through all graph state) |
| Daily Brief | composed at read time | `DailyBrief` + `DailyBriefItem` |

No spec entity is without a persistence + contract binding.
