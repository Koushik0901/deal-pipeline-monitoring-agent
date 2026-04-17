"""
Data-access helpers for the domain database.
All writes use INSERT … ON CONFLICT DO NOTHING for idempotency (FR-024).
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime
from typing import Any

from hiive_monitor import clock as clk

# ── Deals ─────────────────────────────────────────────────────────────────────


def get_live_deals(conn: sqlite3.Connection) -> list[dict]:
    """Return all deals not in settled or broken stage."""
    rows = conn.execute(
        "SELECT * FROM deals WHERE stage NOT IN ('settled', 'broken') ORDER BY stage_entered_at"
    ).fetchall()
    return [dict(r) for r in rows]


def get_deal(conn: sqlite3.Connection, deal_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM deals WHERE deal_id = ?", (deal_id,)).fetchone()
    return dict(row) if row else None


def get_all_deals(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT * FROM deals ORDER BY created_at").fetchall()
    return [dict(r) for r in rows]


# ── Events ────────────────────────────────────────────────────────────────────


def get_events(conn: sqlite3.Connection, deal_id: str, limit: int | None = None) -> list[dict]:
    if limit:
        rows = conn.execute(
            "SELECT * FROM events WHERE deal_id = ? ORDER BY occurred_at DESC LIMIT ?",
            (deal_id, limit),
        ).fetchall()
        return list(reversed([dict(r) for r in rows]))
    rows = conn.execute(
        "SELECT * FROM events WHERE deal_id = ? ORDER BY occurred_at", (deal_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def insert_event(
    conn: sqlite3.Connection,
    deal_id: str,
    event_type: str,
    occurred_at: datetime,
    summary: str = "",
    payload: dict | None = None,
    event_id: str | None = None,
) -> str:
    eid = event_id or str(uuid.uuid4())
    real_now = clk.now().isoformat()
    conn.execute(
        """INSERT OR IGNORE INTO events
           (event_id, deal_id, event_type, occurred_at, created_at, summary, payload)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (eid, deal_id, event_type, occurred_at.isoformat(), real_now, summary, json.dumps(payload or {})),
    )
    return eid


# ── Ticks ─────────────────────────────────────────────────────────────────────


def start_tick(conn: sqlite3.Connection, mode: str, tick_id: str | None = None) -> str:
    """
    Atomically insert a tick row and return the tick_id.
    If a row with this tick_id already exists, the INSERT is ignored (idempotency FR-024).
    """
    tid = tick_id or str(uuid.uuid4())
    real_now = clk.now().isoformat()
    conn.execute(
        """INSERT OR IGNORE INTO ticks (tick_id, mode, tick_started_at)
           VALUES (?, ?, ?)""",
        (tid, mode, real_now),
    )
    conn.commit()
    return tid


def complete_tick(
    conn: sqlite3.Connection,
    tick_id: str,
    deals_screened: int = 0,
    deals_investigated: int = 0,
    errors: list[str] | None = None,
) -> None:
    real_now = clk.now().isoformat()
    conn.execute(
        """UPDATE ticks
           SET tick_completed_at = ?, deals_screened = ?, deals_investigated = ?, errors = ?
           WHERE tick_id = ?""",
        (real_now, deals_screened, deals_investigated, json.dumps(errors or []), tick_id),
    )
    conn.commit()


