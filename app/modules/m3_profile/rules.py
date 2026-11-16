"""列处置与对账：处置优先级写死，四账用同一套计数推导。

处置优先级（命中即停）：拒绝 → 待定 → 采纳。顺序固定，避免结果取决于
代码书写顺序；每条处置都要带原因码，页面与导出直接用。
"""
from __future__ import annotations

import re
from decimal import Decimal

from ... import config

KEY_NAME_RE = re.compile(r"(^id$|_id$|_code$|_no$|_key$)", re.IGNORECASE)


def is_key_name(column_name: str) -> bool:
    return bool(KEY_NAME_RE.search((column_name or "").strip()))


def is_metric(column, foreign_columns: set) -> bool:
    """度量：数值型、列名不是键形状、也不是外键子列。"""
    if column.kind not in config.METRIC_KINDS:
        return False
    if is_key_name(column.name):
        return False
    return column.name.lower() not in foreign_columns


def declared_key_check(table) -> dict:
    """检查 DDL 声明的主键在样本上是否唯一；重复即业务键冲突。"""
    declared = list(table.declared_pk)
    if not declared:
        return {"declared": [], "unique": None, "counted": 0, "duplicates": [],
                "conflict": False}
    columns = []
    for name in declared:
        column = table.column(name)
        if column is None or not column.values:
            return {"declared": declared, "unique": None, "counted": 0, "duplicates": [],
                    "conflict": False}
        columns.append(list(column.values))
    rows = list(zip(*columns))
    seen: dict[tuple, int] = {}
    duplicates = []
    for index, row in enumerate(rows):
        key = tuple(row)
        if key in seen:
            duplicates.append({"key": list(key), "first_row": seen[key], "dup_row": index})
            continue
        seen[key] = index
    counted = len(rows)
    unique = len(seen)
    return {
        "declared": declared,
        "unique": unique,
        "counted": counted,
        "duplicates": duplicates,
        "conflict": bool(duplicates),
        "unique_rate": (Decimal(unique) / Decimal(counted)).quantize(Decimal("0.0001"))
        if counted else Decimal("0"),
    }


def disposition(column_stats: dict, *, key_conflict: bool, is_key_column: bool,
                column_name: str) -> tuple[str, list]:
    """按优先级给出处置与原因码；命中第一条即返回。"""
    codes: list[str] = []
    if column_stats["sample_rows"] < config.MIN_SAMPLE_ROWS:
        return "pending", ["Q_SAMPLE_SHORT"]
    if column_stats["null_rate"] == config.REJECT_NULL_RATE:
        return "rejected", ["Q_ALL_NULL"]
    if column_stats["unparsable_rate"] >= config.REJECT_UNPARSABLE_RATE:
        return "rejected", ["Q_UNPARSABLE_MAJORITY"]
    if key_conflict and is_key_column:
        return "pending", ["Q_KEY_COLUMN_PENDING"]
    if is_key_name(column_name) and column_stats["unique_rate"] < config.PK_UNIQUE_RATE:
        codes.append("Q_LOW_UNIQUE")
    return "adopted", codes


def column_accounts(columns: list) -> dict:
    total = len(columns)
    adopted = len([c for c in columns if c["disposition"] == "adopted"])
    rejected = len([c for c in columns if c["disposition"] == "rejected"])
    pending = len([c for c in columns if c["disposition"] == "pending"])
    return {"total": total, "adopted": adopted, "rejected": rejected, "pending": pending,
            "balanced": total == adopted + rejected + pending}


def row_accounts(ledger: list) -> dict:
    total = sum(row["total"] for row in ledger)
    valid = sum(row["valid"] for row in ledger)
    null_row = sum(row["null_row"] for row in ledger)
    quarantined = sum(row["quarantined"] for row in ledger)
    return {"total": total, "valid": valid, "null_row": null_row,
            "quarantined": quarantined,
            "balanced": total == valid + null_row + quarantined}


def reference_accounts(keys: list) -> dict:
    declared = [k for k in keys if k["kind"] == "fk" and k["declared"]]
    confirmed = [k for k in declared if k["status"] == "confirmed"]
    unidirectional = [k for k in declared if k["status"] == "unidirectional"]
    inferred = [k for k in keys if k["kind"] == "fk" and not k["declared"]
                and k["status"] == "inferred"]
    return {"declared": len(declared), "confirmed": len(confirmed),
            "unidirectional": len(unidirectional), "inferred": len(inferred),
            "balanced": len(declared) == len(confirmed) + len(unidirectional)}


def object_accounts(star: list) -> dict:
    total = len(star)
    fact = len([s for s in star if s["role"] == "fact"])
    dim = len([s for s in star if s["role"] == "dim"])
    pending = len([s for s in star if s["role"] == "pending"])
    return {"total": total, "fact": fact, "dim": dim, "pending": pending,
            "balanced": total == fact + dim + pending}


def build(*, columns: list, row_ledger: list, keys: list, star: list,
          metric_sums: dict) -> dict:
    sub = {
        "columns": column_accounts(columns),
        "rows": row_accounts(row_ledger),
        "references": reference_accounts(keys),
        "objects": object_accounts(star),
    }
    role_by_table = {item["table_name"]: item["role"] for item in star}
    kind_by_column = {(c["table_name"], c["column_name"]): c["kind"] for c in columns}
    fact_total = Decimal("0")
    fact_totals = {}
    for table_name, totals in metric_sums.items():
        if role_by_table.get(table_name) != "fact":
            continue
        fact_totals[table_name] = {}
        for column_name, value in totals.items():
            fact_totals[table_name][column_name] = str(value)
            if kind_by_column.get((table_name, column_name)) != "decimal":
                continue
            fact_total += Decimal(value)
    accounts = dict(sub)
    accounts["metric_total"] = str(fact_total.quantize(config.DECIMAL_QUANT))
    accounts["metric_totals"] = fact_totals
    accounts["balanced"] = all(item["balanced"] for item in sub.values())
    return accounts


def rows_for_page(accounts: dict) -> list:
    """把四账摊平成表格行，页面与规格单共用。"""
    return [
        {"id": "A1", "name": "列账", "expected": accounts["columns"]["total"],
         "parts": (f"采纳 {accounts['columns']['adopted']} + "
                   f"拒绝 {accounts['columns']['rejected']} + "
                   f"待定 {accounts['columns']['pending']}"),
         "balanced": accounts["columns"]["balanced"]},
        {"id": "A2", "name": "行账", "expected": accounts["rows"]["total"],
         "parts": (f"有效 {accounts['rows']['valid']} + "
                   f"空值 {accounts['rows']['null_row']} + "
                   f"隔离 {accounts['rows']['quarantined']}"),
         "balanced": accounts["rows"]["balanced"]},
        {"id": "A3", "name": "引用账", "expected": accounts["references"]["declared"],
         "parts": (f"一致 {accounts['references']['confirmed']} + "
                   f"单向 {accounts['references']['unidirectional']}，"
                   f"另推断 {accounts['references']['inferred']}"),
         "balanced": accounts["references"]["balanced"]},
        {"id": "A4", "name": "对象账", "expected": accounts["objects"]["total"],
         "parts": (f"事实 {accounts['objects']['fact']} + "
                   f"维 {accounts['objects']['dim']} + "
                   f"待定 {accounts['objects']['pending']}"),
         "balanced": accounts["objects"]["balanced"]},
    ]
