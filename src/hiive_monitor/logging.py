"""
Structlog configuration.
Human-readable in dev (LOG_FORMAT=human), JSON in production (LOG_FORMAT=json).
Provides correlation-ID contextvars for tick_id and deal_id (FR-026, FR-027).
"""

from __future__ import annotations

import contextvars
import json
import logging
import pathlib
import sys
import threading

import structlog

_tick_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "tick_id", default=None
)
_deal_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "deal_id", default=None
)

_jsonl_path: pathlib.Path | None = None
_jsonl_lock = threading.Lock()


def _jsonl_tee(logger, method_name, event_dict):  # noqa: ARG001
    if _jsonl_path is not None:
        record = {"level": method_name, **event_dict}
        try:
            with _jsonl_lock:
                _jsonl_path.parent.mkdir(parents=True, exist_ok=True)
                with _jsonl_path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(record, default=str) + "\n")
        except OSError:
            pass
    return event_dict


def _add_correlation_ids(logger, method_name, event_dict):  # noqa: ARG001
    tick_id = _tick_id_var.get()
    deal_id = _deal_id_var.get()
    if tick_id:
        event_dict["tick_id"] = tick_id
    if deal_id:
        event_dict["deal_id"] = deal_id
    return event_dict


def configure_logging(log_format: str = "human", logs_path: str | None = None) -> None:
    global _jsonl_path
    _jsonl_path = pathlib.Path(logs_path) if logs_path else None

    # Force UTF-8 on stdout/stderr so Windows console (cp1252) does not crash
    # the logging call when an event payload contains non-ASCII (e.g. ≤, ①).
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    shared_processors = [
        structlog.contextvars.merge_contextvars,
        _add_correlation_ids,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        _jsonl_tee,
    ]

    if log_format == "json":
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer()

    structlog.configure(
        processors=shared_processors + [renderer],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str = "hiive") -> structlog.BoundLogger:
    return structlog.get_logger(name)


def bind_tick(tick_id: str) -> None:
    _tick_id_var.set(tick_id)


def bind_deal(deal_id: str) -> None:
    _deal_id_var.set(deal_id)


def clear_correlation_ids() -> None:
    _tick_id_var.set(None)
    _deal_id_var.set(None)


def get_log_records(tick_id: str | None = None, deal_id: str | None = None) -> list[dict]:
    """Read JSONL log file, optionally filtered by tick_id or deal_id."""
    if _jsonl_path is None or not _jsonl_path.exists():
        return []
    records: list[dict] = []
    with _jsonl_lock:
        with _jsonl_path.open("r", encoding="utf-8") as f:
            lines = f.readlines()
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if tick_id and record.get("tick_id") != tick_id:
            continue
        if deal_id and record.get("deal_id") != deal_id:
            continue
        records.append(record)
    return records
