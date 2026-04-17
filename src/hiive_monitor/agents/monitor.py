"""
Pipeline Monitor — supervisor LangGraph graph.

Runs on each scheduler tick:
  1. load_live_deals  — fetch all live deal_ids from domain DB
  2. screen_with_haiku — Haiku attention score per deal (parallelised)
  3. apply_suppression — discount deals with recent agent-recommended comms (FR-LOOP-02)
  4. select_investigation_queue — threshold 0.6, top-5 cap (FR-001a)
  5. fan_out_investigators — Send API fan-out to Deal Investigator sub-graphs
  6. close_tick — mark tick complete in DB

Idempotency (FR-024): tick_id written atomically at start; re-entrant if graph
interrupted mid-run because UNIQUE(tick_id, deal_id) prevents double-write.
"""

from __future__ import annotations

import uuid
from typing import Any

from langgraph.graph import END, START, StateGraph

from hiive_monitor import clock as clk
from hiive_monitor import logging as log_module
from hiive_monitor.agents.graph_state import (
    ErrorRecord,
    InvestigatorResult,
    MonitorState,
)
from hiive_monitor.agents.brief_composer import compose_daily_brief
from hiive_monitor.config import get_settings
from hiive_monitor.db.connection import get_domain_conn
from hiive_monitor.db import dao
from hiive_monitor.llm import client as llm_client
from hiive_monitor.llm.client import call_structured
from hiive_monitor.llm.prompts.screening import (
    SCREENING_OUTPUT,
    SCREENING_TEMPLATE,
    build_screening_prompt,
)
from hiive_monitor.models.snapshot import DealSnapshot, Blocker, EventRef
from hiive_monitor.models.stages import DWELL_BASELINES, REQUIRED_DOCUMENTS_BY_STAGE, Stage

import json
from datetime import timezone


# ── Snapshot builder ──────────────────────────────────────────────────────────


def _build_snapshot(deal: dict, conn) -> DealSnapshot:
    """Construct a DealSnapshot from a raw deals row + recent events."""
    from hiive_monitor.db import dao as _dao
    now = clk.now()

    stage = Stage(deal["stage"])
    stage_entered_at = _parse_dt(deal["stage_entered_at"])
    days_in_stage = max(0, (now - stage_entered_at).days)

    rofr_deadline = _parse_dt(deal["rofr_deadline"]) if deal.get("rofr_deadline") else None
    days_to_rofr = max(0, (rofr_deadline - now).days) if rofr_deadline else None

    blockers_raw = json.loads(deal.get("blockers") or "[]")
    blockers = []
    for b in blockers_raw:
        try:
            blockers.append(
                Blocker(kind=b["kind"], description=b["description"], since=_parse_dt(b["since"]))
            )
        except Exception:
            pass

    risk_factors = json.loads(deal.get("risk_factors") or "{}")

    events_raw = _dao.get_events(conn, deal["deal_id"], limit=5)
    recent_events = [
        EventRef(
            event_type=e["event_type"],
            occurred_at=_parse_dt(e["occurred_at"]),
            summary=e.get("summary", ""),
        )
        for e in events_raw
    ]

    # Days since last comm
    comm_events = [
        e for e in events_raw
        if e["event_type"] in ("comm_outbound", "comm_inbound", "comm_sent_agent_recommended")
    ]
    days_since_last_comm = None
    if comm_events:
        last_comm = _parse_dt(comm_events[-1]["occurred_at"])
        days_since_last_comm = max(0, (now - last_comm).days)

    # Issuer name from issuers table
    issuer = _dao.get_issuer(conn, deal["issuer_id"])
    issuer_name = issuer["name"] if issuer else deal["issuer_id"]

    # TS06: compute missing documents when feature is enabled
    missing_documents: list[str] = []
    from hiive_monitor.config import get_settings as _gs
    if _gs().enable_ts06_doc_tracking:
        required = REQUIRED_DOCUMENTS_BY_STAGE.get(stage, [])
        try:
            received = set(json.loads(deal.get("documents_received") or "[]"))
        except (ValueError, TypeError):
            received = set()
        missing_documents = [d for d in required if d not in received]

    return DealSnapshot(
        deal_id=deal["deal_id"],
        issuer_id=deal["issuer_id"],
        issuer_name=issuer_name,
        stage=stage,
        stage_entered_at=stage_entered_at,
        days_in_stage=days_in_stage,
        rofr_deadline=rofr_deadline,
        days_to_rofr=days_to_rofr,
        responsible_party=deal.get("responsible_party", "hiive_ts"),
        blockers=blockers,
        risk_factors=risk_factors,
        recent_events=recent_events,
        days_since_last_comm=days_since_last_comm,
        missing_documents=missing_documents,
    )


