"""M4 入口：键推断结论、星型建议两个接口与两个页面。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from ... import config, errors, reasons
from ...web import Ctx, get_ctx, render
from ..m4_keys import evidence
from ..m5_batches import service as batch_service
from . import service

router = APIRouter(tags=["M4 键推断与建议"])


def _batch_id(ctx: Ctx, batch_id: str | None) -> str:
    batch = ctx.current_batch(batch_id)
    if batch is None:
        raise errors.NotFound("还没有任何批次，请先跑一次接入", code="B_EMPTY")
    return batch["batch_id"]


@router.get("/api/model")
def api_model(batch_id: str | None = None, kind: str = "",
              ctx: Ctx = Depends(get_ctx)) -> dict:
    resolved = _batch_id(ctx, batch_id)
    bundle = batch_service.bundle(ctx.store, resolved)
    payload = service.view(_Modeled(bundle["keys"], bundle["star"]))
    if kind:
        payload["keys"] = [row for row in payload["keys"] if row["kind"] == kind]
    payload["batch_id"] = resolved
    payload["objects"] = payload.pop("star")
    payload["role_labels"] = config.ROLE_LABELS
    payload["scd_labels"] = config.SCD_LABELS
    return payload


class _Modeled:
    """把落库的行包装成 service.view 期望的两个列表。"""

    def __init__(self, keys: list, star: list):
        self.keys = keys
        self.star = star


@router.get("/keys")
def page_keys(request: Request, ctx: Ctx = Depends(get_ctx)):
    batch = ctx.current_batch()
    payload = {"pk": [], "fk": [], "rejected": [], "conflicts": []}
    if batch is not None:
        bundle = batch_service.bundle(ctx.store, batch["batch_id"])
        view = service.view(_Modeled(bundle["keys"], bundle["star"]))
        payload = {
            "pk": [row for row in view["keys"] if row["kind"] == "pk"],
            "fk": [row for row in view["keys"]
                   if row["kind"] == "fk" and row["status"] in
                   ("confirmed", "inferred", "unidirectional")],
            "rejected": [row for row in view["keys"]
                         if row["kind"] == "fk" and row["status"] == "rejected"],
            "conflicts": bundle["conflicts"],
        }
        for row in payload["pk"] + payload["fk"] + payload["rejected"]:
            row["note"] = evidence.pk_note(row) if row["kind"] == "pk" \
                else evidence.fk_note(row)
    return render(request, "keys.html", page="keys", page_title="键推断", payload=payload,
                  batch=batch)


@router.get("/star")
def page_star(request: Request, ctx: Ctx = Depends(get_ctx)):
    batch = ctx.current_batch()
    payload = {"facts": [], "dims": [], "pending": [], "accounts": None, "notes": []}
    if batch is not None:
        bundle = batch_service.bundle(ctx.store, batch["batch_id"])
        payload["accounts"] = bundle["accounts"]
        for row in bundle["star"]:
            entry = dict(row)
            entry["role_label"] = config.ROLE_LABELS.get(row["role"], row["role"])
            entry["scd_label"] = config.SCD_LABELS.get(row["scd"], row["scd"] or "—")
            entry["role_note"] = (
                f"度量 {len(row.get('metric_columns') or [])}，"
                f"外键 {row.get('fk_count', 0)}")
            if row["role"] == "pending":
                entry["role_note"] += f"；{reasons.label(row.get('reason_code', ''))}"
            entry["scd_note"] = (
                f"变更列 {row.get('evidence', {}).get('scd', {}).get('change_columns', 0)}"
                f"/{row.get('evidence', {}).get('scd', {}).get('column_count', 0)}")
            if row["role"] == "fact":
                payload["facts"].append(entry)
            elif row["role"] == "dim":
                payload["dims"].append(entry)
            else:
                payload["pending"].append(entry)
        payload["notes"] = evidence.footnotes(bundle["keys"], bundle["star"],
                                              bundle["accounts"] or {})
    return render(request, "star.html", page="star", page_title="星型建议", payload=payload,
                  batch=batch)
