"""主键与外键推断：唯一率 + 非空 + 包含度 + 基数比，全部给出数值依据。

判定顺序固定：
1. 表有 DDL 声明主键 → 用声明，样本上验唯一性；
2. 没有声明 → 单列候选：列名是键形状、唯一率 1、无空值；
3. 外键：DDL 声明优先，再按同名列 + 包含度推断；声明与数据不一致时判单向。
"""
from __future__ import annotations

from decimal import Decimal

from ... import config
from ..m3_profile import rules

QUANT = Decimal("0.0001")


def _q(value: Decimal) -> Decimal:
    return value.quantize(QUANT)


def inclusion(child_values: list, parent_values: list) -> dict:
    """包含度 = 子表去重取值落在父表里的比例；基数比 = 子去重 / 父去重。"""
    child = {value for value in child_values if value is not None}
    parent = {value for value in parent_values if value is not None}
    matched = len(child & parent)
    return {
        "child_distinct": len(child),
        "parent_distinct": len(parent),
        "matched": matched,
        "inclusion": _q(Decimal(matched) / Decimal(len(child))) if child else Decimal("0"),
        "cardinality": _q(Decimal(len(child)) / Decimal(len(parent))) if parent else Decimal("0"),
    }


def key_columns_filled(rows: list, names: list) -> tuple[list, int]:
    """组合键：只取每列都有值的行。"""
    tuples = []
    for row in rows:
        values = [row.get(name) for name in names]
        if any(value is None for value in values):
            continue
        tuples.append(tuple(values))
    return tuples, len(tuples)


def pk_finding(table, column_stats: dict) -> dict:
    """主键结论：声明优先，其次单列推断；冲突时保留首见行。"""
    declared = list(table.declared_pk)
    if declared:
        tuples, counted = key_columns_filled(table.rows_norm, declared)
        unique = len(set(tuples))
        rate = _q(Decimal(unique) / Decimal(counted)) if counted else Decimal("0")
        status = "confirmed" if counted and unique == counted else "conflict"
        code = "K_PK_CONFIRMED" if status == "confirmed" else "K_DUP_KEY_CONFLICT"
        return {
            "kind": "pk", "child_table": table.name, "child_columns": declared,
            "parent_table": None, "parent_columns": [], "declared": True,
            "status": status, "inclusion": None, "cardinality_ratio": None,
            "reason_code": code,
            "evidence": {"source": "ddl", "distinct": unique, "counted": counted,
                         "unique_rate": str(rate)},
        }
    candidates = []
    for column in table.columns:
        if not rules.is_key_name(column.name):
            continue
        if column.kind not in ("code", "text", "int", "bigint"):
            continue
        if not column.values:
            continue
        stats = column_stats.get(column.name.lower())
        if stats is None:
            continue
        if stats["unique_rate"] != config.PK_UNIQUE_RATE or stats["null_total"] != 0:
            continue
        candidates.append((column, stats))
    if not candidates:
        return {
            "kind": "pk", "child_table": table.name, "child_columns": [],
            "parent_table": None, "parent_columns": [], "declared": False,
            "status": "none", "inclusion": None, "cardinality_ratio": None,
            "reason_code": "K_NO_PK",
            "evidence": {"source": "inferred", "candidates": 0},
        }
    column, stats = candidates[0]
    return {
        "kind": "pk", "child_table": table.name, "child_columns": [column.name],
        "parent_table": None, "parent_columns": [], "declared": False,
        "status": "inferred", "inclusion": None, "cardinality_ratio": None,
        "reason_code": "K_PK_CONFIRMED",
        "evidence": {"source": "inferred", "distinct": stats["distinct_count"],
                     "counted": stats["sample_rows"] - stats["null_total"],
                     "unique_rate": str(stats["unique_rate"])},
    }


