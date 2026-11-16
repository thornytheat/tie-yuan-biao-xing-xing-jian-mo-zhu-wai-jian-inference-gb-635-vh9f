"""M5 编排：接入 → 类型 → 剖析 → 键 → 规格单，五段依次计数，最后落库出清单。

整条流水线只有一个入口 run()，好处是批次键、阶段耗时、四账都在同一处收口；
出错时批次留 running/aborted 记录，不留半批结果。
"""
from __future__ import annotations

import datetime as dt
import json
import time
from decimal import Decimal

from ... import config, errors, manifest, reasons
from ..m1_ingest import service as m1_service
from ..m2_contract import service as m2_service
from ..m3_profile import rules as m3_rules
from ..m3_profile import service as m3_service
from ..m4_keys import service as m4_service
from ..m6_spec import spec_out
from . import resume, state


def _now() -> str:
    return dt.datetime.now(config.UTC).isoformat(timespec="seconds")


def run(*, settings, store, source: str = "sample", params: dict | None = None,
        force: bool = False) -> dict:
    params = dict(params or {"sample_cap": config.MAX_SAMPLE_ROWS_PER_TABLE})
    started_at = _now()
    clock = time.perf_counter()

    intake = m1_service.intake(settings)
    inputs = list(intake.inputs)
    input_digest = manifest.digest_parts(intake.digest_parts)
    batch_key_value = manifest.batch_key(rule_version=settings.rule_version,
                                         inputs=inputs, params=params)
    if not force:
        existing = store.find_done_by_key(batch_key_value)
        if existing is not None:
            payload = resume.replay_payload(store, existing)
            payload["input_digest"] = input_digest
            return payload

    batch = state.new_batch(batch_key_value=batch_key_value, input_digest=input_digest,
                            source=source, params=params)
    store.upsert_batch(batch)

    try:
        result = _pipeline(settings=settings, intake=intake, batch=batch,
                           input_digest=input_digest, inputs=inputs, params=params,
                           started_at=started_at, clock=clock, store=store)
    except Exception as exc:  # noqa: BLE001 - 失败要留痕，再抛给上层
        batch["status"] = "aborted"
        batch["finished_at"] = _now()
        batch["note"] = f"{type(exc).__name__}: {exc}"
        store.upsert_batch(batch)
        raise
    return result


