"""M8 入口：双时间总览接口、上游更新试算、双时间页面。"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Request

from ... import errors
from ...web import Ctx, get_ctx, render
from . import service

router = APIRouter(tags=["M8 双时间建模"])


def _batch_id(ctx: Ctx, batch_id: str | None) -> str:
    batch = ctx.current_batch(batch_id)
    if batch is None:
        raise errors.NotFound("还没有任何批次，请先跑一次接入", code="B_EMPTY")
    return batch["batch_id"]


@router.get("/api/temporal")
def api_temporal(batch_id: str | None = None, as_of: str | None = None,
                 ctx: Ctx = Depends(get_ctx)) -> dict:
    resolved = _batch_id(ctx, batch_id)
    return service.build(ctx.settings, ctx.store, resolved, as_of=as_of)


@router.post("/api/temporal/propose")
def api_temporal_propose(batch_id: str | None = None,
                         payload: dict = Body(default_factory=dict),
                         ctx: Ctx = Depends(get_ctx)) -> dict:
    resolved = _batch_id(ctx, batch_id)
    candidate = payload.get("corrections") if isinstance(payload, dict) else None
    return service.propose(ctx.settings, ctx.store, resolved, candidate)


@router.get("/temporal")
def page_temporal(request: Request, as_of: str | None = None,
                  ctx: Ctx = Depends(get_ctx)):
    batch = ctx.current_batch()
    payload = None
    if batch is not None:
        payload = service.build(ctx.settings, ctx.store, batch["batch_id"],
                                as_of=as_of)
    return render(request, "temporal.html", page="temporal", page_title="双时间建模",
                  payload=payload, batch=batch)