def _parse_dt(value: str | None):
    from datetime import datetime
    if not value:
        return clk.now()
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return clk.now()


# ── Graph nodes ───────────────────────────────────────────────────────────────


def load_live_deals(state: MonitorState) -> dict:
    conn = get_domain_conn()
    deals = dao.get_live_deals(conn)
    conn.close()
    log_module.get_logger().info(
        "monitor.load_deals", tick_id=state["tick_id"], count=len(deals)
    )
    return {"live_deals": deals, "candidate_deals": [d["deal_id"] for d in deals]}


def screen_with_haiku(state: MonitorState) -> dict:
    """Score all live deals with Haiku. One LLM call per deal (sequential)."""
    settings = get_settings()
    conn = get_domain_conn()
    deal_map = {d["deal_id"]: d for d in state["live_deals"]}

    scores: dict[str, float] = {}
    errors: list[ErrorRecord] = []

    for deal_id in state["candidate_deals"]:
        deal = deal_map.get(deal_id)
        if not deal:
            continue
        try:
            snapshot = _build_snapshot(deal, conn)
            result = call_structured(
                template=SCREENING_TEMPLATE,
                template_vars=build_screening_prompt(snapshot),
                output_model=SCREENING_OUTPUT,
                model=settings.slm_model,
                tick_id=state["tick_id"],
                deal_id=deal_id,
                call_name="screen_deal",
            )
            scores[deal_id] = result.score if result else 0.0
        except Exception as e:
            errors.append(ErrorRecord(deal_id=deal_id, error=str(e), node="screen_with_haiku"))
            scores[deal_id] = 0.0

    conn.close()
    log_module.get_logger().info(
        "monitor.screened", tick_id=state["tick_id"], count=len(scores)
    )
    return {"attention_scores": scores, "errors": errors}


def apply_suppression(state: MonitorState) -> dict:
    """
    Discount attention scores for deals with recent agent-recommended comms (FR-LOOP-02).
    Uses SUPPRESSION_TICKS and SUPPRESSION_MULTIPLIER from settings.
    """
    settings = get_settings()
    conn = get_domain_conn()
    deal_ids = list(state["attention_scores"].keys())
    suppress_set = dao.get_suppressed_deal_ids(
        conn, deal_ids, within_ticks=settings.suppression_ticks
    )
    conn.close()

    suppressed: dict[str, float] = {}
    for deal_id, raw_score in state["attention_scores"].items():
        if deal_id in suppress_set:
            suppressed[deal_id] = raw_score * settings.suppression_multiplier
            log_module.get_logger().debug(
                "monitor.suppressed", deal_id=deal_id, raw=raw_score,
                suppressed=suppressed[deal_id],
            )
        else:
            suppressed[deal_id] = raw_score

    return {"suppressed_scores": suppressed}


def select_investigation_queue(state: MonitorState) -> dict:
    """
    Pick deals above threshold (0.6) by suppressed score, capped at top 5.
    Ties broken by days_to_rofr ascending then days_in_stage descending (FR-001a).
    """
    threshold = get_settings().attention_threshold
    eligible = {
        deal_id: score
        for deal_id, score in state["suppressed_scores"].items()
        if score >= threshold
    }

    # Sort: highest score first, cap at 5
    ranked = sorted(eligible.keys(), key=lambda d: eligible[d], reverse=True)[:5]

    log_module.get_logger().info(
        "monitor.queue_selected",
        tick_id=state["tick_id"],
        queue=ranked,
        threshold=threshold,
    )
    return {"investigation_queue": ranked}