def _pipeline(*, settings, intake, batch, input_digest, inputs, params,
              started_at, clock, store) -> dict:
    stages = []
    mark = time.perf_counter()

    tables_with_sample = len([1 for table in intake.ddl.tables
                              if intake.rows(table.name)])
    stages.append(state.stage_row(
        "import", in_count=len(intake.ddl.tables), out_count=tables_with_sample,
        quarantine=len(intake.quarantine),
        elapsed_ms=_ms(mark), note=f"分段 {intake.section_count}"))
    mark = time.perf_counter()

    coerced = m2_service.build(intake, settings)
    column_total = sum(len(table.columns) for table in coerced.tables)
    stages.append(state.stage_row(
        "contract", in_count=column_total,
        out_count=len([1 for table in coerced.tables
                       for column in table.columns if column.present]),
        quarantine=len(coerced.quarantine), elapsed_ms=_ms(mark)))
    mark = time.perf_counter()

    profiled = m3_service.build(coerced)
    adopted = len([1 for column in profiled.columns
                   if column["disposition"] == "adopted"])
    stages.append(state.stage_row(
        "profile", in_count=len(profiled.columns), out_count=adopted,
        quarantine=len(profiled.columns) - adopted, elapsed_ms=_ms(mark),
        note=f"冲突 {len(profiled.conflicts)}"))
    mark = time.perf_counter()

    modeled = m4_service.build(coerced, profiled)
    accounts = m3_rules.build(columns=profiled.columns, row_ledger=profiled.row_ledger,
                              keys=modeled.keys, star=modeled.star,
                              metric_sums=profiled.metric_sums)
    confirmed = len([1 for item in modeled.keys if item["status"] in ("confirmed", "inferred")])
    stages.append(state.stage_row(
        "keys", in_count=len(coerced.tables), out_count=confirmed,
        quarantine=len(modeled.quarantine), elapsed_ms=_ms(mark),
        note=f"外键推断 {len([1 for i in modeled.keys if i['status'] == 'inferred'])}"))
    mark = time.perf_counter()

    spec_rows = spec_out.rows(profiled, modeled, accounts)
    stages.append(state.stage_row(
        "spec", in_count=len(modeled.keys) + len(modeled.star), out_count=len(spec_rows),
        elapsed_ms=_ms(mark)))

    quarantine = (list(intake.quarantine) + list(profiled.quarantine)
                  + list(modeled.quarantine))
    totals = {
        "accounts_json": json.dumps(accounts, ensure_ascii=False),
        "columns_total": accounts["columns"]["total"],
        "rows_total": accounts["rows"]["total"],
        "metric_total": accounts["metric_total"],
        "quarantine_total": len(quarantine),
        "conflict_total": len(profiled.conflicts),
        "spec_rows": len(spec_rows),
        "sample_rows": sum(row["total"] for row in profiled.row_ledger),
    }
    finished_at = _now()
    elapsed = int((time.perf_counter() - clock) * 1000)
    batch["status"] = "done"
    batch["finished_at"] = finished_at
    batch["note"] = ""

    manifest_doc = manifest.build(
        run_id=batch["run_id"], batch_id=batch["batch_id"],
        batch_key_value=batch["batch_key"], settings=settings, inputs=inputs,
        params=params, started_at=started_at, finished_at=finished_at,
        elapsed_ms=elapsed, stages=stages,
        counters={key: str(value) for key, value in totals.items()
                  if key not in ("accounts_json",)},
        input_digest=input_digest)

    tables = [dict(table, role=_role_of(modeled, table["table_name"]),
                   role_reason=_role_code(modeled, table["table_name"]),
                   scd=_scd_of(modeled, table["table_name"]),
                   scd_reason=_scd_code(modeled, table["table_name"]),
                   metric_count=_metric_count(modeled, table["table_name"]),
                   fk_count=_fk_count(modeled, table["table_name"]))
              for table in profiled.tables]
    star_rows = [{
        "table_name": row["table_name"], "role": row["role"],
        "scd": row["scd"], "reason_code": row["role_reason"],
        "metric_columns": [item["column"] for item in row["metric_columns"]],
        "fk_count": row["fk_count"], "degenerate": row["degenerate"],
        "evidence": {**row["evidence"], "role_reason": row["role_reason"],
                     "scd_reason": row["scd_reason"]},
    } for row in modeled.star]
    key_rows = [{
        "kind": item["kind"], "child_table": item["child_table"],
        "child_columns": item["child_columns"], "parent_table": item["parent_table"],
        "parent_columns": item["parent_columns"], "declared": item["declared"],
        "status": item["status"], "inclusion": item["inclusion"],
        "cardinality_ratio": item["cardinality_ratio"],
        "reason_code": item["reason_code"],
        "evidence": {**item["evidence"], "note": item.get("note", "")},
    } for item in modeled.keys]

    store.save_run(
        batch=batch, tables=tables, columns=profiled.columns, keys=key_rows,
        star=star_rows, quarantine=quarantine, conflicts=profiled.conflicts,
        row_ledger=profiled.row_ledger, stages=stages, totals=totals,
        manifest=manifest_doc)

    return {
        "status": "done",
        "replayed": False,
        "code": "",
        "batch_id": batch["batch_id"],
        "batch_key": batch["batch_key"],
        "run_id": batch["run_id"],
        "input_digest": input_digest,
        "elapsed_ms": elapsed,
        "stages": stages,
        "accounts": accounts,
        "counts": store.count_rows(batch["batch_id"]),
        "watermark": state.watermark(stages),
        "manifest": manifest_doc,
    }


def _ms(mark: float) -> int:
    return int((time.perf_counter() - mark) * 1000)


