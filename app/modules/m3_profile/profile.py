"""列剖析：空值率、唯一率、去重数、不可解析数与度量和。

比率一律用 Decimal 计算并量化到 4 位小数，避免 0.6666… 这类尾数在页面与
导出之间出现两种写法；度量合计按最小刻度 0.01 量化。
"""
from __future__ import annotations

from decimal import Decimal

from ... import config
from . import rules

RATE_QUANT = Decimal("0.0001")


def rate(numerator: int, denominator: int) -> Decimal:
    if denominator <= 0:
        return Decimal("0")
    return (Decimal(numerator) / Decimal(denominator)).quantize(RATE_QUANT)


def distinct_count(values: list) -> int:
    return len(set(values))


def metric_total(column) -> Decimal | None:
    if column.kind not in config.METRIC_KINDS:
        return None
    total = Decimal("0")
    for value in column.values:
        try:
            total += Decimal(value)
        except Exception:
            continue
    return total.quantize(config.DECIMAL_QUANT)


def column_stats(column) -> dict:
    sample_rows = column.sample_rows
    counted = sample_rows - column.null_total
    distinct = distinct_count(column.values)
    return {
        "sample_rows": sample_rows,
        "null_total": column.null_total,
        "empty_count": column.empty_count,
        "sentinel_count": column.sentinel_count,
        "distinct_count": distinct,
        "unparsable": column.unparsable,
        "null_rate": rate(column.null_total, sample_rows),
        "unparsable_rate": rate(column.unparsable, sample_rows),
        "unique_rate": rate(distinct, counted),
        "kind": column.kind,
        "metric_total": metric_total(column),
        "sample_value": column.sample_value,
        "present": column.present,
    }


def key_signature(columns: list) -> tuple[int, int]:
    """组合键在该表的唯一性：(去重数, 有值的行数)。空值行不参与。"""
    seen = set()
    counted = 0
    for row in columns:
        if any(cell is None for cell in row):
            continue
        counted += 1
        seen.add(tuple(row))
    return len(seen), counted


def table_totals(tables: list, entries: dict) -> dict:
    """整批的度量合计，按表归集；只统计判定为度量的列，Decimal 相加。"""
    per_table = {}
    for table in tables:
        totals = {}
        for column in table.columns:
            entry = entries.get((table.name, column.name))
            if entry is None or not entry["is_metric"]:
                continue
            total = metric_total(column)
            if total is not None:
                totals[column.name] = str(total)
        if totals:
            per_table[table.name] = totals
    return per_table


def decorate(columns: list) -> list:
    """补上派生字段：从库里取出的列没有 is_key_name / is_metric 这些判定结果。"""
    for column in columns:
        column["is_key_name"] = rules.is_key_name(column["column_name"])
        column["is_metric"] = column.get("measurement") == "度量"
        column["unparsable_rate"] = rate(column.get("unparsable", 0),
                                         column.get("sample_rows", 0))
        column.setdefault("in_declared_pk", False)
    return columns


def summary(columns: list) -> dict:
    """给列剖析页用的汇总条：只放数字，不放解释。"""
    adopted = [c for c in columns if c["disposition"] == "adopted"]
    key_like = [c for c in columns if c["is_key_name"]]
    return {
        "column_total": len(columns),
        "adopted": len(adopted),
        "pending": len([c for c in columns if c["disposition"] == "pending"]),
        "rejected": len([c for c in columns if c["disposition"] == "rejected"]),
        "key_like": len(key_like),
        "all_null": len([c for c in columns if c["null_rate"] == Decimal("1")]),
    }
