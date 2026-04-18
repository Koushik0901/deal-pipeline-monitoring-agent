"""Deal detail route — GET /deals/{deal_id}."""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Request

from hiive_monitor.db import dao
from hiive_monitor.db.connection import get_domain_conn

router = APIRouter()


def _templates():
    from hiive_monitor.app import templates as _t
    return _t


@router.get("/deals/{deal_id}")
async def deal_detail(request: Request, deal_id: str, debug: str = ""):
    conn = get_domain_conn()
    deal = dao.get_deal(conn, deal_id)
    if deal is None:
        conn.close()
        raise HTTPException(status_code=404, detail=f"Deal {deal_id!r} not found.")

    issuer = dao.get_issuer(conn, deal["issuer_id"]) if deal.get("issuer_id") else None
    events = dao.get_events(conn, deal_id)
    interventions = dao.get_interventions(conn, deal_id)

    raw_obs = dao.get_observations(conn, deal_id)
    observations = [
        {**obs, "reasoning_parsed": json.loads(obs["reasoning"]) if obs.get("reasoning") else {}}
        for obs in raw_obs
    ]
    conn.close()

    return _templates().TemplateResponse(
        request,
        "deal_detail.html",
        {
            "deal": deal,
            "issuer": issuer,
            "events": events,
            "interventions": interventions,
            "observations": observations,
            "debug": debug,
        },
    )
