"""M6 编排：把落库结果装配成规格单，附带脚注与导出载荷。"""
from __future__ import annotations

import json

from ..m4_keys import evidence
from ..m5_batches import service as batch_service
from . import csv_out, json_out, spec_out


def build(store, batch_id: str) -> dict:
    bundle = batch_service.bundle(store, batch_id)
    notes = evidence.footnotes(bundle["keys"], bundle["star"],
                               bundle["accounts"] or {})
    document = spec_out.document(bundle, notes)
    document["evidence_rows"] = [
        {"table_name": row.get("table_name"), "column_name": row.get("column_name"),
         "row_ref": row.get("row_ref"), "raw_value": row.get("raw_value"),
         "code": row.get("code"), "detail": row.get("detail")}
        for row in bundle["quarantine"]
    ]
    return document


def to_payload(document: dict) -> dict:
    """接口返回：去掉模板专用字段，只留数据。"""
    fields = ["title", "accounts", "account_rows", "columns", "objects",
              "facts", "dims", "pending", "footnotes", "total_rows", "tables"]
    payload = {field: document.get(field) for field in fields}
    payload["keys"] = document.get("key_rows")
    payload["batch_id"] = document["batch"]["batch_id"]
    payload["run_id"] = document["batch"]["run_id"]
    payload["input_digest"] = (document.get("manifest") or {}).get("input_digest", "")
    return payload


def columns_csv(document: dict) -> bytes:
    return csv_out.encode(csv_out.columns_csv(document["columns"]))


def evidence_csv(document: dict) -> bytes:
    return csv_out.encode(csv_out.evidence_csv(document["evidence_rows"]))


def manifest_json(document: dict) -> bytes:
    payload = {
        "run_id": document["batch"]["run_id"],
        "batch_id": document["batch"]["batch_id"],
        "batch_key": document["batch"]["batch_key"],
        "input_digest": (document.get("manifest") or {}).get("input_digest", ""),
        "inputs": (document.get("manifest") or {}).get("inputs", []),
        "params": (document.get("manifest") or {}).get("params", {}),
        "counters": (document.get("manifest") or {}).get("counters", {}),
        "accounts": document.get("accounts"),
    }
    return json_out.encode(json_out.dumps(payload, indent=2))


def accounts_payload(raw: str) -> dict:
    return json.loads(raw)
