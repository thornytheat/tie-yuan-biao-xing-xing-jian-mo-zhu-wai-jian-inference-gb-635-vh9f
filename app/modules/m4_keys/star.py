"""星型建议：事实 / 维度 / 待定，维度再给 SCD 类型，事实里再挑退化维度。

判定顺序固定，先判待定再判事实，避免「有度量就当事实」把冲突表也拉进去：
1. 键组合有冲突 → 待定；
2. 含度量且外键 ≥ 2 → 事实；
3. 含度量但外键不足 → 待定；
4. 无度量且被引用或自身有外键 → 维度；
5. 其余 → 待定。
"""
from __future__ import annotations

from decimal import Decimal

from ... import config

VERSION_COLUMNS = ("updated_at", "valid_from", "effective_from", "etl_time")
SCD_QUANT = Decimal("0.0001")


def change_columns(table, key_columns: set, fk_columns: set) -> list:
    names = []
    for column in table.columns:
        lowered = column.name.lower()
        if lowered in key_columns or lowered in fk_columns:
            continue
        if lowered in VERSION_COLUMNS:
            continue
        names.append(column.name)
    return names


def has_version_column(table) -> bool:
    return any(column.name.lower() in VERSION_COLUMNS for column in table.columns)


def scd_of(table, key_columns: set, fk_columns: set) -> tuple[str, str, dict]:
    if not has_version_column(table):
        return "SCD0", "K_NO_VERSION_COLUMN", {
            "version_column": False,
            "change_columns": 0,
            "column_count": len(table.columns),
            "ratio": "0",
        }
    changes = change_columns(table, key_columns, fk_columns)
    total = len(table.columns) or 1
    ratio = (Decimal(len(changes)) / Decimal(total)).quantize(SCD_QUANT)
    scd = "SCD2" if ratio > config.SCD2_CHANGE_RATIO else "SCD1"
    return scd, "K_SCD_BY_RATIO", {
        "version_column": True,
        "change_columns": len(changes),
        "column_count": len(table.columns),
        "ratio": str(ratio),
        "change_names": changes,
    }


def degenerate_of(table, key_columns: list, fk_columns: set, kind_by_name: dict) -> list:
    """事实表主键若是单列文本/编码，通常是退化维度（如订单号）。"""
    if len(key_columns) != 1:
        return []
    name = key_columns[0]
    if name.lower() in fk_columns:
        return []
    kind = kind_by_name.get(name.lower(), "")
    if kind not in ("code", "text"):
        return []
    return [{"column": name, "kind": kind, "code": "K_DEGENERATE"}]


def role_of(*, table, metrics: list, fk_count: int, referenced: bool,
            key_conflict: bool, has_pk: bool) -> tuple[str, str, dict]:
    evidence = {"metrics": len(metrics), "fk_count": fk_count,
                "referenced": referenced, "key_conflict": key_conflict,
                "has_pk": has_pk,
                "fact_fk_min": config.FACT_MIN_FOREIGN_KEYS}
    if key_conflict:
        return "pending", "K_DUP_KEY_CONFLICT", evidence
    if metrics and fk_count >= config.FACT_MIN_FOREIGN_KEYS and has_pk:
        return "fact", "K_FACT_CONFIRMED", evidence
    if referenced:
        return "dim", "K_DIM_REFERENCED", evidence
    if metrics and fk_count < config.FACT_MIN_FOREIGN_KEYS:
        return "pending", "K_FACT_FK_SHORT", evidence
    if fk_count >= 1:
        return "dim", "K_DIM_REFERENCED", evidence
    return "pending", "K_NO_PK" if not has_pk else "K_NO_METRIC", evidence


def metric_columns(table, column_stats: dict) -> list:
    out = []
    for column in table.columns:
        stats = column_stats.get(column.name.lower())
        if stats is None:
            continue
        if stats.get("is_metric"):
            out.append({"column": column.name, "kind": column.kind,
                        "total": (None if stats["metric_total"] is None
                                  else str(stats["metric_total"])),
                        "unit": _unit_of(column.kind)})
    return out


def _unit_of(kind: str) -> str:
    return "元" if kind == "decimal" else "件"