def _row_of(modeled, table_name: str) -> dict | None:
    lowered = table_name.lower()
    for row in modeled.star:
        if row["table_name"].lower() == lowered:
            return row
    return None


def _role_of(modeled, table_name: str) -> str:
    row = _row_of(modeled, table_name)
    return row["role"] if row else "pending"


def _role_code(modeled, table_name: str) -> str:
    row = _row_of(modeled, table_name)
    return row["role_reason"] if row else ""


def _scd_of(modeled, table_name: str) -> str:
    row = _row_of(modeled, table_name)
    return row["scd"] if row else ""


def _scd_code(modeled, table_name: str) -> str:
    row = _row_of(modeled, table_name)
    return row["scd_reason"] if row else ""


def _metric_count(modeled, table_name: str) -> int:
    row = _row_of(modeled, table_name)
    return len(row["metric_columns"]) if row else 0


def _fk_count(modeled, table_name: str) -> int:
    row = _row_of(modeled, table_name)
    return row["fk_count"] if row else 0


def bundle(store, batch_id: str) -> dict:
    """批次详情：页面与导出共用一次装配，避免每页各拼一套。"""
    batch = store.get_batch(batch_id)
    if batch is None:
        raise errors.NotFound(f"批次不存在：{batch_id}", code="B_EMPTY")
    totals = store.load_totals(batch_id)
    accounts = json.loads(totals.get("accounts_json") or "null")
    return {
        "batch": batch,
        "tables": store.load_tables(batch_id),
        "columns": store.load_columns(batch_id),
        "keys": store.load_keys(batch_id),
        "star": store.load_star(batch_id),
        "quarantine": store.load_quarantine(batch_id),
        "conflicts": store.load_conflicts(batch_id),
        "row_ledger": store.load_row_ledger(batch_id),
        "stages": store.load_stages(batch_id),
        "totals": totals,
        "accounts": accounts,
        "manifest": store.load_manifest(batch_id) or {},
    }


def row(batch: dict, counts: dict | None = None) -> dict:
    """批次行：列表与详情共用，状态文案只在这里写一次。"""
    return {
        "batch_id": batch["batch_id"],
        "batch_key": batch["batch_key"][:12],
        "run_id": batch["run_id"],
        "status": batch["status"],
        "status_label": {"done": "完成", "running": "运行中", "failed": "失败",
                         "aborted": "中断"}.get(batch["status"], batch["status"]),
        "created_at": (batch.get("created_at") or "").replace("T", " ")[:19],
        "finished_at": (batch.get("finished_at") or "").replace("T", " ")[:19],
        "input_digest": (batch.get("input_digest") or "")[:12],
        "resume_of": batch.get("resume_of") or "",
        "counts": counts or {},
        "note": batch.get("note") or "",
    }


def rows(batches: list, store) -> list:
    return [row(batch, store.count_rows(batch["batch_id"])) for batch in batches]


def stage_rows(stages: list) -> list:
    return [
        {"stage": item["stage"],
         "label": config.STAGE_LABELS.get(item["stage"], item["stage"]),
         "in_count": item["in_count"], "out_count": item["out_count"],
         "quarantine_count": item["quarantine_count"], "status": item["status"],
         "elapsed_ms": item["elapsed_ms"], "note": item.get("note", "")}
        for item in stages
    ]


def list_batches(store, limit: int = 30) -> list:
    return rows(store.list_batches(limit), store)


def accounts_of(store, batch_id: str) -> dict:
    totals = store.load_totals(batch_id)
    raw = totals.get("accounts_json")
    if not raw:
        raise errors.NotFound("该批次没有对账结果", code="X_EMPTY_SELECTION")
    accounts = json.loads(raw)
    accounts["rows_page"] = m3_rules.rows_for_page(accounts)
    accounts["batch_id"] = batch_id
    accounts["metric_total"] = str(Decimal(accounts["metric_total"]).quantize(
        config.DECIMAL_QUANT))
    return accounts


def codes_of(rows: list) -> list:
    return sorted({row["code"] for row in rows if row.get("code")})
