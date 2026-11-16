"""规格单装配：列清单、键结论、星型对象、四账，四个区块共用一套字段。

列区块只用「采纳」的列，拒绝与待定的列单独列在附注区，规格单正表不混入。
"""
from __future__ import annotations

from ... import config, reasons
from ..m3_profile import rules as m3_rules


def column_row(column: dict, role: str) -> dict:
    return {
        "table_name": column["table_name"],
        "column_name": column["column_name"],
        "kind": column["kind"],
        "ddl_type": column["ddl_type"],
        "nullable": "是" if column["nullable"] else "否",
        "null_rate": str(column["null_rate"]),
        "unique_rate": str(column["unique_rate"]),
        "distinct_count": column["distinct_count"],
        "sample_rows": column["sample_rows"],
        "disposition": column["disposition"],
        "disposition_label": config.DISPOSITION_LABELS.get(column["disposition"],
                                                           column["disposition"]),
        "role": role,
        "metric_total": (None if column["metric_total"] is None
                         else str(column["metric_total"])),
        "codes": list(column["reason_codes"]),
        "code_labels": [reasons.label(code) for code in column["reason_codes"]],
        "note": (f"空值 {column['null_total']}/{column['sample_rows']}，"
                 f"去重 {column['distinct_count']}"),
    }


def column_rows(columns: list) -> list:
    return [column_row(column, column.get("measurement", "")) for column in columns]


def key_rows(keys: list) -> list:
    rows = []
    for item in keys:
        rows.append({
            "kind": item["kind"],
            "kind_label": "主键" if item["kind"] == "pk" else "外键",
            "child_table": item["child_table"],
            "child_columns": item["child_columns"],
            "child_text": "、".join(item["child_columns"]) or "—",
            "parent_table": item["parent_table"] or "—",
            "parent_text": "、".join(item["parent_columns"]) or "—",
            "declared": "DDL" if item["declared"] else "推断",
            "status": item["status"],
            "status_label": {"confirmed": "一致", "inferred": "推断", "unidirectional": "单向",
                             "conflict": "冲突", "none": "无", "rejected": "不成立",
                             "unresolved": "未解析"}.get(item["status"], item["status"]),
            "inclusion": None if item["inclusion"] is None else str(item["inclusion"]),
            "cardinality_ratio": (None if item["cardinality_ratio"] is None
                                  else str(item["cardinality_ratio"])),
            "code": item["reason_code"],
            "code_label": reasons.label(item["reason_code"]),
            "note": (item.get("evidence") or {}).get("note", ""),
        })
    return rows


def object_rows(star: list) -> list:
    rows = []
    for item in star:
        evidence = item.get("evidence") or {}
        rows.append({
            "table_name": item["table_name"],
            "role": item["role"],
            "role_label": config.ROLE_LABELS.get(item["role"], item["role"]),
            "scd": item["scd"],
            "scd_label": config.SCD_LABELS.get(item["scd"], item["scd"] or "—"),
            "scd_ratio": evidence.get("scd", {}).get("ratio", ""),
            "metric_count": len(item.get("metric_columns") or []),
            "metric_text": "、".join(_metric_name(entry)
                                    for entry in (item.get("metric_columns") or [])) or "—",
            "fk_count": item.get("fk_count", 0),
            "degenerate": "、".join(entry["column"] for entry in item.get("degenerate") or []),
            "reason_code": item.get("reason_code", ""),
            "reason_label": reasons.label(item.get("reason_code", "")),
        })
    return rows


def _metric_name(entry) -> str:
    return entry.get("column") if isinstance(entry, dict) else str(entry)


def account_rows(accounts: dict) -> list:
    return m3_rules.rows_for_page(accounts)


def rows(profiled, modeled, accounts) -> list:
    """规格单总行数：列 + 键 + 对象 + 四账。阶段计数与导出共用。"""
    return (column_rows(profiled.columns) + key_rows(modeled.keys)
            + object_rows(modeled.star) + account_rows(accounts))


def document(bundle: dict, footnotes: list) -> dict:
    """从落库数据装配一份规格单；页面与接口都调这里。"""
    columns = bundle["columns"]
    keys = bundle["keys"]
    star = bundle["star"]
    accounts = bundle["accounts"] or {}
    tables = bundle["tables"]
    manifest = bundle.get("manifest") or {}
    adopted = [column for column in columns if column["disposition"] == "adopted"]
    others = [column for column in columns if column["disposition"] != "adopted"]
    fact_rows = [row for row in object_rows(star) if row["role"] == "fact"]
    dim_rows = [row for row in object_rows(star) if row["role"] == "dim"]
    pending_rows = [row for row in object_rows(star) if row["role"] == "pending"]
    return {
        "title": "贴源层星型模型规格单",
        "batch": bundle["batch"],
        "manifest": manifest,
        "accounts": accounts,
        "account_rows": account_rows(accounts),
        "tables": tables,
        "adopted_columns": adopted,
        "other_columns": others,
        "columns": column_rows(adopted),
        "key_rows": key_rows(keys),
        "objects": object_rows(star),
        "facts": fact_rows,
        "dims": dim_rows,
        "pending": pending_rows,
        "footnotes": footnotes,
        "total_rows": len(adopted) + len(keys) + len(star) + len(account_rows(accounts)),
    }
