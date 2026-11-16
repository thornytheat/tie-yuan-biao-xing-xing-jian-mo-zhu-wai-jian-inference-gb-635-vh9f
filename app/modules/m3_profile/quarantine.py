"""隔离区与冲突表导出：一行一条证据，CSV 带 BOM 便于 Excel 直接打开。

导出字段固定：阶段、原因码、表、列、行号引用、原值、说明。
"""
from __future__ import annotations

import csv
import io

from ... import reasons

HEADERS = ("阶段", "级别", "原因码", "表", "列", "行号引用", "原值", "说明")
CONFLICT_HEADERS = ("表", "键列", "键值", "保留行", "冲突行", "说明")


def to_csv_text(rows: list) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(HEADERS)
    for row in rows:
        code = row.get("code") or ""
        writer.writerow([
            reasons.stage_of(code),
            reasons.LEVEL_LABELS.get(reasons.level_of(code), ""),
            code,
            row.get("table_name") or "",
            row.get("column_name") or "",
            row.get("row_ref") or "",
            row.get("raw_value") or "",
            row.get("detail") or "",
        ])
    return buffer.getvalue()


def encode_csv(text: str) -> bytes:
    return text.encode("utf-8-sig")


def conflicts_to_csv_text(rows: list) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(CONFLICT_HEADERS)
    for row in rows:
        writer.writerow([
            row.get("table_name") or "",
            "、".join(row.get("key_columns") or []),
            row.get("key_value") or "",
            _compact(row.get("kept") or {}),
            _compact(row.get("dropped") or {}),
            row.get("note") or "",
        ])
    return buffer.getvalue()


def _compact(payload: dict) -> str:
    return "; ".join(f"{key}={value}" for key, value in payload.items())


def distribution(rows: list) -> list:
    """原因码分布：页面顶部一条，导出明细在下面。"""
    counter: dict[str, dict] = {}
    for row in rows:
        code = row.get("code") or ""
        bucket = counter.setdefault(code, {
            "code": code, "level": reasons.level_of(code),
            "title": reasons.label(code), "count": 0})
        bucket["count"] += 1
    return sorted(counter.values(), key=lambda item: (-item["count"], item["code"]))
