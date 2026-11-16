"""M3 入口：列剖析、隔离区、冲突表、四账对账与页面。"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Request, Response

from ... import errors
from ...web import Ctx, get_ctx, render
from ..m5_batches import service as batch_service
from . import quarantine, rules, service

router = APIRouter(tags=["M3 质量剖析"])


def _batch_id(ctx: Ctx, batch_id: str | None) -> str:
    batch = ctx.current_batch(batch_id)
    if batch is None:
        raise errors.NotFound("还没有任何批次，请先跑一次接入", code="B_EMPTY")
    return batch["batch_id"]


@router.get("/api/profile")
def api_profile(batch_id: str | None = None, table: str = "", disposition: str = "",
                code: str = "", ctx: Ctx = Depends(get_ctx)) -> dict:
    resolved = _batch_id(ctx, batch_id)
    columns = ctx.store.load_columns(resolved)
    payload = service.view(columns, disposition=disposition, table=table, code=code)
    payload["batch_id"] = resolved
    payload["tables"] = [row["table_name"] for row in ctx.store.load_tables(resolved)]
    return payload


@router.get("/api/quarantine")
def api_quarantine(batch_id: str | None = None, code: str = "", level: str = "",
                   ctx: Ctx = Depends(get_ctx)) -> dict:
    resolved = _batch_id(ctx, batch_id)
    rows = ctx.store.load_quarantine(resolved)
    if code:
        rows = [row for row in rows if row["code"] == code]
    if level:
        rows = [row for row in rows if row["level"] == level]
    return {
        "batch_id": resolved,
        "rows": rows,
        "total": len(rows),
        "distribution": quarantine.distribution(rows),
        "conflicts": ctx.store.load_conflicts(resolved),
    }


@router.get("/api/quarantine/export.csv")
def api_quarantine_csv(batch_id: str | None = None,
                       ctx: Ctx = Depends(get_ctx)) -> Response:
    resolved = _batch_id(ctx, batch_id)
    rows = ctx.store.load_quarantine(resolved)
    body = quarantine.encode_csv(quarantine.to_csv_text(rows))
    return Response(content=body, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition":
                             f'attachment; filename="starmodel-quarantine-{resolved}.csv"'})


@router.get("/api/conflicts/export.csv")
def api_conflicts_csv(batch_id: str | None = None,
                      ctx: Ctx = Depends(get_ctx)) -> Response:
    resolved = _batch_id(ctx, batch_id)
    rows = ctx.store.load_conflicts(resolved)
    body = quarantine.encode_csv(quarantine.conflicts_to_csv_text(rows))
    return Response(content=body, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition":
                             f'attachment; filename="starmodel-conflicts-{resolved}.csv"'})


@router.get("/api/reconcile")
def api_reconcile(batch_id: str | None = None, ctx: Ctx = Depends(get_ctx)) -> dict:
    resolved = _batch_id(ctx, batch_id)
    return batch_service.accounts_of(ctx.store, resolved)


@router.get("/profile")
def page_profile(request: Request, table: str = "", disposition: str = "", code: str = "",
                 ctx: Ctx = Depends(get_ctx)):
    batch = ctx.current_batch()
    payload = {"rows": [], "column_total": 0, "adopted": 0, "pending": 0, "rejected": 0,
               "key_like": 0, "all_null": 0,
               "filters": {"table_name": table, "disposition": disposition, "code": code},
               "tables": []}
    if batch is not None:
        columns = ctx.store.load_columns(batch["batch_id"])
        payload = service.view(columns, disposition=disposition, table=table, code=code)
        payload["tables"] = [row["table_name"]
                             for row in ctx.store.load_tables(batch["batch_id"])]
    return render(request, "profile.html", page="profile", page_title="列剖析",
                  payload=payload, batch=batch)


def accounts_json(store, batch_id: str) -> dict:
    totals = store.load_totals(batch_id)
    return json.loads(totals.get("accounts_json") or "{}")


def rows_for_page(accounts: dict) -> list:
    return rules.rows_for_page(accounts)
