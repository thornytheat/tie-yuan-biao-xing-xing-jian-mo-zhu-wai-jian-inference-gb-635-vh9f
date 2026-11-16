"""M7 规则三：回滚提示。

回滚顺序是迁移顺序的逆序；可逆性由变更类型决定，与下游影响无关：
- 约束类可原地回滚（删索引、删外键）；
- 删行与孤儿值清洗改了数据，只能靠备份表还原，标为不可原地回滚；
- 类型收窄后原值已被截断，也必须先快照。
"""
from __future__ import annotations

REVERSIBLE = {
    "dedupe_rows": False,
    "fix_orphan": False,
    "alter_type": True,
    "rename_column": True,
    "add_unique": True,
    "add_fk": True,
}


def _columns(change: dict) -> str:
    return "_".join(change["columns"]) or "all"


def sql_of(change: dict) -> str:
    table = change["table"]
    column = change["columns"][0] if change["columns"] else ""
    if change["kind"] == "add_fk":
        return f"ALTER TABLE {table} DROP CONSTRAINT fk_{table}_{column}"
    if change["kind"] == "add_unique":
        return f"DROP INDEX ux_{table}_{_columns(change)}"
    if change["kind"] == "rename_column":
        target = change.get("target") or {}
        keep = (target.get("columns") or [column])[0]
        return f"ALTER TABLE {table} RENAME COLUMN {keep} TO {column}"
    if change["kind"] == "alter_type":
        return f"ALTER TABLE {table} ALTER COLUMN {column} TYPE 原声明类型"
    if change["kind"] == "dedupe_rows":
        return f"-- 从备份表按 {change['columns']} 重放被删行"
    return f"UPDATE {table} SET {column} = 备份值 WHERE {column} = 清洗值"


def reversible_of(change: dict) -> bool:
    if change["kind"] == "alter_type" and change.get("narrowing"):
        return False
    return REVERSIBLE.get(change["kind"], False)


def needs_snapshot(change: dict) -> bool:
    return not reversible_of(change) or change["kind"] == "alter_type"


def entry(change: dict) -> dict:
    reversible = reversible_of(change)
    if reversible:
        risk = "约束可原地撤销，已入库数据需复核"
    elif change["kind"] == "alter_type":
        risk = "收窄后原值已截断，回滚只能靠备份"
    elif change["kind"] == "dedupe_rows":
        risk = "行已删除，回滚只能靠备份表重放"
    else:
        risk = "原值已改写，回滚只能靠备份表还原"
    return {
        "change_id": change["change_id"],
        "kind": change["kind"],
        "label": change["label"],
        "table": change["table"],
        "columns": list(change["columns"]),
        "reversible": reversible,
        "needs_snapshot": needs_snapshot(change),
        "sql": sql_of(change),
        "risk": risk,
        "code": "M_ROLLBACK_IRREVERSIBLE" if not reversible else "M_ROLLBACK_ORDER",
    }


def plan(changes: list, migration_order: list, blocked_ids: set) -> list:
    """按迁移逆序给出可回滚项；被阻断的变更没执行过，不进回滚清单。"""
    by_id = {change["change_id"]: change for change in changes}
    executable = [change_id for change_id in migration_order
                  if change_id not in blocked_ids and change_id in by_id]
    rows = []
    for position, change_id in enumerate(reversed(executable), 1):
        row = entry(by_id[change_id])
        row["position"] = position
        rows.append(row)
    return rows


def summary(rows: list) -> dict:
    return {
        "total": len(rows),
        "reversible": len([row for row in rows if row["reversible"]]),
        "irreversible": len([row for row in rows if not row["reversible"]]),
        "snapshot": len([row for row in rows if row["needs_snapshot"]]),
    }
