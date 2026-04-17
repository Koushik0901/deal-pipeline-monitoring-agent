"""Reviewer Queue page route."""

from __future__ import annotations

from fastapi import APIRouter, Request

from hiive_monitor.db import dao
from hiive_monitor.db.connection import get_domain_conn

router = APIRouter()


@router.get("/queue")
async def queue(request: Request, severity: str = "", stage: str = "", issuer: str = ""):
    from hiive_monitor.app import templates

    conn = get_domain_conn()
    open_ivs = dao.get_open_interventions(conn)
    items = []
    for iv in open_ivs:
        deal = dao.get_deal(conn, iv["deal_id"])
        if severity and iv.get("severity") != severity:
            continue
        if stage and deal and deal.get("stage") != stage:
            continue
        if issuer and deal and deal.get("issuer_id") != issuer:
            continue
        items.append({**iv, "deal": deal})
    conn.close()

    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(
            request, "_all_open_list.html",
            {"items": items, "severity": severity, "stage": stage, "issuer": issuer},
        )

    return templates.TemplateResponse(
        request, "queue.html",
        {"items": items, "severity": severity, "stage": stage, "issuer": issuer},
    )
