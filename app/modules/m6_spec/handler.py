"""M6 入口：规格单接口、三种导出、规格单页面（可打印）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response

from ... import errors
from ...web import Ctx, get_ctx, render
from . import service

router = APIRouter(tags=["M6 规格单导出"])


def _batch_id(ctx: Ctx, batch_id: str | None) -> str:
    batch = ctx.current_batch(batch_id)
    if batch is None:
        raise errors.NotFound("还没有任何批次，请先跑一次接入", code="X_EMPTY_SELECTION")
    return batch["batch_id"]


@router.get("/api/spec")
def api_spec(batch_id: str | None = None, ctx: Ctx = Depends(get_ctx)) -> dict:
    resolved = _batch_id(ctx, batch_id)
    document = service.build(ctx.store, resolved)
    payload = service.to_payload(document)
    if not payload["columns"]:
        raise errors.NotFound("该批次没有采纳列，规格单为空", code="X_EMPTY_SELECTION")
    return payload


@router.get("/api/spec/export.csv")
def api_spec_csv(batch_id: str | None = None, ctx: Ctx = Depends(get_ctx)) -> Response:
    resolved = _batch_id(ctx, batch_id)
    document = service.build(ctx.store, resolved)
    return Response(content=service.columns_csv(document),
                    media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition":
                             f'attachment; filename="starmodel-spec-{resolved}.csv"'})


@router.get("/api/spec/evidence.csv")
def api_spec_evidence(batch_id: str | None = None,
                      ctx: Ctx = Depends(get_ctx)) -> Response:
    resolved = _batch_id(ctx, batch_id)
    document = service.build(ctx.store, resolved)
    return Response(content=service.evidence_csv(document),
                    media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition":
                             f'attachment; filename="starmodel-evidence-{resolved}.csv"'})


@router.get("/api/spec/manifest.json")
def api_spec_manifest(batch_id: str | None = None,
                      ctx: Ctx = Depends(get_ctx)) -> Response:
    resolved = _batch_id(ctx, batch_id)
    document = service.build(ctx.store, resolved)
    return Response(content=service.manifest_json(document),
                    media_type="application/json; charset=utf-8",
                    headers={"Content-Disposition":
                             f'attachment; filename="starmodel-manifest-{resolved}.json"'})


@router.get("/spec")
def page_spec(request: Request, ctx: Ctx = Depends(get_ctx)):
    batch = ctx.current_batch()
    document = None
    if batch is not None:
        document = service.build(ctx.store, batch["batch_id"])
    return render(request, "spec.html", page="spec", page_title="规格单", batch=batch,
                  document=document)
