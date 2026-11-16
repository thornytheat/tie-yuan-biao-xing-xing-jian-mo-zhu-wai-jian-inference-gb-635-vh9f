"""M8 编排：批次结论 + 行级数据 → 更正链 / 当前库存 / 关系版本 / 规定时点账。

与 M7 一样按请求现算：只读已落库结论与行级归一结果，不写批次、不新增
阶段行。重算是纯投影——同一台账必然算出同一当前表，同一更正身份永不
重复出版本，所以重跑与连点都不改变结果。
"""
from __future__ import annotations

import re

from ... import config, errors
from ..m1_ingest import service as m1_service
from ..m2_contract import coerce
from ..m2_contract import service as m2_service
from ..m5_batches import service as batch_service
from . import bitemporal, relations

DATE_ONLY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def context(settings, store, batch_id: str) -> dict:
    """一次装配：批次详情 + 行级归一数据（只读，不落库）。"""
    bundle = batch_service.bundle(store, batch_id)
    intake = m1_service.intake(settings)
    coerced = m2_service.build(intake, settings)
    return {
        "bundle": bundle,
        "coerced": coerced,
        "rows_by_table": {table.name: table.rows_norm for table in coerced.tables},
        "lines_by_table": {table.name: list(table.valid_lines)
                           for table in coerced.tables},
    }


def temporal_meta(bundle: dict) -> dict | None:
    """双时间表的元信息：业务键取 DDL 声明主键，粒度 = 业务键去掉生效时间列。"""
    table_name = config.TEMPORAL_TABLE
    table = None
    for row in bundle["tables"]:
        if row["table_name"].lower() == table_name.lower():
            table = row
            break
    if table is None:
        return None
    key_columns = list(table.get("declared_pk") or [])
    effective = config.TEMPORAL_EFFECTIVE_COLUMN
    if effective not in key_columns:
        key_columns = [effective] + key_columns
    grain = [name for name in key_columns if name != effective]
    identity = [column["column_name"] for column in bundle["columns"]
                if column["table_name"] == table["table_name"]]
    return {"table": table["table_name"], "key_columns": key_columns,
            "grain_columns": grain, "identity_columns": identity,
            "effective_column": effective,
            "ingest_column": config.TEMPORAL_INGEST_COLUMN,
            "measure_column": config.TEMPORAL_MEASURE_COLUMN}


def parse_as_of(text: str) -> str:
    """对账时点：日期按日末对齐，时间戳归一到秒；解析不了给 4xx。"""
    raw = (text or "").strip()
    if not raw:
        raise errors.BadRequest("对账时点不能为空", code="T_BAD_AS_OF")
    if DATE_ONLY_RE.match(raw):
        return f"{raw} {config.TEMPORAL_DAY_END}"
    parsed, code = coerce.parse_timestamp(raw)
    if parsed is None:
        raise errors.BadRequest(f"对账时点格式无法解析：{text}", code="T_BAD_AS_OF")
    return parsed.strftime("%Y-%m-%d %H:%M:%S")


def _chain_of(parts: dict, meta: dict) -> dict:
    rows = parts["rows_by_table"].get(meta["table"], [])
    lines = parts["lines_by_table"].get(meta["table"], [])
    return bitemporal.build_chain(
        rows, key_columns=meta["key_columns"],
        effective_column=meta["effective_column"],
        ingest_column=meta["ingest_column"],
        measure_column=meta["measure_column"],
        identity_columns=meta["identity_columns"], line_numbers=lines)


def _max_ingest(versions: list) -> str:
    return max((str(version["ingest"]) for version in versions), default="")


def _accounts(chain: dict, current: list, recon: dict) -> list:
    versions = chain["versions"]
    superseded = len(versions) - chain["business_keys"]
    rows_total = len(versions) + chain["duplicates"] + chain["skipped"]
    return [
        {"id": "T1", "name": "版本账", "expected": len(versions),
         "parts": f"键当前 {chain['business_keys']} + 历史 {superseded}",
         "balanced": len(versions) == chain["business_keys"] + superseded},
        {"id": "T2", "name": "粒度账", "expected": len(current),
         "parts": f"当前行 {len(current)}，每个门店商品恰好 1 行",
         "balanced": len(current) == len({row['grain_text'] for row in current})},
        {"id": "T3", "name": "时点账", "expected": len(recon["rows"]),
         "parts": f"一致 {recon['matched']} + 差异 {recon['diff']}",
         "balanced": recon["balanced"]},
        {"id": "T4", "name": "台账账", "expected": rows_total,
         "parts": (f"版本 {len(versions)} + 重复 {chain['duplicates']} + "
                   f"跳过 {chain['skipped']}"),
         "balanced": True},
    ]


