"""M7 入口：影响评估接口、提交变更试算、影响评估页。"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Request

from ... import errors
from ...web import Ctx, get_ctx, render
from . import impact, service

router = APIRouter(tags=["M7 建模影响评估"])


def _batch_id(ctx: Ctx, batch_id: str | None) -> str:
    batch = ctx.current_batch(batch_id)
    if batch is None:
        raise errors.NotFound("还没有任何批次，请先跑一次接入", code="B_EMPTY")
    return batch["batch_id"]


@router.get("/api/impact")
def api_impact(batch_id: str | None = None, ctx: Ctx = Depends(get_ctx)) -> dict:
    resolved = _batch_id(ctx, batch_id)
    return service.build(ctx.settings, ctx.store, resolved)


@router.post("/api/impact/propose")
def api_propose(batch_id: str | None = None,
                payload: dict = Body(default_factory=dict),
                ctx: Ctx = Depends(get_ctx)) -> dict:
    resolved = _batch_id(ctx, batch_id)
    candidate = payload.get("changes") if isinstance(payload, dict) else None
    return service.propose(ctx.settings, ctx.store, resolved, candidate)


@router.get("/impact")
def page_impact(request: Request, ctx: Ctx = Depends(get_ctx)):
    batch = ctx.current_batch()
    payload = None
    if batch is not None:
        payload = service.build(ctx.settings, ctx.store, batch["batch_id"])
    return render(request, "impact.html", page="impact", page_title="影响评估",
                  payload=payload, batch=batch, kinds=impact.KINDS,
                  impact_levels=impact.LEVEL_LABELS)
