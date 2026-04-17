"""Daily brief composer — ranks a tick's observations into an analyst brief."""

from __future__ import annotations

import json

from hiive_monitor import clock as clk
from hiive_monitor import logging as log_module
from hiive_monitor.config import get_settings
from hiive_monitor.db import dao
from hiive_monitor.db.connection import get_domain_conn
from hiive_monitor.llm import client as llm_client
from hiive_monitor.llm.prompts.daily_brief import (
    DAILY_BRIEF_OUTPUT,
    DAILY_BRIEF_TEMPLATE,
    build_daily_brief_prompt,
)
from hiive_monitor.models.brief import DailyBrief


def compose_daily_brief(tick_id: str) -> DailyBrief | None:
    """Rank this tick's observations + open interventions into a DailyBrief."""
    settings = get_settings()
    conn = get_domain_conn()

    observations = dao.get_observations_by_tick(conn, tick_id)
    open_interventions = dao.get_open_interventions(conn)

    for obs in observations:
        raw = obs.get("reasoning")
        try:
            obs["reasoning"] = json.loads(raw) if raw else {}
        except (ValueError, TypeError):
            obs["reasoning"] = {}

    conn.close()

    generated_at = clk.now().isoformat()

    if not observations and not open_interventions:
        log_module.get_logger().info(
            "brief_composer.skip_empty_pipeline",
            tick_id=tick_id,
        )
        return DailyBrief(tick_id=tick_id, generated_at=generated_at, items=[])

    brief = llm_client.call_structured(
        template=DAILY_BRIEF_TEMPLATE,
        template_vars=build_daily_brief_prompt(tick_id, generated_at, observations, open_interventions),
        output_model=DAILY_BRIEF_OUTPUT,
        model=settings.llm_model,
        tick_id=tick_id,
        deal_id="__brief__",
        call_name="compose_daily_brief",
    )

    if brief is None:
        log_module.get_logger().warning(
            "brief_composer.llm_failed",
            tick_id=tick_id,
            observations=len(observations),
        )
        return None

    # Ensure tick_id and generated_at are set from our context, not LLM output
    brief = brief.model_copy(update={"tick_id": tick_id, "generated_at": generated_at})

    log_module.get_logger().info(
        "brief_composer.composed",
        tick_id=tick_id,
        items=len(brief.items),
        severities=[i.severity.value for i in brief.items],
    )
    return brief
