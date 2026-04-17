"""
Stretch-feature migrations — idempotent ALTER TABLE statements run only when
the corresponding feature flag is enabled. Safe to call on every startup.
"""

from __future__ import annotations

import sqlite3

from hiive_monitor.logging import get_logger


def stretch_migrations() -> None:
    """Run all enabled stretch-feature migrations."""
    from hiive_monitor.config import get_settings
    from hiive_monitor.db.connection import get_domain_conn

    settings = get_settings()
    log = get_logger()
    conn = get_domain_conn()

    if settings.enable_ts06_doc_tracking:
        try:
            conn.execute(
                "ALTER TABLE deals ADD COLUMN documents_received TEXT NOT NULL DEFAULT '[]'"
            )
            conn.commit()
            log.info("migration.ts06.applied", column="documents_received")
        except sqlite3.OperationalError:
            pass  # column already exists — idempotent

    conn.close()
