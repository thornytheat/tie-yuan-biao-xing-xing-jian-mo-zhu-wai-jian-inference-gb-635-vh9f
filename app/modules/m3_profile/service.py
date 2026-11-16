"""M3 编排：逐列统计 → 处置判定 → 主键唯一性检查 → 行账与冲突表。

处置顺序、四账计数都在 rules.py；本文件只做编排与证据拼装。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ... import config, reasons
from . import profile, rules


@dataclass
class Profiled:
    tables: list = field(default_factory=list)
    columns: list = field(default_factory=list)
    row_ledger: list = field(default_factory=list)
    quarantine: list = field(default_factory=list)
    conflicts: list = field(default_factory=list)
    metric_sums: dict = field(default_factory=dict)
    codes: list = field(default_factory=list)

    def table(self, name: str) -> dict | None:
        lowered = name.lower()
        for table in self.tables:
            if table["table_name"].lower() == lowered:
                return table
        return None

    def column(self, table_name: str, column_name: str) -> dict | None:
        lowered_table, lowered_column = table_name.lower(), column_name.lower()
        for column in self.columns:
            if (column["table_name"].lower() == lowered_table
                    and column["column_name"].lower() == lowered_column):
                return column
        return None

    def columns_of(self, table_name: str) -> list:
        return [c for c in self.columns if c["table_name"].lower() == table_name.lower()]


def _q(code, *, table=None, column=None, row_ref=None, raw_value=None, detail=None):
    return {"code": code, "stage": reasons.stage_of(code), "level": reasons.level_of(code),
            "table_name": table, "column_name": column, "row_ref": row_ref,
            "raw_value": raw_value, "detail": detail}


def _role_label(is_key: bool, is_metric: bool) -> str:
    if is_key:
        return "键"
    if is_metric:
        return "度量"
    return "属性"


def build(coerced) -> Profiled:
    result = Profiled(quarantine=list(coerced.quarantine))
    for table in coerced.tables:
        key_check = rules.declared_key_check(table)
        key_columns = {name.lower() for name in table.declared_pk}
        foreign_columns = {name.lower() for fk in table.foreign_keys
                           for name in fk["columns"]}
        table_columns = []
        for column in table.columns:
            stats = profile.column_stats(column)
            is_key = rules.is_key_name(column.name)
            is_metric = rules.is_metric(column, foreign_columns)
            disposition, codes = rules.disposition(
                stats, key_conflict=key_check["conflict"],
                is_key_column=column.name.lower() in key_columns,
                column_name=column.name)
            reason_codes = sorted(set(list(codes) + list(column.codes)))
            entry = {
                "table_name": table.name,
                "column_name": column.name,
                "ordinal": column.ordinal,
                "ddl_type": column.ddl_type,
                "base_type": column.base_type,
                "width": column.width,
                "nullable": column.nullable,
                "comment": column.comment,
                "kind": stats["kind"],
                "sample_rows": stats["sample_rows"],
                "null_total": stats["null_total"],
                "empty_count": stats["empty_count"],
                "sentinel_count": stats["sentinel_count"],
                "distinct_count": stats["distinct_count"],
                "unparsable": stats["unparsable"],
                "unparsable_rate": stats["unparsable_rate"],
                "null_rate": stats["null_rate"],
                "unique_rate": stats["unique_rate"],
                "metric_total": stats["metric_total"],
                "measurement": _role_label(is_key, is_metric),
                "disposition": disposition,
                "reason_codes": reason_codes,
                "sample_value": stats["sample_value"],
                "is_key_name": is_key,
                "is_metric": is_metric,
                "in_declared_pk": column.name.lower() in key_columns,
                "evidence": {
                    "kind": stats["kind"],
                    "sample_rows": stats["sample_rows"],
                    "null_total": stats["null_total"],
                    "empty_count": stats["empty_count"],
                    "sentinel_count": stats["sentinel_count"],
                    "distinct_count": stats["distinct_count"],
                    "null_rate": str(stats["null_rate"]),
                    "unique_rate": str(stats["unique_rate"]),
                    "declared_type": column.ddl_type,
                    "widened_from": column.widened_from,
                    "padded_examples": list(column.padded_examples),
                    "present": stats["present"],
                },
            }
            table_columns.append(entry)
            result.columns.append(entry)
            for code in reason_codes:
                if code == "Q_SENTINEL_VALUE" and column.sentinel_count:
                    result.quarantine.append(_q(
                        code, table=table.name, column=column.name,
                        raw_value=column.sample_value,
                        detail=f"哨兵 {column.sentinel_count} 个按空值计"))
                elif code == "Q_ALL_NULL":
                    result.quarantine.append(_q(
                        code, table=table.name, column=column.name,
                        detail=f"样本行数 {stats['sample_rows']}，非空取值 0"))
                elif code == "Q_SAMPLE_SHORT":
                    result.quarantine.append(_q(
                        code, table=table.name, column=column.name,
                        detail=f"样本行数 {stats['sample_rows']}"))
                elif code == "Q_LOW_UNIQUE":
                    result.quarantine.append(_q(
                        code, table=table.name, column=column.name,
                        detail=f"唯一率 {stats['unique_rate']}"))
                elif code == "E_TYPE_WIDENED":
                    widened = column.widened_from or (
                        f"{column.ddl_type} → {stats['kind']}")
                    result.quarantine.append(_q(
                        code, table=table.name, column=column.name,
                        raw_value=column.sample_value, detail=widened))
        if key_check["conflict"]:
            for duplicate in key_check["duplicates"]:
                first = table.rows_norm[duplicate["first_row"]]
                other = table.rows_norm[duplicate["dup_row"]]
                kept = {name: first.get(name) for name in key_check["declared"]}
                dropped = {name: other.get(name) for name in key_check["declared"]}
                # 冲突证据要带上取值不同的那几列，否则看不出冲突在哪
                for column in table.columns:
                    if column.name in kept:
                        continue
                    left, right = first.get(column.name), other.get(column.name)
                    if left != right:
                        kept[column.name] = left
                        dropped[column.name] = right
                result.conflicts.append({
                    "table_name": table.name,
                    "key_columns": list(key_check["declared"]),
                    "key_value": "|".join(str(value) for value in duplicate["key"]),
                    "kept": kept, "dropped": dropped,
                    "note": f"首见行 {table.valid_lines[duplicate['first_row']]}，"
                            f"冲突行 {table.valid_lines[duplicate['dup_row']]}",
                })
            result.quarantine.append(_q(
                "K_DUP_KEY_CONFLICT", table=table.name,
                column="、".join(key_check["declared"]),
                detail=(f"键组合去重 {key_check['unique']} / {key_check['counted']}，"
                        f"冲突 {len(key_check['duplicates'])} 组")))
            for name in key_check["declared"]:
                entry = next((c for c in table_columns
                              if c["column_name"].lower() == name.lower()), None)
                if entry is not None and "Q_KEY_COLUMN_PENDING" not in entry["reason_codes"]:
                    entry["reason_codes"] = sorted(
                        entry["reason_codes"] + ["Q_KEY_COLUMN_PENDING"])
        result.tables.append({
            "table_name": table.name,
            "ordinal": table.ordinal,
            "sample_rows": len(table.rows_norm),
            "column_count": len(table_columns),
            "declared_pk": list(table.declared_pk),
            "sample_rows_total": table.row_total,
            "valid_rows": len(table.rows_norm),
            "null_row": table.null_row_count,
            "quarantined": table.quarantined_count,
            "case_conflicts": table.case_conflicts,
            "column_widenings": table.column_widenings,
            "key_check": {
                "declared": key_check["declared"],
                "unique": key_check["unique"],
                "counted": key_check["counted"],
                "conflict": key_check["conflict"],
            },
        })
        result.row_ledger.append({
            "table_name": table.name,
            "total": table.row_total,
            "valid": len(table.rows_norm),
            "null_row": table.null_row_count,
            "quarantined": table.quarantined_count,
        })
    entries = {(entry["table_name"], entry["column_name"]): entry
               for entry in result.columns}
    result.metric_sums = profile.table_totals(coerced.tables, entries)
    result.codes = sorted({code for column in result.columns
                           for code in column["reason_codes"]}
                          | {row["code"] for row in result.quarantine})
    return result


def view(columns: list, *, disposition: str = "", table: str = "",
         code: str = "") -> dict:
    """列剖析页/接口的筛选与汇总。"""
    rows = profile.decorate(list(columns))
    if disposition:
        rows = [row for row in rows if row["disposition"] == disposition]
    if table:
        rows = [row for row in rows if row["table_name"].lower() == table.lower()]
    if code:
        rows = [row for row in rows if code in row["reason_codes"]]
    payload = profile.summary(rows)
    payload["rows"] = [_serialize(row) for row in rows]
    payload["filters"] = {"disposition": disposition, "table_name": table, "code": code}
    payload["labels"] = config.DISPOSITION_LABELS
    return payload


def _serialize(row: dict) -> dict:
    out = dict(row)
    out.pop("evidence", None)
    out["null_rate"] = str(row["null_rate"])
    out["unique_rate"] = str(row["unique_rate"])
    out["unparsable_rate"] = str(row["unparsable_rate"])
    out["metric_total"] = None if row["metric_total"] is None else str(row["metric_total"])
    return out
