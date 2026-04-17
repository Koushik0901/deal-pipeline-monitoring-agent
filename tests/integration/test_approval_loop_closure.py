"""Integration tests for the intervention approval/dismiss loop (FR-LOOP-01, T069b)."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from tests.integration.conftest import seed_deal, seed_issuer


def _seed_pending_intervention(conn, deal_id: str = "D-0042") -> tuple[str, str]:
    """Seed an observation + pending intervention. Returns (observation_id, intervention_id)."""
    oid = str(uuid.uuid4())
    iid = str(uuid.uuid4())
    tick_id = str(uuid.uuid4())
    conn.execute(
        """INSERT INTO ticks (tick_id, mode, tick_started_at, tick_completed_at)
           VALUES (?, 'simulated', datetime('now', '-5 minutes'), datetime('now', '-4 minutes'))""",
        (tick_id,),
    )
    conn.execute(
        """INSERT INTO agent_observations
           (observation_id, tick_id, deal_id, observed_at, severity, dimensions_evaluated, reasoning)
           VALUES (?, ?, ?, datetime('now', '-3 minutes'), 'act', '[]', '{}')""",
        (oid, tick_id, deal_id),
    )
    conn.execute(
        """INSERT INTO interventions
           (intervention_id, deal_id, observation_id, intervention_type,
            draft_subject, draft_body, created_at)
           VALUES (?, ?, ?, 'outbound_nudge', 'Follow-up', 'Please advise.', datetime('now', '-3 minutes'))""",
        (iid, deal_id, oid),
    )
    conn.commit()
    return oid, iid


# ── (1) approve path ──────────────────────────────────────────────────────────


def test_approve_creates_comm_event(db_path):
    """
    Approving an intervention must insert exactly one comm_sent_agent_recommended
    event referencing the intervention_id. (FR-LOOP-01)
    """
    path, conn = db_path
    seed_issuer(conn)
    seed_deal(conn, "D-0042")
    oid, iid = _seed_pending_intervention(conn)

    from hiive_monitor.db import dao
    from hiive_monitor import clock as clk

    sim_ts = clk.now()
    event_id = dao.approve_intervention_atomic(conn, iid, simulated_timestamp=sim_ts)

    # Intervention status → approved
    iv = dao.get_intervention(conn, iid)
    assert iv["status"] == "approved"

    # Exactly one comm_sent_agent_recommended event
    events = conn.execute(
        "SELECT * FROM events WHERE deal_id = 'D-0042' AND event_type = 'comm_sent_agent_recommended'"
    ).fetchall()
    assert len(events) == 1
    event = dict(events[0])
    assert event["event_id"] == event_id

    # Payload references the intervention_id
    payload = json.loads(event["payload"])
    assert payload.get("intervention_id") == iid

    # occurred_at matches the simulated timestamp (to the second)
    assert event["occurred_at"].startswith(sim_ts.isoformat()[:19])


# ── (2) event-insert failure rolls back entirely ──────────────────────────────


def test_approve_rollback_on_event_insert_failure(db_path, monkeypatch):
    """
    If the event INSERT inside the transaction raises (simulated via pre-seeding a
    duplicate event_id), `with conn:` rolls back; intervention status stays 'pending'.
    (FR-LOOP-01)
    """
    path, conn = db_path
    seed_issuer(conn)
    seed_deal(conn, "D-0042")
    oid, iid = _seed_pending_intervention(conn)

    from hiive_monitor.db import dao
    from hiive_monitor import clock as clk

    # Pre-seed an event with a known UUID so the INSERT inside approve_intervention_atomic
    # will collide on the PRIMARY KEY, triggering a rollback via `with conn:`.
    fixed_event_id = "fixed-event-id-for-rollback-test"
    conn.execute(
        """INSERT INTO events (event_id, deal_id, event_type, occurred_at, created_at, summary, payload)
           VALUES (?, 'D-0042', 'comm_outbound', datetime('now'), datetime('now'), 'pre-seeded', '{}')""",
        (fixed_event_id,),
    )
    conn.commit()

    # Patch uuid.uuid4 in dao to return the fixed ID — next call to approve will collide
    import uuid as _uuid_mod
    original_uuid4 = _uuid_mod.uuid4
    call_count = [0]

    def _fixed_uuid():
        call_count[0] += 1
        if call_count[0] == 1:
            return uuid.UUID(fixed_event_id[:36].ljust(36, "0").replace("-", "").ljust(32, "0"))
        return original_uuid4()

    monkeypatch.setattr(_uuid_mod, "uuid4", _fixed_uuid)

    with pytest.raises(Exception):
        dao.approve_intervention_atomic(conn, iid, simulated_timestamp=clk.now())

    monkeypatch.setattr(_uuid_mod, "uuid4", original_uuid4)

    # Transaction rolled back — status still pending
    iv = dao.get_intervention(conn, iid)
    assert iv["status"] == "pending", "intervention must remain pending after rollback"

    # No comm_sent_agent_recommended event created (only the pre-seeded comm_outbound)
    events = conn.execute(
        "SELECT * FROM events WHERE deal_id = 'D-0042' AND event_type = 'comm_sent_agent_recommended'"
    ).fetchall()
    assert len(events) == 0


# ── (3) dismiss path emits zero events ───────────────────────────────────────


def test_dismiss_emits_no_events(db_path):
    """
    Dismissing an intervention must mark it 'dismissed' and emit zero comm events.
    (FR-LOOP-01)
    """
    path, conn = db_path
    seed_issuer(conn)
    seed_deal(conn, "D-0042")
    oid, iid = _seed_pending_intervention(conn)

    from hiive_monitor.db import dao

    dao.update_intervention_status(conn, iid, status="dismissed")

    iv = dao.get_intervention(conn, iid)
    assert iv["status"] == "dismissed"

    events = conn.execute(
        "SELECT * FROM events WHERE deal_id = 'D-0042' AND event_type = 'comm_sent_agent_recommended'"
    ).fetchall()
    assert len(events) == 0
