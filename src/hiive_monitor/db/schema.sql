-- Hiive Deal Pipeline Monitor — Domain Schema
-- Seven entity types; agent checkpoint state lives in agent_checkpoints.db (separate).
-- All timestamps are ISO8601 strings (UTC).

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- ── Issuers ──────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS issuers (
    issuer_id            TEXT PRIMARY KEY,
    name                 TEXT NOT NULL,
    typical_response_days INTEGER NOT NULL DEFAULT 5,
    rofr_window_days     INTEGER NOT NULL DEFAULT 30,
    multi_layer_rofr     INTEGER NOT NULL DEFAULT 0,  -- BOOL
    sector               TEXT,
    created_at           TEXT NOT NULL
);

-- ── Parties ──────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS parties (
    party_id             TEXT PRIMARY KEY,
    party_type           TEXT NOT NULL CHECK (party_type IN ('buyer', 'seller', 'counsel', 'internal')),
    display_name         TEXT NOT NULL,
    is_first_time        INTEGER NOT NULL DEFAULT 0,   -- BOOL
    prior_breakage_count INTEGER NOT NULL DEFAULT 0,
    created_at           TEXT NOT NULL
);

-- ── Deals ────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS deals (
    deal_id              TEXT PRIMARY KEY,
    issuer_id            TEXT NOT NULL REFERENCES issuers(issuer_id),
    buyer_id             TEXT NOT NULL REFERENCES parties(party_id),
    seller_id            TEXT NOT NULL REFERENCES parties(party_id),
    shares               INTEGER NOT NULL,
    price_per_share      REAL NOT NULL,
    stage                TEXT NOT NULL CHECK (stage IN (
        'bid_accepted', 'docs_pending', 'issuer_notified', 'rofr_pending',
        'rofr_cleared', 'signing', 'funding', 'settled', 'broken'
    )),
    stage_entered_at     TEXT NOT NULL,  -- simulated-time ISO8601
    rofr_deadline        TEXT,           -- simulated-time ISO8601 or NULL
    responsible_party    TEXT CHECK (responsible_party IN ('buyer', 'seller', 'issuer', 'hiive_ts', NULL)),
    blockers             TEXT NOT NULL DEFAULT '[]',   -- JSON array of {kind, description, since}
    risk_factors         TEXT NOT NULL DEFAULT '{}',   -- JSON {is_first_time_buyer, prior_breakage_count, ...}
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL
);

-- ── Events (append-only) ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS events (
    event_id             TEXT PRIMARY KEY,
    deal_id              TEXT NOT NULL REFERENCES deals(deal_id),
    event_type           TEXT NOT NULL CHECK (event_type IN (
        'stage_transition', 'doc_received', 'doc_requested',
        'comm_outbound', 'comm_inbound', 'comm_sent_agent_recommended'
    )),
    occurred_at          TEXT NOT NULL,  -- simulated-time ISO8601 (FR-019)
    created_at           TEXT NOT NULL,  -- real-time ISO8601 (FR-019)
    summary              TEXT NOT NULL DEFAULT '',
    payload              TEXT NOT NULL DEFAULT '{}'  -- JSON freeform
);

CREATE INDEX IF NOT EXISTS idx_events_deal_id ON events(deal_id);
CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type);

-- ── Ticks ────────────────────────────────────────────────────────────────────
-- Load-bearing for FR-024 idempotency: written atomically at tick start.
CREATE TABLE IF NOT EXISTS ticks (
    tick_id              TEXT PRIMARY KEY,
    mode                 TEXT NOT NULL CHECK (mode IN ('real_time', 'simulated')),
    tick_started_at      TEXT NOT NULL,   -- real-time ISO8601
    tick_completed_at    TEXT,            -- NULL until complete
    deals_screened       INTEGER,
    deals_investigated   INTEGER,
    errors               TEXT NOT NULL DEFAULT '[]'  -- JSON array of error strings
);

-- ── Agent Observations ───────────────────────────────────────────────────────
-- One row per (tick_id, deal_id). Dedup via UNIQUE constraint (FR-024).
CREATE TABLE IF NOT EXISTS agent_observations (
    observation_id       TEXT PRIMARY KEY,
    tick_id              TEXT NOT NULL REFERENCES ticks(tick_id),
    deal_id              TEXT NOT NULL REFERENCES deals(deal_id),
    observed_at          TEXT NOT NULL,
    severity             TEXT CHECK (severity IN ('informational', 'watch', 'act', 'escalate', NULL)),
    dimensions_evaluated TEXT NOT NULL DEFAULT '[]',  -- JSON list of RiskDimension names
    reasoning            TEXT NOT NULL DEFAULT '{}',  -- JSON: full structured reasoning incl. enrichment_chain
    intervention_id      TEXT REFERENCES interventions(intervention_id),  -- linked if drafted
    UNIQUE (tick_id, deal_id)  -- idempotency anchor (FR-024)
);

CREATE INDEX IF NOT EXISTS idx_observations_deal_id ON agent_observations(deal_id);
CREATE INDEX IF NOT EXISTS idx_observations_tick_id ON agent_observations(tick_id);

-- ── Interventions ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS interventions (
    intervention_id      TEXT PRIMARY KEY,
    deal_id              TEXT NOT NULL REFERENCES deals(deal_id),
    observation_id       TEXT REFERENCES agent_observations(observation_id),
    intervention_type    TEXT NOT NULL CHECK (intervention_type IN (
        'outbound_nudge', 'internal_escalation', 'brief_entry'
    )),
    recipient_type       TEXT CHECK (recipient_type IN ('buyer', 'seller', 'issuer', 'internal', NULL)),
    draft_subject        TEXT,
    draft_body           TEXT NOT NULL,
    final_text           TEXT,   -- NULL until approved/edited
    status               TEXT NOT NULL DEFAULT 'pending' CHECK (status IN (
        'pending', 'approved', 'edited', 'dismissed'
    )),
    reasoning_ref        TEXT,   -- observation_id whose reasoning produced this
    created_at           TEXT NOT NULL,
    approved_at          TEXT,
    dismissed_at         TEXT
);

CREATE INDEX IF NOT EXISTS idx_interventions_deal_id ON interventions(deal_id);
CREATE INDEX IF NOT EXISTS idx_interventions_status ON interventions(status);
