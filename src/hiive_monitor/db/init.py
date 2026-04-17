"""
Initialise the domain database from schema.sql.
Run via: python -m hiive_monitor.db.init
"""

from __future__ import annotations

import pathlib

from hiive_monitor.db.connection import get_domain_conn
from hiive_monitor.logging import configure_logging, get_logger

_SCHEMA = pathlib.Path(__file__).parent / "schema.sql"


def init_domain_db() -> None:
    log = get_logger()
    conn = get_domain_conn()
    schema_sql = _SCHEMA.read_text(encoding="utf-8")
    # SQLite executescript handles multiple statements separated by semicolons.
    conn.executescript(schema_sql)
    conn.commit()
    conn.close()
    log.info("domain.db initialised", schema=str(_SCHEMA))


def init_checkpoint_db() -> None:
    """LangGraph SqliteSaver creates its own tables on first use; we just ensure the file exists."""
    from hiive_monitor.config import get_settings

    db_path = get_settings().checkpoint_db_path
    if db_path != ":memory:":
        path = pathlib.Path(db_path)
        path.touch(exist_ok=True)
    get_logger().info("agent_checkpoints.db ready", path=db_path)


if __name__ == "__main__":
    configure_logging()
    init_domain_db()
    init_checkpoint_db()
    print("Database initialisation complete.")
