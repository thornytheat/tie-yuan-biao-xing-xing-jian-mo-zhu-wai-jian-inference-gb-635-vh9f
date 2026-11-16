"""依据拼装：把每个结论背后的数值写成一行可复核的注文与脚注。

页面上的上标编号与规格单末尾的脚注共用这里的序号，避免两处各写一份。
"""
from __future__ import annotations

from decimal import Decimal


def rate_text(value) -> str:
    if value is None or value == "":
        return "—"
    if isinstance(value, str):
        return value
    return f"{value:.4f}" if isinstance(value, Decimal) else f"{float(value):.4f}"


def pct_text(value) -> str:
    if value is None or value == "":
        return "—"
    number = Decimal(str(value))
    return f"{(number * 100).quantize(Decimal('0.01'))}%"


def pk_note(finding: dict) -> str:
    evidence = finding.get("evidence") or {}
    if finding.get("status") == "none":
        return "无候选主键"
    distinct = evidence.get("distinct", 0)
    counted = evidence.get("counted", 0)
    return f"去重 {distinct} / {counted} 行，唯一率 {rate_text(evidence.get('unique_rate'))}"


def fk_note(finding: dict) -> str:
    evidence = finding.get("evidence") or {}
    matched = evidence.get("matched", 0)
    child = evidence.get("child_distinct", 0)
    parent = evidence.get("parent_distinct", 0)
    return (f"包含度 {rate_text(finding.get('inclusion'))}（{matched}/{child}），"
            f"基数比 {rate_text(finding.get('cardinality_ratio'))}（{child}/{parent}）")


def column_note(column: dict) -> str:
    return (f"空值 {column.get('null_total', 0)}/{column.get('sample_rows', 0)}，"
            f"去重 {column.get('distinct_count', 0)}，"
            f"唯一率 {rate_text(column.get('unique_rate'))}")


def role_tag(object_row: dict) -> str:
    mapped = {"fact": "事实", "dim": "维度", "pending": "待定"}
    return mapped.get(object_row.get("role"), object_row.get("role") or "")


def scd_tag(object_row: dict) -> str:
    evidence = object_row.get("evidence") or {}
    if not object_row.get("scd"):
        return "—"
    ratio = evidence.get("ratio")
    if ratio in (None, "", "0"):
        return object_row["scd"]
    return f"{object_row['scd']} {ratio}"


def footnotes(keys: list, star: list, accounts: dict) -> list:
    """规格单末尾的编号注文；序号与页面上标一致。"""
    items = []
    for finding in keys:
        if finding["kind"] == "pk":
            text = f"{finding['child_table']} 主键：{pk_note(finding)}"
        else:
            text = (f"{finding['child_table']}.{finding['child_columns'][0]} → "
                    f"{finding['parent_table']}：{fk_note(finding)}")
        items.append(text)
    objects = accounts.get("objects") or {}
    items.append(f"对象账：事实 {objects.get('fact', 0)}、维 {objects.get('dim', 0)}、"
                 f"待定 {objects.get('pending', 0)}")
    columns = accounts.get("columns") or {}
    items.append(f"列账：采纳 {columns.get('adopted', 0)}、拒绝 {columns.get('rejected', 0)}、"
                 f"待定 {columns.get('pending', 0)}")
    rows = accounts.get("rows") or {}
    items.append(f"行账：有效 {rows.get('valid', 0)}、空值 {rows.get('null_row', 0)}、"
                 f"隔离 {rows.get('quarantined', 0)}")
    return items
