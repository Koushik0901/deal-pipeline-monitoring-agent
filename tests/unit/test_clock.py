"""
Clock abstraction tests (FR-020).
Covers: SimulatedClock injection, advance(), set_clock(), no datetime.now() in src/.
"""

from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timezone, timedelta

import pytest

from hiive_monitor import clock as clk
from hiive_monitor.clock import SimulatedClock, RealTimeClock, set_clock


def _fixed_clock(dt: datetime) -> SimulatedClock:
    c = SimulatedClock(start=dt)
    return c


class TestSimulatedClock:
    def test_returns_injected_time(self):
        base = datetime(2026, 4, 16, 9, 0, tzinfo=timezone.utc)
        c = _fixed_clock(base)
        assert c.now() == base

    def test_advance_increments_days(self):
        base = datetime(2026, 4, 16, 9, 0, tzinfo=timezone.utc)
        c = _fixed_clock(base)
        c.advance(3)
        assert c.now() == base + timedelta(days=3)

    def test_advance_rejects_zero_or_negative(self):
        c = _fixed_clock(datetime(2026, 1, 1, tzinfo=timezone.utc))
        with pytest.raises(ValueError):
            c.advance(0)
        with pytest.raises(ValueError):
            c.advance(-1)

    def test_set_overrides_current_time(self):
        c = _fixed_clock(datetime(2026, 1, 1, tzinfo=timezone.utc))
        new_time = datetime(2027, 6, 15, 12, 0, tzinfo=timezone.utc)
        c.set(new_time)
        assert c.now() == new_time


class TestSetClock:
    def test_set_clock_affects_clk_now(self):
        base = datetime(2026, 4, 16, 9, 0, tzinfo=timezone.utc)
        c = _fixed_clock(base)
        original = clk._clock
        try:
            set_clock(c)
            assert clk.now() == base
        finally:
            set_clock(original)  # restore

    def test_real_time_clock_returns_recent_datetime(self):
        c = RealTimeClock()
        t = c.now()
        assert t.tzinfo is not None
        # Should be within the last 10 seconds of wall time
        from datetime import datetime as dt
        assert abs((dt.now(tz=timezone.utc) - t).total_seconds()) < 10

    def test_real_time_clock_advance_raises(self):
        c = RealTimeClock()
        with pytest.raises(RuntimeError):
            c.advance(1)


class TestClockDiscipline:
    """FR-020: No source file may call datetime.now() directly."""

    def test_no_datetime_now_in_src(self):
        result = subprocess.run(
            [sys.executable, "-m", "grep", "-rn", r"datetime\.now()", "src/"],
            capture_output=True,
            text=True,
            cwd=sys.path[0] if sys.path[0] else ".",
        )
        # grep returns exit code 1 if no match (good), 0 if match found (bad)
        # Use rg/grep via shell for cross-platform
        import subprocess as sp
        import pathlib

        src_dir = pathlib.Path(__file__).parent.parent.parent / "src"
        py_files = list(src_dir.rglob("*.py"))
        violations = []
        for f in py_files:
            text = f.read_text(encoding="utf-8", errors="ignore")
            for i, line in enumerate(text.splitlines(), 1):
                # Allow in clock.py itself (RealTimeClock.now implementation)
                if "datetime.now(" in line and "__pycache__" not in str(f):
                    if f.name != "clock.py":
                        violations.append(f"{f.relative_to(src_dir.parent)}:{i}: {line.strip()}")
        assert violations == [], (
            f"Found datetime.now() calls outside clock.py:\n" + "\n".join(violations)
        )
