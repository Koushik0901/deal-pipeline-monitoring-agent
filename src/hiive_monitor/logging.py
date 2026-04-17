"""
Structlog configuration.
Human-readable in dev (LOG_FORMAT=human), JSON in production (LOG_FORMAT=json).
Provides correlation-ID contextvars for tick_id and deal_id (FR-026, FR-027).
"""

from __future__ import annotations

import contextvars
import logging
import sys

import structlog

_tick_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "tick_id", default=None
)
_deal_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "deal_id", default=None
)


def _add_correlation_ids(logger, method_name, event_dict):  # noqa: ARG001
    tick_id = _tick_id_var.get()
    deal_id = _deal_id_var.get()
    if tick_id:
        event_dict["tick_id"] = tick_id
    if deal_id:
        event_dict["deal_id"] = deal_id
    return event_dict


def configure_logging(log_format: str = "human") -> None:
    shared_processors = [
        structlog.contextvars.merge_contextvars,
        _add_correlation_ids,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
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
