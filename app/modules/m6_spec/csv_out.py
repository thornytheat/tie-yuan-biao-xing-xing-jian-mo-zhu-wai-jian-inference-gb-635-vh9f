"""CSV 导出：列规格表与证据行，字段固定，带 BOM 便于直接用表格软件打开。"""
from __future__ import annotations

import csv
import io

COLUMN_HEADERS = ("表", "列", "声明类型", "推断类型", "可空", "空值率", "唯一率",
                  "去重数", "样本行数", "处置", "角色", "度量合计", "原因码")
EVIDENCE_HEADERS = ("表", "列", "行号引用", "原值", "原因码", "结论")


def to_csv_text(headers, rows) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(headers)
    for row in rows:
        writer.writerow(["" if value is None else value for value in row])
    return buffer.getvalue()


def columns_csv(rows: list) -> str:
    body = [
        (row["table_name"], row["column_name"], row["ddl_type"], row["kind"],
         row["nullable"], row["null_rate"], row["unique_rate"], row["distinct_count"],
         row["sample_rows"], row["disposition_label"], row["role"],
         row["metric_total"] or "", "、".join(row["codes"]))
        for row in rows
    ]
    return to_csv_text(COLUMN_HEADERS, body)


def evidence_csv(rows: list) -> str:
    body = [(row.get("table_name") or "", row.get("column_name") or "",
             row.get("row_ref") or "", row.get("raw_value") or "",
             row.get("code") or "", row.get("detail") or "")
            for row in rows]
    return to_csv_text(EVIDENCE_HEADERS, body)


def encode(text: str) -> bytes:
    return text.encode("utf-8-sig")