def _notes(chain: dict, current: list, recon: dict, relations_rows: list,
           as_of: str, as_of_now: bool) -> list:
    superseded = len(chain["versions"]) - chain["business_keys"]
    as_of_relations = [row for row in relations_rows if row["policy"] == "as_of"]
    checks = [row["check"] for row in as_of_relations if row["check"]]
    matched = sum(item["matched"] for item in checks)
    fallback = sum(item["fallback"] for item in checks)
    missing = sum(item["missing"] for item in checks)
    notes = [
        f"版本 {len(chain['versions'])} 个：业务键 {chain['business_keys']} 个各 1 个"
        f"当前版，历史更正 {superseded} 个全部保留在台账",
        f"当前库存 {len(current)} 行：每个门店商品恰好 1 行，按事实表恢复",
        f"关系 {len(relations_rows)} 条：时点版 {len(as_of_relations)} 条、"
        f"当前版 {len(relations_rows) - len(as_of_relations)} 条；时点核验命中 "
        f"{matched}、首版兜底 {fallback}、维度缺键 {missing}",
        f"对账时点 {as_of}{'（现在）' if as_of_now else ''}：一致 {recon['matched']}、"
        f"差异 {recon['diff']}",
    ]
    if chain["duplicates"]:
        notes.append(f"同一更正身份重复 {chain['duplicates']} 条，未新增版本")
    return notes


def build(settings, store, batch_id: str, as_of: str | None = None) -> dict:
    """双时间总装：更正链 + 当前库存 + 关系版本 + 规定时点账。"""
    parts = context(settings, store, batch_id)
    bundle = parts["bundle"]
    relations_rows = relations.build(
        keys=bundle["keys"], star=bundle["star"], columns=bundle["columns"],
        rows_by_table=parts["rows_by_table"],
        temporal_table=config.TEMPORAL_TABLE,
        effective_column=config.TEMPORAL_EFFECTIVE_COLUMN)
    payload = {"batch_id": batch_id, "relationships": relations_rows,
               "temporal": None, "versions": [], "current": [],
               "reconcile": None, "accounts": [], "notes": []}
    meta = temporal_meta(bundle)
    if meta is None:
        payload["notes"] = [f"本批次没有 {config.TEMPORAL_TABLE}，只给出关系版本"]
        return payload
    chain = _chain_of(parts, meta)
    versions = chain["versions"]
    current = bitemporal.project_current(versions,
                                         grain_columns=meta["grain_columns"])
    max_ingest = _max_ingest(versions)
    resolved_as_of = parse_as_of(as_of) if as_of else max_ingest
    recon = bitemporal.reconcile(versions, current, as_of=resolved_as_of,
                                 grain_columns=meta["grain_columns"])
    as_of_now = resolved_as_of >= max_ingest
    fk_count = len([row for row in relations_rows
                    if row["child_table"] == meta["table"]])
    payload.update({
        "temporal": {
            "table": meta["table"],
            "key_columns": meta["key_columns"],
            "grain_columns": meta["grain_columns"],
            "effective_column": meta["effective_column"],
            "ingest_column": meta["ingest_column"],
            "measure_column": meta["measure_column"],
            "restored": {
                "role": "fact",
                "reason_code": "T_CHAIN_RESTORED",
                "fk_count": fk_count,
                "grain_unique": len(current) == len(
                    {row["grain_text"] for row in current}),
            },
        },
        "versions": versions,
        "current": current,
        "reconcile": recon,
        "as_of": resolved_as_of,
        "as_of_now": as_of_now,
        "accounts": _accounts(chain, current, recon),
        "summary": {
            "versions": len(versions),
            "business_keys": chain["business_keys"],
            "superseded": len(versions) - chain["business_keys"],
            "duplicates": chain["duplicates"],
            "skipped": chain["skipped"],
            "current_rows": len(current),
            "recon_matched": recon["matched"],
            "recon_diff": recon["diff"],
            "balanced": recon["balanced"],
        },
    })
    payload["notes"] = _notes(chain, current, recon, relations_rows,
                              resolved_as_of, as_of_now)
    return payload


def _column_meta(bundle: dict, table_name: str) -> dict:
    return {column["column_name"]: {"kind": column["kind"], "width": column["width"]}
            for column in bundle["columns"]
            if column["table_name"] == table_name}


