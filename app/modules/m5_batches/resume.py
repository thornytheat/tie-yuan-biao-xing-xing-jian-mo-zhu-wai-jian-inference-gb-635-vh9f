"""重跑与续跑：同一批次键直接复用结果，不新增行；中断批次从水位继续。

复用判定只看「批次键 + 状态 done」，与调用次数无关，所以连点两次结果一样。
"""
from __future__ import annotations

from ... import reasons
from . import state


def replay_payload(store, batch: dict) -> dict:
    counts = store.count_rows(batch["batch_id"])
    totals = store.load_totals(batch["batch_id"])
    stages = store.load_stages(batch["batch_id"])
    return {
        "status": "done",
        "replayed": True,
        "code": "B_IDEMPOTENT_REPLAY",
        "message": reasons.label("B_IDEMPOTENT_REPLAY"),
        "batch_id": batch["batch_id"],
        "batch_key": batch["batch_key"],
        "run_id": batch["run_id"],
        "counts": counts,
        "watermark": state.watermark(stages),
        "stages": stages,
        "accounts": _accounts(totals),
    }


def can_resume(store, batch_id: str) -> tuple[bool, str]:
    batch = store.get_batch(batch_id)
    if batch is None:
        return False, "B_EMPTY"
    if batch["status"] == "done":
        return False, "B_IDEMPOTENT_REPLAY"
    stages = store.load_stages(batch_id)
    mark = state.watermark(stages)
    if not mark["stage"]:
        return False, "B_RESUME_NO_STATE"
    return True, ""


def _accounts(totals: dict) -> dict | None:
    import json
    raw = totals.get("accounts_json")
    return json.loads(raw) if raw else None
