"""SQLite connection factories — domain DB and LangGraph checkpoint DB are kept separate."""

from __future__ import annotations

import sqlite3

from hiive_monitor.config import get_settings


def get_domain_conn() -> sqlite3.Connection:
    settings = get_settings()
    conn = sqlite3.connect(settings.domain_db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def get_checkpoint_conn_string() -> str:
    settings = get_settings()
    return f"sqlite:///{settings.checkpoint_db_path}"