def fan_out_investigators(state: MonitorState) -> dict:
    """Invoke Deal Investigator for each queued deal (sequential for reliability)."""
    from hiive_monitor.agents.investigator import get_investigator_graph

    graph = get_investigator_graph()
    conn = get_domain_conn()
    deal_map = {d["deal_id"]: d for d in state["live_deals"]}
    results: list[InvestigatorResult] = []
    errors: list[ErrorRecord] = []

    for deal_id in state["investigation_queue"]:
        deal = deal_map.get(deal_id)
        if not deal:
            continue
        try:
            snapshot = _build_snapshot(deal, conn)
            initial: dict = {
                "tick_id": state["tick_id"],
                "deal_id": deal_id,
                "deal_snapshot": snapshot,
                "risk_signals": [],
                "severity": None,
                "severity_reasoning": None,
                "enrichment_count": 0,
                "enrichment_chain": [],
                "enrichment_context": {},
                "intervention": None,
                "observation_id": None,
                "error": None,
            }
            # thread_id scoped per (tick, deal) so checkpointer isolates runs
            config = {"configurable": {"thread_id": f"{state['tick_id']}:{deal_id}"}}
            final = graph.invoke(initial, config=config)
            severity_val = final.get("severity")
            results.append(
                InvestigatorResult(
                    deal_id=deal_id,
                    severity=severity_val.value if severity_val else None,
                    observation_id=final.get("observation_id"),
                    intervention_id=None,
                    error=final.get("error"),
                )
            )
        except Exception as e:
            errors.append(
                ErrorRecord(deal_id=deal_id, error=str(e), node="fan_out_investigators")
            )
            log_module.get_logger().error(
                "investigator.invocation_error", deal_id=deal_id, error=str(e)
            )

    conn.close()
    log_module.get_logger().info(
        "monitor.investigators_complete",
        tick_id=state["tick_id"],
        count=len(results),
    )
    return {"investigator_results": results, "errors": errors}


def close_tick(state: MonitorState) -> dict:
    conn = get_domain_conn()
    results = state.get("investigator_results", [])
    errors = state.get("errors", [])
    dao.complete_tick(
        conn,
        state["tick_id"],
        deals_screened=len(state.get("candidate_deals", [])),
        deals_investigated=len(results),
        errors=[e["error"] for e in errors],
    )
    conn.close()
    log_module.get_logger().info(
        "monitor.tick_complete",
        tick_id=state["tick_id"],
        investigated=len(results),
        errors=len(errors),
    )

    try:
        compose_daily_brief(state["tick_id"])
    except Exception as exc:
        # Swallow: a failed brief must not fail the tick that already persisted results.
        log_module.get_logger().warning(
            "monitor.brief_composition_error", tick_id=state["tick_id"], error=str(exc)
        )

    llm_client.evict_tick(state["tick_id"])
    return {}


# ── Graph construction ────────────────────────────────────────────────────────


def build_monitor_graph():
    g = StateGraph(MonitorState)
    g.add_node("load_live_deals", load_live_deals)
    g.add_node("screen_with_haiku", screen_with_haiku)
    g.add_node("apply_suppression", apply_suppression)
    g.add_node("select_investigation_queue", select_investigation_queue)
    g.add_node("fan_out_investigators", fan_out_investigators)
    g.add_node("close_tick", close_tick)

    g.add_edge(START, "load_live_deals")
    g.add_edge("load_live_deals", "screen_with_haiku")
    g.add_edge("screen_with_haiku", "apply_suppression")
    g.add_edge("apply_suppression", "select_investigation_queue")
    g.add_edge("select_investigation_queue", "fan_out_investigators")
    g.add_edge("fan_out_investigators", "close_tick")
    g.add_edge("close_tick", END)

    return g.compile()


_monitor_graph = None


def _get_monitor_graph():
    global _monitor_graph
    if _monitor_graph is None:
        _monitor_graph = build_monitor_graph()
    return _monitor_graph


# ── Public entry point ────────────────────────────────────────────────────────


def run_tick(mode: str = "simulated", tick_id: str | None = None) -> str:
    """
    Execute one full monitoring tick. Idempotent: if tick_id already exists in
    ticks table (completed), this call is a no-op. Returns the tick_id.
    """
    conn = get_domain_conn()
    tid = tick_id or str(uuid.uuid4())
    log_module.bind_tick(tid)

    # Idempotency check (FR-024)
    existing = dao.get_tick(conn, tid)
    if existing and existing.get("tick_completed_at"):
        conn.close()
        log_module.get_logger().info("monitor.tick_already_complete", tick_id=tid)
        return tid

    dao.start_tick(conn, mode=mode, tick_id=tid)
    conn.close()

    log_module.get_logger().info("monitor.tick_start", tick_id=tid, mode=mode)

    graph = _get_monitor_graph()
    initial_state: MonitorState = {
        "tick_id": tid,
        "mode": mode,
        "tick_started_at": clk.now().isoformat(),
        "live_deals": [],
        "candidate_deals": [],
        "attention_scores": {},
        "suppressed_scores": {},
        "investigation_queue": [],
        "investigator_results": [],
        "errors": [],
    }

    graph.invoke(initial_state)
    log_module.clear_correlation_ids()
    return tid
