"""Shared fixtures for integration tests."""

from __future__ import annotations

import os
import pathlib
import sqlite3
import tempfile
import uuid
from datetime import datetime, timezone

import pytest


_SCHEMA = pathlib.Path(__file__).parent.parent.parent / "src" / "hiive_monitor" / "db" / "schema.sql"


@pytest.fixture()
def db_path(monkeypatch):
    """Temp domain DB with schema applied; sets DOMAIN_DB_PATH and resets settings."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name

    monkeypatch.setenv("DOMAIN_DB_PATH", path)
    monkeypatch.setenv("CHECKPOINT_DB_PATH", ":memory:")
    monkeypatch.setenv("CLOCK_MODE", "simulated")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    import hiive_monitor.config as _cfg
    _cfg._settings = None

    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(_SCHEMA.read_text(encoding="utf-8"))
    conn.commit()

    yield path, conn

    conn.close()
    try:
        os.unlink(path)
    except OSError:
        pass

    import hiive_monitor.config as _cfg
    _cfg._settings = None


def seed_parties(conn: sqlite3.Connection) -> tuple[str, str]:
    """Insert a buyer and seller party; return (buyer_id, seller_id)."""
    bid = "P-BUYER-001"
    sid = "P-SELLER-001"
    conn.execute(
        "INSERT OR IGNORE INTO parties (party_id, party_type, display_name, is_first_time, prior_breakage_count, created_at)"
        " VALUES (?, 'buyer', 'Test Buyer', 0, 0, datetime('now'))",
        (bid,),
    )
    conn.execute(
        "INSERT OR IGNORE INTO parties (party_id, party_type, display_name, is_first_time, prior_breakage_count, created_at)"
        " VALUES (?, 'seller', 'Test Seller', 0, 0, datetime('now'))",
        (sid,),
    )
    conn.commit()
    return bid, sid


def seed_issuer(conn: sqlite3.Connection, issuer_id: str = "I-001", name: str = "TestCo") -> None:
    conn.execute(
        "INSERT OR IGNORE INTO issuers"
        " (issuer_id, name, typical_response_days, rofr_window_days, multi_layer_rofr, created_at)"
        " VALUES (?, ?, 7, 45, 0, datetime('now'))",
        (issuer_id, name),
    )
    conn.commit()


def seed_deal(
    conn: sqlite3.Connection,
    deal_id: str = "D-001",
    issuer_id: str = "I-001",
    stage: str = "docs_pending",
    days_in_stage: int = 3,
    rofr_deadline_days: int | None = None,
) -> None:
    buyer_id, seller_id = seed_parties(conn)
    stage_entered = f"datetime('now', '-{days_in_stage} days')"
    rofr_clause = (
        f"date('now', '+{rofr_deadline_days} days')"
        if rofr_deadline_days is not None
        else "NULL"
    )
    conn.execute(
        f"""INSERT OR IGNORE INTO deals
            (deal_id, issuer_id, buyer_id, seller_id, shares, price_per_share,
             stage, stage_entered_at, rofr_deadline, responsible_party,
             blockers, risk_factors, created_at, updated_at)
            VALUES (?, ?, ?, ?, 1000, 25.00, ?, {stage_entered}, {rofr_clause},
                    'hiive_ts', '[]', '{{}}', datetime('now'), datetime('now'))""",
        (deal_id, issuer_id, buyer_id, seller_id, stage),
    )
    conn.commit()


def seed_tick(conn: sqlite3.Connection, tick_id: str) -> None:
    """Insert a completed tick row so FK constraints on agent_observations are satisfied."""
    conn.execute(
        """INSERT OR IGNORE INTO ticks (tick_id, mode, tick_started_at, tick_completed_at)
           VALUES (?, 'simulated', datetime('now', '-2 minutes'), datetime('now', '-1 minute'))""",
        (tick_id,),
    )
    conn.commit()


def seed_event(
    conn: sqlite3.Connection,
    deal_id: str,
    event_type: str,
    minutes_ago: int = 1,
    payload: dict | None = None,
) -> str:
    import json
    eid = str(uuid.uuid4())
    conn.execute(
        f"""INSERT INTO events
            (event_id, deal_id, event_type, occurred_at, created_at, summary, payload)
            VALUES (?, ?, ?, datetime('now', '-{minutes_ago} minutes'),
                    datetime('now', '-{minutes_ago} minutes'), '', ?)""",
        (eid, deal_id, event_type, json.dumps(payload or {})),
    )
    conn.commit()
    return eid