def fk_from_declaration(table, foreign_key: dict, tables_by_name: dict,
                        pk_by_table: dict) -> dict:
    parent = tables_by_name.get(foreign_key["ref_table"].lower())
    child_name = foreign_key["columns"][0]
    parent_name = foreign_key["ref_columns"][0] if foreign_key["ref_columns"] else child_name
    child_column = table.column(child_name)
    parent_column = parent.column(parent_name) if parent else None
    if child_column is None or parent_column is None:
        return {
            "kind": "fk", "child_table": table.name, "child_columns": [child_name],
            "parent_table": foreign_key["ref_table"], "parent_columns": [parent_name],
            "declared": True, "status": "unresolved", "inclusion": None,
            "cardinality_ratio": None, "reason_code": "K_FK_LOW_INCLUSION",
            "evidence": {"source": "ddl", "note": "父表或列在 DDL 里找不到"},
        }
    stats = inclusion(child_column.values, parent_column.values)
    parent_pk = pk_by_table.get(foreign_key["ref_table"].lower(), {})
    parent_pk_columns = [name.lower() for name in parent_pk.get("child_columns", [])]
    parent_is_pk = (len(parent_pk_columns) == 1
                    and parent_pk_columns[0] == parent_name.lower())
    status, code = _judge(stats, parent_is_pk, declared=True)
    return {
        "kind": "fk", "child_table": table.name, "child_columns": [child_name],
        "parent_table": parent.name, "parent_columns": [parent_name],
        "declared": True, "status": status, "inclusion": stats["inclusion"],
        "cardinality_ratio": stats["cardinality"], "reason_code": code,
        "evidence": {"source": "ddl", **{key: str(value) if isinstance(value, Decimal)
                                         else value for key, value in stats.items()},
                     "parent_is_pk": parent_is_pk},
    }


def fk_inferred(child, child_column, parent, parent_column, parent_pk: dict) -> dict | None:
    if child.name == parent.name and child_column.name == parent_column.name:
        return None
    parent_pk_names = [name.lower() for name in parent_pk.get("child_columns", [])]
    if len(parent_pk_names) != 1 or parent_pk_names[0] != parent_column.name.lower():
        return None
    stats = inclusion(child_column.values, parent_column.values)
    status, code = _judge(stats, True, declared=False)
    if status != "inferred":
        return None
    return {
        "kind": "fk", "child_table": child.name,
        "child_columns": [child_column.name],
        "parent_table": parent.name, "parent_columns": [parent_column.name],
        "declared": False, "status": "inferred", "inclusion": stats["inclusion"],
        "cardinality_ratio": stats["cardinality"], "reason_code": "K_FK_INFERRED",
        "evidence": {"source": "inferred",
                     **{key: str(value) if isinstance(value, Decimal) else value
                        for key, value in stats.items()}},
    }


def _judge(stats: dict, parent_is_pk: bool, *, declared: bool) -> tuple[str, str]:
    """声明外键只看包含度（声明即权威）；推断外键才对基数比设区间。"""
    inclusion_ok = stats["inclusion"] >= config.FK_INCLUSION_MIN
    card_ok = (config.FK_CARDINALITY_MIN <= stats["cardinality"]
               <= config.FK_CARDINALITY_MAX)
    if declared:
        if inclusion_ok and parent_is_pk:
            return "confirmed", "K_FK_CONFIRMED"
        return "unidirectional", "K_FK_UNIDIRECTIONAL"
    if inclusion_ok and parent_is_pk and card_ok:
        return "inferred", "K_FK_INFERRED"
    if not inclusion_ok:
        return "rejected", "K_FK_LOW_INCLUSION"
    if not parent_is_pk:
        return "rejected", "K_FK_CARD_INELIGIBLE"
    return "rejected", "K_FK_CARD_OUT"


def key_pairs(tables: list) -> list:
    """同名列的表对：只在名字完全一致的键名列之间找推断边。"""
    pairs = []
    for parent in tables:
        for child in tables:
            if parent.name == child.name:
                continue
            for parent_column in parent.columns:
                if not rules.is_key_name(parent_column.name):
                    continue
                child_column = child.column(parent_column.name)
                if child_column is None:
                    continue
                pairs.append((child, child_column, parent, parent_column))
    return pairs
