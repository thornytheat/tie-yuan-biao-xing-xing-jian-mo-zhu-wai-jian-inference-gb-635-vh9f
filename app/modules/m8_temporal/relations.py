"""关系版本政策：每条外键关系用哪一版维度、为什么，规格单与页面共用。

判定顺序固定，命中即停：
1. 子表是维度 → 当前版：维度间引用本期不做时点关联；
2. 父表无版本列 → 当前版：没有版本链可关联（记 T_DIM_NO_VERSION）；
3. 子表无业务时刻列 → 当前版：没有可对齐的时刻；
4. 其余 → 时点版：按子表业务时刻关联父维当时版本（记 T_REL_AS_OF）。

时点版关系逐事实行核验：命中版本区间 / 早于首版兜底 / 维度无此键，
计数写进关系行，规格单里的「为什么」与页面上的数是同一组。
"""
from __future__ import annotations

from ... import config
from ..m4_keys.star import VERSION_COLUMNS
from . import bitemporal

POLICY_LABELS = {"as_of": "时点版", "current": "当前版"}


def version_column_of(columns: list) -> str | None:
    """父维的版本列：按登记顺序取第一个出现的。"""
    names = {column["column_name"].lower(): column["column_name"]
             for column in columns}
    for candidate in VERSION_COLUMNS:
        if candidate in names:
            return names[candidate]
    return None


def business_time_column(table_name: str, columns: list, *,
                         temporal_table: str, effective_column: str) -> str | None:
    """子表的业务时刻列：双时间表用生效时间列，其余找 _time/_date 时刻列。"""
    if table_name.lower() == temporal_table.lower():
        return effective_column
    for column in columns:
        name = column["column_name"].lower()
        if name in VERSION_COLUMNS:
            continue
        if column["kind"] in ("timestamp", "date") and name.endswith(("_time", "_date")):
            return column["column_name"]
    return None


def _fact_moment(row: dict, column: str, *, day_end: bool) -> str | None:
    value = row.get(column)
    if value is None:
        return None
    text = str(value)
    return f"{text} {config.TEMPORAL_DAY_END}" if day_end else text


def build(*, keys: list, star: list, columns: list, rows_by_table: dict,
          temporal_table: str, effective_column: str) -> list:
    """对每条可用外键（一致/推断）给出版本政策并核验。"""
    columns_by_table: dict[str, list] = {}
    for column in columns:
        columns_by_table.setdefault(column["table_name"], []).append(column)
    role_by_table = {row["table_name"]: row["role"] for row in star}
    relations = []
    findings = [item for item in keys
                if item["kind"] == "fk" and item["status"] in ("confirmed", "inferred")]
    for finding in findings:
        child = finding["child_table"]
        parent = finding["parent_table"]
        child_column = finding["child_columns"][0]
        parent_column = (finding["parent_columns"] or [""])[0]
        child_columns = columns_by_table.get(child, [])
        parent_columns = columns_by_table.get(parent, [])
        parent_version = version_column_of(parent_columns)
        child_role = role_by_table.get(child, "pending")
        is_fact = child_role == "fact" or child.lower() == temporal_table.lower()

        policy = "current"
        reason_code = "T_REL_CURRENT"
        as_of_column = None
        check = None
        if not is_fact:
            if child_role == "dim":
                why = "子表是维度，维度间引用本期按当前版，不做时点关联"
            else:
                why = "子表角色待定，先定角色再谈时点关联，本期按当前版"
        elif parent_version is None:
            reason_code = "T_DIM_NO_VERSION"
            why = "父维没有版本列，建不出版本链，只能按当前版"
        else:
            as_of_column = business_time_column(
                child, child_columns, temporal_table=temporal_table,
                effective_column=effective_column)
            if as_of_column is None:
                why = "子表没有业务时刻列，没有可对齐的时刻，按当前版"
            else:
                policy = "as_of"
                reason_code = "T_REL_AS_OF"
                day_end = child.lower() == temporal_table.lower()
                why = (f"父维有版本列 {parent_version}，子表业务时刻 "
                       f"{as_of_column}{'（按日末对齐）' if day_end else ''}，"
                       f"归属按发生时刻取当时版本")
                chains = bitemporal.dim_versions(
                    rows_by_table.get(parent, []), key_column=parent_column,
                    ingest_column=parent_version)
                facts = []
                for row in rows_by_table.get(child, []):
                    moment = _fact_moment(row, as_of_column, day_end=day_end)
                    if row.get(child_column) is None or moment is None:
                        continue
                    facts.append({"key": row.get(child_column), "moment": moment})
                check = bitemporal.resolve_as_of(facts, chains)["counts"]
        relations.append({
            "child_table": child,
            "child_column": child_column,
            "parent_table": parent,
            "parent_column": parent_column,
            "declared": bool(finding.get("declared")),
            "status": finding["status"],
            "policy": policy,
            "policy_label": POLICY_LABELS[policy],
            "as_of_column": as_of_column,
            "reason_code": reason_code,
            "why": why,
            "check": check,
        })
    relations.sort(key=lambda item: (item["child_table"], item["child_column"]))
    return relations
