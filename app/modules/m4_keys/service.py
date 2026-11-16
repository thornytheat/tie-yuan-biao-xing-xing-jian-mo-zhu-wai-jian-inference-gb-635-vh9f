"""M4 编排：主键 → 外键（声明 + 推断）→ 星型对象 → 退化维度 → 引用账。"""
from __future__ import annotations

from dataclasses import dataclass, field

from ... import reasons
from . import evidence, keys, star


@dataclass
class Modeled:
    keys: list = field(default_factory=list)
    star: list = field(default_factory=list)
    quarantine: list = field(default_factory=list)
    codes: list = field(default_factory=list)

    def fk_of(self, table_name: str) -> list:
        lowered = table_name.lower()
        return [item for item in self.keys
                if item["kind"] == "fk" and item["child_table"].lower() == lowered
                and item["status"] in ("confirmed", "inferred")]

    def pk_of(self, table_name: str) -> dict | None:
        lowered = table_name.lower()
        for item in self.keys:
            if item["kind"] == "pk" and item["child_table"].lower() == lowered:
                return item
        return None


def _q(code, *, table=None, column=None, detail=None):
    return {"code": code, "stage": reasons.stage_of(code), "level": reasons.level_of(code),
            "table_name": table, "column_name": column, "row_ref": None,
            "raw_value": None, "detail": detail}


def build(coerced, profiled) -> Modeled:
    result = Modeled(quarantine=[])
    stats_by_table: dict[str, dict] = {}
    for column in profiled.columns:
        stats_by_table.setdefault(column["table_name"].lower(), {})[
            column["column_name"].lower()] = column

    pk_findings = []
    for table in coerced.tables:
        pk = keys.pk_finding(table, stats_by_table.get(table.name.lower(), {}))
        pk["note"] = evidence.pk_note(pk)
        pk_findings.append(pk)
        if pk["status"] == "none":
            result.quarantine.append(_q("K_NO_PK", table=table.name,
                                        detail="无声明主键，也没有单列候选"))
    pk_by_table = {item["child_table"].lower(): item for item in pk_findings}

    tables_by_name = {table.name.lower(): table for table in coerced.tables}
    fk_findings = []
    for table in coerced.tables:
        for foreign_key in table.foreign_keys:
            finding = keys.fk_from_declaration(table, foreign_key, tables_by_name,
                                               pk_by_table)
            finding["note"] = evidence.fk_note(finding)
            fk_findings.append(finding)
    declared_pairs = {(item["child_table"].lower(), item["child_columns"][0].lower())
                      for item in fk_findings}
    for child, child_column, parent, parent_column in keys.key_pairs(coerced.tables):
        if (child.name.lower(), child_column.name.lower()) in declared_pairs:
            continue
        finding = keys.fk_inferred(child, child_column, parent, parent_column,
                                   pk_by_table.get(parent.name.lower(), {}))
        if finding is None:
            continue
        finding["note"] = evidence.fk_note(finding)
        fk_findings.append(finding)

    for finding in fk_findings:
        if finding["status"] == "unidirectional":
            result.quarantine.append(_q(
                "K_FK_UNIDIRECTIONAL", table=finding["child_table"],
                column=finding["child_columns"][0],
                detail=(f"声明指向 {finding['parent_table']}，"
                        f"包含度 {finding['inclusion']}")))
        elif finding["status"] == "inferred":
            result.quarantine.append(_q(
                "K_FK_INFERRED", table=finding["child_table"],
                column=finding["child_columns"][0],
                detail=f"推断指向 {finding['parent_table']}，需补进 DDL"))
        elif finding["status"] == "rejected":
            result.quarantine.append(_q(
                finding["reason_code"], table=finding["child_table"],
                column=finding["child_columns"][0],
                detail=f"包含度 {finding['inclusion']}，基数比 {finding['cardinality_ratio']}"))

    result.keys = pk_findings + fk_findings

    referenced = set()
    for finding in fk_findings:
        if finding["status"] in ("confirmed", "inferred", "unidirectional") and \
                finding["parent_table"]:
            referenced.add(finding["parent_table"].lower())

    for table in coerced.tables:
        stats = stats_by_table.get(table.name.lower(), {})
        fk_items = [item for item in fk_findings
                    if item["child_table"].lower() == table.name.lower()
                    and item["status"] in ("confirmed", "inferred")]
        fk_columns = {item["child_columns"][0].lower() for item in fk_items}
        metrics = star.metric_columns(table, stats)
        pk = pk_by_table.get(table.name.lower(), {})
        pk_columns = [name.lower() for name in pk.get("child_columns", [])]
        key_conflict = pk.get("status") == "conflict"
        role, code, role_evidence = star.role_of(
            table=table, metrics=metrics, fk_count=len(fk_items),
            referenced=table.name.lower() in referenced,
            key_conflict=key_conflict, has_pk=bool(pk_columns))
        scd, scd_code, scd_evidence = ("", "", {})
        if role == "dim":
            scd, scd_code, scd_evidence = star.scd_of(
                table, set(pk_columns), fk_columns | {c.lower() for c in fk_columns})
            if scd == "SCD0":
                result.quarantine.append(_q(
                    "K_NO_VERSION_COLUMN", table=table.name,
                    detail="无 updated_at，按静态维度处理"))
        degenerate = []
        if role == "fact":
            kind_by_name = {name.lower(): item.get("kind", "")
                            for name, item in stats.items()}
            degenerate = star.degenerate_of(table, pk.get("child_columns", []),
                                            fk_columns, kind_by_name)
            for item in degenerate:
                result.quarantine.append(_q(
                    "K_DEGENERATE", table=table.name, column=item["column"],
                    detail=f"{item['kind']} 列，作退化维度"))
            if any(column.name.lower().endswith(("_time", "_date"))
                   for column in table.columns):
                result.quarantine.append(_q(
                    "K_DATE_GAIN", table=table.name,
                    detail="含日期/时间列，可关联日期维"))
        row = {
            "table_name": table.name,
            "role": role,
            "role_reason": code,
            "scd": scd,
            "scd_reason": scd_code if role == "dim" else "",
            "reason_code": code,
            "metric_columns": metrics,
            "metric_count": len(metrics),
            "fk_count": len(fk_items),
            "fk_columns": sorted(fk_columns),
            "degenerate": degenerate,
            "scd_evidence": scd_evidence,
            "evidence": {
                **role_evidence,
                "metrics": [item["column"] for item in metrics],
                "scd": scd_evidence,
            },
        }
        result.star.append(row)

    result.codes = sorted({row["code"] for row in result.quarantine} |
                          {item.get("reason_code", "") for item in result.keys} |
                          {item.get("role_reason", "") for item in result.star})
    result.codes = [code for code in result.codes if code]
    return result


def view(modeled: Modeled) -> dict:
    return {
        "keys": [_serialize_key(item) for item in modeled.keys],
        "star": [_serialize_star(item) for item in modeled.star],
    }


def _serialize_key(item: dict) -> dict:
    out = dict(item)
    out["inclusion"] = None if item["inclusion"] is None else str(item["inclusion"])
    out["cardinality_ratio"] = (None if item["cardinality_ratio"] is None
                                else str(item["cardinality_ratio"]))
    return out


def _serialize_star(item: dict) -> dict:
    out = dict(item)
    out["evidence"] = dict(item["evidence"])
    return out