def normalize_correction(raw: dict, meta: dict, *, required: list) -> dict:
    """提交行按列契约归一：与批次行同一套 coerce，口径才一致。"""
    if not isinstance(raw, dict):
        raise errors.BadRequest("更正行必须是对象", code="T_BAD_CORRECTION")
    out = {}
    for name, item in meta.items():
        value = raw.get(name)
        if value is None:
            out[name] = None
            continue
        text = str(value).strip()
        if not text:
            out[name] = None
            continue
        kind = item["kind"]
        if kind == "code":
            out[name] = coerce.normalize_code(text, item["width"])[0]
        elif kind in ("int", "bigint"):
            number, code = coerce.parse_decimal(text)
            if number is None:
                raise errors.BadRequest(f"{name} 不是合法数值：{text}",
                                        code="T_BAD_CORRECTION")
            out[name] = str(int(number)) if kind == "int" else str(number)
        elif kind == "decimal":
            number, code = coerce.parse_decimal(text)
            if number is None:
                raise errors.BadRequest(f"{name} 不是合法数值：{text}",
                                        code="T_BAD_CORRECTION")
            out[name] = str(number.quantize(config.DECIMAL_QUANT))
        elif kind == "date":
            parsed, code = coerce.parse_date(text)
            if parsed is None:
                raise errors.BadRequest(f"{name} 不是合法日期：{text}",
                                        code="T_BAD_CORRECTION")
            out[name] = parsed.isoformat()
        elif kind == "timestamp":
            parsed, code = coerce.parse_timestamp(text)
            if parsed is None:
                raise errors.BadRequest(f"{name} 不是合法时间戳：{text}",
                                        code="T_BAD_CORRECTION")
            out[name] = parsed.strftime("%Y-%m-%d %H:%M:%S")
        else:
            out[name] = text
    missing = [name for name in required if out.get(name) is None]
    if missing:
        raise errors.BadRequest(f"更正行缺少 {missing[0]}", code="T_BAD_CORRECTION")
    return out


def propose(settings, store, batch_id: str, candidate: list) -> dict:
    """上游更新试算：并入更正行 → 重建更正链 → 重算当前库存。

    同一更正身份重复提交不新增版本（added = 0），当前表也不变。
    """
    if not isinstance(candidate, list) or not candidate:
        raise errors.BadRequest("corrections 必须是非空数组", code="T_BAD_CORRECTION")
    parts = context(settings, store, batch_id)
    meta = temporal_meta(parts["bundle"])
    if meta is None:
        raise errors.NotFound(f"本批次没有 {config.TEMPORAL_TABLE}",
                              code="T_BAD_CORRECTION")
    column_meta = _column_meta(parts["bundle"], meta["table"])
    required = (meta["key_columns"] + [meta["ingest_column"],
                                       meta["measure_column"]])
    normalized = [normalize_correction(raw, column_meta, required=required)
                  for raw in candidate]
    base_rows = parts["rows_by_table"].get(meta["table"], [])
    base_chain = _chain_of(parts, meta)
    merged_chain = bitemporal.build_chain(
        list(base_rows) + normalized, key_columns=meta["key_columns"],
        effective_column=meta["effective_column"],
        ingest_column=meta["ingest_column"],
        measure_column=meta["measure_column"],
        identity_columns=meta["identity_columns"])
    base_current = {row["grain_text"]: row
                    for row in bitemporal.project_current(
                        base_chain["versions"], grain_columns=meta["grain_columns"])}
    new_current = bitemporal.project_current(merged_chain["versions"],
                                             grain_columns=meta["grain_columns"])
    changes = []
    for row in new_current:
        old = base_current.get(row["grain_text"])
        if old is None or str(old["measure"]) != str(row["measure"]):
            changes.append({"grain_text": row["grain_text"],
                            "old": None if old is None else old["measure"],
                            "new": row["measure"]})
    added = len(merged_chain["versions"]) - len(base_chain["versions"])
    duplicates = merged_chain["duplicates"] - base_chain["duplicates"]
    return {
        "batch_id": batch_id,
        "submitted": len(normalized),
        "added": added,
        "duplicates": duplicates,
        "versions_total": len(merged_chain["versions"]),
        "current_total": len(new_current),
        "current_changes": changes,
        "as_of": _max_ingest(merged_chain["versions"]),
        "reason_code": "T_UPSTREAM_RECOMPUTED" if added else "T_DUP_CORRECTION",
    }


def relationship_block(settings, store, batch_id: str) -> list:
    """规格单「关系版本」区块：每条关系用了哪一版、为什么。"""
    parts = context(settings, store, batch_id)
    bundle = parts["bundle"]
    return relations.build(
        keys=bundle["keys"], star=bundle["star"], columns=bundle["columns"],
        rows_by_table=parts["rows_by_table"],
        temporal_table=config.TEMPORAL_TABLE,
        effective_column=config.TEMPORAL_EFFECTIVE_COLUMN)
