"""
Demo fast-forward: run 3 simulated ticks so the Daily Brief is populated
immediately when a reviewer opens the app. Each tick advances the simulated
clock by 1 day to show the agent reasoning about changing conditions.

Called by: make demo (after make seed)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from hiive_monitor import clock as clk
from hiive_monitor import logging as log_module
from hiive_monitor.clock import SimulatedClock
from hiive_monitor.db.init import init_checkpoint_db, init_domain_db
from hiive_monitor.llm.client import clear_cache


def run_demo_ticks(num_ticks: int = 3, start_days_offset: int = 0) -> None:
    log_module.configure_logging()
    logger = log_module.get_logger()

    # Start from the seed data's base date
    base = datetime(2026, 4, 16, 9, 0, tzinfo=UTC)
    sim_clock = SimulatedClock(start=base + timedelta(days=start_days_offset))
    clk.set_clock(sim_clock)

    init_domain_db()
    init_checkpoint_db()

    from hiive_monitor.agents.monitor import run_tick

    for i in range(num_ticks):
        logger.info("demo.tick_start", tick=i + 1, now=clk.now().isoformat())
        clear_cache()
        tick_id = run_tick(mode="simulated")
        logger.info("demo.tick_complete", tick=i + 1, tick_id=tick_id[:8])

        # Advance 1 day between ticks
        if i < num_ticks - 1:
            sim_clock.advance(1)

    print(f"\n✓ Ran {num_ticks} simulated ticks. Brief is populated.")
    print(f"  Final simulated time: {clk.now().strftime('%Y-%m-%d')}")


if __name__ == "__main__":
    run_demo_ticks()