def get_tick(conn: sqlite3.Connection, tick_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM ticks WHERE tick_id = ?", (tick_id,)).fetchone()
    return dict(row) if row else None


def get_last_completed_tick(conn: sqlite3.Connection) -> dict | None:
    row = conn.execute(
        "SELECT * FROM ticks WHERE tick_completed_at IS NOT NULL ORDER BY tick_started_at DESC LIMIT 1"
    ).fetchone()
    return dict(row) if row else None


# ── Agent Observations ────────────────────────────────────────────────────────


def insert_observation(
    conn: sqlite3.Connection,
    tick_id: str,
    deal_id: str,
    severity: str | None,
    dimensions_evaluated: list[str],
    reasoning: dict,
    intervention_id: str | None = None,
    observation_id: str | None = None,
) -> str:
    oid = observation_id or str(uuid.uuid4())
    observed_at = clk.now().isoformat()
    conn.execute(
        """INSERT OR IGNORE INTO agent_observations
           (observation_id, tick_id, deal_id, observed_at, severity,
            dimensions_evaluated, reasoning, intervention_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            oid,
            tick_id,
            deal_id,
            observed_at,
            severity,
            json.dumps(dimensions_evaluated),
            json.dumps(reasoning),
            intervention_id,
        ),
    )
    conn.commit()
    return oid


def get_observations(conn: sqlite3.Connection, deal_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM agent_observations WHERE deal_id = ? ORDER BY observed_at",
        (deal_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_observations_by_tick(conn: sqlite3.Connection, tick_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM agent_observations WHERE tick_id = ?", (tick_id,)
    ).fetchall()
    return [dict(r) for r in rows]


# ── Interventions ─────────────────────────────────────────────────────────────


def insert_intervention(
    conn: sqlite3.Connection,
    deal_id: str,
    observation_id: str,
    intervention_type: str,
    draft_body: str,
    recipient_type: str | None = None,
    draft_subject: str | None = None,
    reasoning_ref: str | None = None,
    intervention_id: str | None = None,
) -> str:
    iid = intervention_id or str(uuid.uuid4())
    created_at = clk.now().isoformat()
    conn.execute(
        """INSERT OR IGNORE INTO interventions
           (intervention_id, deal_id, observation_id, intervention_type,
            recipient_type, draft_subject, draft_body, reasoning_ref, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            iid,
            deal_id,
            observation_id,
            intervention_type,
            recipient_type,
            draft_subject,
            draft_body,
            reasoning_ref,
            created_at,
        ),
    )
    conn.commit()
    return iid


def update_intervention_status(
    conn: sqlite3.Connection,
    intervention_id: str,
    status: str,
    final_text: str | None = None,
) -> None:
    conn.execute(
        "UPDATE interventions SET status = ?, final_text = ? WHERE intervention_id = ?",
        (status, final_text, intervention_id),
    )
    conn.commit()


def has_open_intervention(conn: sqlite3.Connection, deal_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM interventions WHERE deal_id = ? AND status = 'pending' LIMIT 1",
        (deal_id,),
    ).fetchone()
    return row is not None


def get_intervention(conn: sqlite3.Connection, intervention_id: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM interventions WHERE intervention_id = ?", (intervention_id,)
    ).fetchone()
    return dict(row) if row else None


def get_interventions(conn: sqlite3.Connection, deal_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM interventions WHERE deal_id = ? ORDER BY created_at", (deal_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def get_open_interventions(conn: sqlite3.Connection) -> list[dict]:
    """All pending interventions (any severity) ordered by severity then age."""
    rows = conn.execute(
        """SELECT i.*, d.issuer_id, d.stage, o.severity
           FROM interventions i
           JOIN deals d ON d.deal_id = i.deal_id
           LEFT JOIN agent_observations o ON o.observation_id = i.observation_id
           WHERE i.status = 'pending'
           ORDER BY
             CASE o.severity
               WHEN 'escalate' THEN 0
               WHEN 'act'      THEN 1
               WHEN 'watch'    THEN 2
               ELSE 3
             END,
             i.created_at"""
    ).fetchall()
    return [dict(r) for r in rows]


# ── Enrichment tool functions (read-only, called by N4 enrich_context) ─────


def fetch_communication_content(conn: sqlite3.Connection, deal_id: str) -> list[dict]:
    """Retrieve actual message text from comm history (FR-AGENT-03)."""
    rows = conn.execute(
        """SELECT occurred_at, event_type as direction, payload
           FROM events
           WHERE deal_id = ? AND event_type IN ('comm_outbound', 'comm_inbound', 'comm_sent_agent_recommended')
           ORDER BY occurred_at DESC
           LIMIT 10""",
        (deal_id,),
    ).fetchall()
    result = []
    for r in rows:
        payload = json.loads(r["payload"]) if r["payload"] else {}
        result.append({
            "occurred_at": r["occurred_at"],
            "direction": r["direction"],
            "body": payload.get("body", payload.get("summary", "")),
        })
    return result


def fetch_prior_observations(conn: sqlite3.Connection, deal_id: str) -> list[dict]:
    """Recent agent observations for this deal (FR-AGENT-03)."""
    rows = conn.execute(
        """SELECT tick_id, severity, reasoning
           FROM agent_observations
           WHERE deal_id = ?
           ORDER BY observed_at DESC
           LIMIT 5""",
        (deal_id,),
    ).fetchall()
    result = []
    for r in rows:
        reasoning = json.loads(r["reasoning"]) if r["reasoning"] else {}
        result.append({
            "tick_id": r["tick_id"],
            "severity": r["severity"],
            "reasoning_summary": reasoning.get("severity_rationale", reasoning.get("summary", "")),
        })
    return result


def fetch_issuer_history(conn: sqlite3.Connection, issuer_id: str) -> list[dict]:
    """Prior settled/broken deals for this issuer (FR-AGENT-03)."""
    rows = conn.execute(
        """SELECT d.deal_id, d.stage as final_stage, o.reasoning
           FROM deals d
           LEFT JOIN agent_observations o ON o.deal_id = d.deal_id
           WHERE d.issuer_id = ? AND d.stage IN ('settled', 'broken')
           ORDER BY d.updated_at DESC
           LIMIT 5""",
        (issuer_id,),
    ).fetchall()
    result = []
    for r in rows:
        reasoning = json.loads(r["reasoning"]) if r["reasoning"] else {}
        result.append({
            "deal_id": r["deal_id"],
            "final_stage": r["final_stage"],
            "key_signals": reasoning.get("dimensions_triggered", []),
        })
    return result


# ── Suppression query (FR-LOOP-02) ────────────────────────────────────────────


def get_suppressed_deal_ids(
    conn: sqlite3.Connection, deal_ids: list[str], within_ticks: int
) -> set[str]:
    """
    Return the subset of deal_ids that have a comm_sent_agent_recommended event
    within the last `within_ticks` completed ticks. Single round-trip version.
    """
    if not deal_ids:
        return set()
    recent_ticks = conn.execute(
        """SELECT tick_started_at FROM ticks
           WHERE tick_completed_at IS NOT NULL
           ORDER BY tick_started_at DESC
           LIMIT ?""",
        (within_ticks,),
    ).fetchall()
    if not recent_ticks:
        return set()
    # Oldest tick in the window defines the cutoff timestamp
    cutoff = min(r["tick_started_at"] for r in recent_ticks)
    placeholders = ",".join("?" * len(deal_ids))
    rows = conn.execute(
        f"""SELECT DISTINCT deal_id FROM events
            WHERE deal_id IN ({placeholders})
              AND event_type = 'comm_sent_agent_recommended'
              AND created_at >= ?""",
        [*deal_ids, cutoff],
    ).fetchall()
    return {r["deal_id"] for r in rows}


# ── Approval transaction (FR-LOOP-01, FR-LOOP-03) ────────────────────────────


def approve_intervention_atomic(
    conn: sqlite3.Connection,
    intervention_id: str,
    simulated_timestamp: datetime,
    final_text: str | None = None,
) -> str:
    """
    Atomically:
    1. Update intervention status to 'approved' (and final_text if provided).
    2. Insert a comm_sent_agent_recommended event for the deal.

    Both writes happen in a single transaction. If either fails, both roll back (FR-LOOP-01).
    Returns the event_id of the newly created comm event.
    """
    intervention = get_intervention(conn, intervention_id)
    if not intervention:
        raise ValueError(f"Intervention {intervention_id} not found")

    deal_id = intervention["deal_id"]
    event_id = str(uuid.uuid4())
    real_now = clk.now().isoformat()

    with conn:
        conn.execute(
            """UPDATE interventions
               SET status = 'approved', final_text = ?, approved_at = ?
               WHERE intervention_id = ?""",
            (final_text or intervention["draft_body"], real_now, intervention_id),
        )
        conn.execute(
            """INSERT INTO events
               (event_id, deal_id, event_type, occurred_at, created_at, summary, payload)
               VALUES (?, ?, 'comm_sent_agent_recommended', ?, ?, ?, ?)""",
            (
                event_id,
                deal_id,
                simulated_timestamp.isoformat(),
                real_now,
                "Outreach sent via agent recommendation",
                json.dumps({"intervention_id": intervention_id}),
            ),
        )

    return event_id


def edit_intervention_atomic(
    conn: sqlite3.Connection,
    intervention_id: str,
    simulated_timestamp: datetime,
    final_text: str,
) -> str:
    """
    Atomically mark intervention as 'edited' with the analyst's final text
    AND emit a comm_sent_agent_recommended event (FR-LOOP-01).
    """
    intervention = get_intervention(conn, intervention_id)
    if not intervention:
        raise ValueError(f"Intervention {intervention_id} not found")

    deal_id = intervention["deal_id"]
    event_id = str(uuid.uuid4())
    real_now = clk.now().isoformat()

    with conn:
        conn.execute(
            """UPDATE interventions
               SET status = 'edited', final_text = ?, approved_at = ?
               WHERE intervention_id = ?""",
            (final_text, real_now, intervention_id),
        )
        conn.execute(
            """INSERT INTO events
               (event_id, deal_id, event_type, occurred_at, created_at, summary, payload)
               VALUES (?, ?, 'comm_sent_agent_recommended', ?, ?, ?, ?)""",
            (
                event_id,
                deal_id,
                simulated_timestamp.isoformat(),
                real_now,
                "Outreach sent via agent recommendation (analyst-edited draft)",
                json.dumps({"intervention_id": intervention_id, "edited": True}),
            ),
        )

    return event_id


# ── Intervention outcome tracking (TS08) ──────────────────────────────────────


def fetch_intervention_outcomes(conn: sqlite3.Connection, issuer_id: str) -> list[dict]:
    """
    For each approved/edited intervention on deals belonging to this issuer,
    check whether a stage_transition or comm_inbound event followed within 7 days.
    Returns stats grouped by intervention_type.
    Attribution is correlation, not causation.
    """
    rows = conn.execute(
        """SELECT
               i.intervention_type,
               CASE
                 WHEN EXISTS (
                   SELECT 1 FROM events e
                   WHERE e.deal_id = i.deal_id
                     AND e.event_type IN ('stage_transition', 'comm_inbound')
                     AND e.occurred_at > i.approved_at
                     AND e.occurred_at <= datetime(i.approved_at, '+7 days')
                 ) THEN 1 ELSE 0
               END AS responded
           FROM interventions i
           JOIN deals d ON d.deal_id = i.deal_id
           WHERE d.issuer_id = ?
             AND i.status IN ('approved', 'edited')
             AND i.approved_at IS NOT NULL
           ORDER BY i.intervention_type""",
        (issuer_id,),
    ).fetchall()

    stats: dict[str, dict] = {}
    for r in rows:
        t = r["intervention_type"]
        if t not in stats:
            stats[t] = {"total": 0, "with_response": 0}
        stats[t]["total"] += 1
        stats[t]["with_response"] += r["responded"]

    return [
        {
            "intervention_type": itype,
            "total_approved": v["total"],
            "followed_by_response_7d": v["with_response"],
            "response_rate_pct": round(v["with_response"] / v["total"] * 100) if v["total"] > 0 else 0,
        }
        for itype, v in stats.items()
    ]


# ── Issuers ────────────────────────────────────────────────────────────────────


def get_issuer(conn: sqlite3.Connection, issuer_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM issuers WHERE issuer_id = ?", (issuer_id,)).fetchone()
    return dict(row) if row else None


def insert_issuer(conn: sqlite3.Connection, issuer: dict) -> None:
    conn.execute(
        """INSERT OR IGNORE INTO issuers
           (issuer_id, name, typical_response_days, rofr_window_days, multi_layer_rofr, sector, created_at)
           VALUES (:issuer_id, :name, :typical_response_days, :rofr_window_days, :multi_layer_rofr, :sector, :created_at)""",
        issuer,
    )


def insert_party(conn: sqlite3.Connection, party: dict) -> None:
    conn.execute(
        """INSERT OR IGNORE INTO parties
           (party_id, party_type, display_name, is_first_time, prior_breakage_count, created_at)
           VALUES (:party_id, :party_type, :display_name, :is_first_time, :prior_breakage_count, :created_at)""",
        party,
    )


def insert_deal(conn: sqlite3.Connection, deal: dict[str, Any]) -> None:
    conn.execute(
        """INSERT OR IGNORE INTO deals
           (deal_id, issuer_id, buyer_id, seller_id, shares, price_per_share,
            stage, stage_entered_at, rofr_deadline, responsible_party,
            blockers, risk_factors, created_at, updated_at)
           VALUES (:deal_id, :issuer_id, :buyer_id, :seller_id, :shares, :price_per_share,
                   :stage, :stage_entered_at, :rofr_deadline, :responsible_party,
                   :blockers, :risk_factors, :created_at, :updated_at)""",
        deal,
    )
