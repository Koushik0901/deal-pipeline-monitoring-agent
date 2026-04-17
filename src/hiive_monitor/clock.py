"""
Clock abstraction — all timestamp reads go through this module.
No application code calls datetime.now() directly (FR-020).
Enforced by tests/unit/test_no_datetime_now.py.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...
    def advance(self, days: int) -> None: ...


class RealTimeClock:
    """Always returns wall-clock UTC. advance() is a no-op in real-time mode."""

    def now(self) -> datetime:
        return datetime.now(tz=timezone.utc)

    def advance(self, days: int) -> None:  # noqa: ARG002
        raise RuntimeError("Cannot advance a real-time clock.")


class SimulatedClock:
    """Deterministic injected clock. advance(days) increments the stored time."""

    def __init__(self, start: datetime | None = None) -> None:
        self._current: datetime = start or datetime(2025, 1, 15, 9, 0, 0, tzinfo=timezone.utc)

    def now(self) -> datetime:
        return self._current

    def advance(self, days: int) -> None:
        if days <= 0:
            raise ValueError(f"advance(days) requires days > 0, got {days}")
        from datetime import timedelta

        self._current += timedelta(days=days)

    def set(self, dt: datetime) -> None:
        """Set the clock to an arbitrary datetime (used by eval harness)."""
        self._current = dt


# Module-level singleton — replaced during tests or eval harness runs.
_clock: Clock | None = None


def get_clock() -> Clock:
    global _clock
    if _clock is None:
        mode = os.getenv("CLOCK_MODE", "real_time").lower()
        if mode == "simulated":
            _clock = SimulatedClock()
        else:
            _clock = RealTimeClock()
    return _clock


def set_clock(clock: Clock) -> None:
    """Inject a clock instance (used by eval harness and tests)."""
    global _clock
    _clock = clock


def now() -> datetime:
    """Convenience wrapper — the one permitted way to read current time."""
    return get_clock().now()
