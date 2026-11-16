"""M7 规则一：变更派生、下游引用匹配、唯一性判定。

三条规则在此实现，纯函数，不读库不读文件：

1. 唯一性判定先定空值策略。SQL 的 UNIQUE 让含空值的键免检，把空值当普通值会得到
   相反结论；有效行为 0 时不能判「唯一」。
2. 组合键含空值成员时两种策略结论不同，变更不许静默选一种，必须阻断待裁定。
3. 列名仅大小写不同不等于同一列。折叠比较会得到「合并后仍唯一」的伪唯一结论，
   而被折叠掉的那一列可能正被下游引用。
"""
from __future__ import annotations

import csv
import io

PHASES = ("data_fix", "type", "rename", "key", "ref")
PHASE_LABELS = {"data_fix": "清洗", "type": "类型", "rename": "改名",
                "key": "键", "ref": "引用"}
LEVEL_LABELS = {"break": "中断", "review": "核对", "info": "留痕"}
LEVEL_ORDER = ("break", "review", "info")

KINDS = {
    "dedupe_rows": {"label": "去重行", "phase": "data_fix", "breaking": True},
    "fix_orphan": {"label": "清洗孤儿值", "phase": "data_fix", "breaking": False},
    "alter_type": {"label": "改类型", "phase": "type", "breaking": True},
    "rename_column": {"label": "改列名", "phase": "rename", "breaking": True},
    "add_unique": {"label": "加唯一约束", "phase": "key", "breaking": False},
    "add_fk": {"label": "加外键", "phase": "ref", "breaking": False},
}

REGISTRY_HEADER = ("object_id", "object_type", "object_name", "owner", "criticality",
                   "ref_table", "ref_column", "ref_mode")
CRITICALITY = ("P1", "P2", "P3")


# ---------- 变更对象 ----------

def change_id(kind: str, table: str, columns: list) -> str:
    return f"{kind}:{table}:{'|'.join(columns)}"


def make_change(kind: str, table: str, columns: list, *, note: str = "",
                reason_code: str = "M_CHANGE_DERIVED", target: dict | None = None,
                narrowing: bool = False) -> dict:
    spec = KINDS[kind]
    return {
        "change_id": change_id(kind, table, columns),
        "kind": kind,
        "label": spec["label"],
        "table": table,
        "columns": list(columns),
        "phase": spec["phase"],
        "phase_label": PHASE_LABELS[spec["phase"]],
        "breaking": spec["breaking"],
        "narrowing": narrowing,
        "reason_code": reason_code,
        "note": note,
        "target": dict(target or {}),
    }


def derive(bundle: dict, coerced) -> list:
    """从本轮结论派生建议变更；顺序固定，同输入必然得到同一串变更。"""
    changes: list = []
    seen: set = set()

    def add(kind, table, columns, **kwargs):
        item = make_change(kind, table, columns, **kwargs)
        if item["change_id"] in seen:
            return
        seen.add(item["change_id"])
        changes.append(item)

    tables = {row["table_name"].lower(): row for row in bundle["tables"]}
    pks = {row["child_table"].lower(): row for row in bundle["keys"]
           if row["kind"] == "pk"}
    fks = [row for row in bundle["keys"] if row["kind"] == "fk"]

    for name, pk in pks.items():
        if pk["status"] != "conflict":
            continue
        columns = list(pk["child_columns"])
        table = tables.get(name, {}).get("table_name", pk["child_table"])
        add("dedupe_rows", table, columns,
            note=f"组合键在样本上重复，按 {len(columns)} 列保留首见行")
        add("add_unique", table, columns,
            note="去重后再加唯一约束，顺序不可颠倒")

    for fk in fks:
        child = tables.get(fk["child_table"].lower(), {}).get(
            "table_name", fk["child_table"])
        column = fk["child_columns"][0] if fk["child_columns"] else ""
        if not column:
            continue
        if fk["status"] == "unidirectional":
            add("fix_orphan", child, [column],
                note=f"父表 {fk['parent_table']} 缺值，包含度 {fk['inclusion']}")
        elif fk["status"] == "inferred" and not fk["declared"]:
            add("add_fk", child, [column],
                note=f"指向 {fk['parent_table']}，需补进 DDL",
                target={"table": fk["parent_table"],
                        "columns": list(fk["parent_columns"])})

    for column in bundle["columns"]:
        if "E_TYPE_WIDENED" not in (column.get("reason_codes") or []):
            continue
        add("alter_type", column["table_name"], [column["column_name"]],
            note=f"声明 {column['ddl_type']}，样本类型 {column['kind']}，跨表取最宽")

    for run in coerced.tables:
        for group in run.case_conflicts:
            if len(group) < 2:
                continue
            keep, fold = group[0], group[1]
            add("rename_column", run.name, [fold],
                note="两列名仅大小写不同，合并前先确认下游引用",
                target={"table": run.name, "columns": [keep]})
    return changes


# ---------- 下游登记表 ----------

def parse_registry(text: str) -> dict:
    """解析下游登记表：列数不齐、级别非法、重复行都记下来，不静默吞掉。"""
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return {"rows": [], "total": 0, "dups": 0, "errors": [],
                "header": [], "missing": list(REGISTRY_HEADER)}
    reader = csv.reader(io.StringIO("\n".join(lines)))
    header = [cell.strip() for cell in next(reader)]
    index = {name: header.index(name) for name in REGISTRY_HEADER if name in header}
    missing = [name for name in REGISTRY_HEADER if name not in index]
    rows, errors, seen = [], [], set()
    for line_no, cells in enumerate(reader, 2):
        if not any(cell.strip() for cell in cells):
            continue
        if missing:
            continue
        if len(cells) < len(header):
            errors.append({"line": line_no, "code": "M_REGISTRY_SHORT_ROW",
                           "detail": f"列数 {len(cells)} 少于表头 {len(header)}"})
            continue
        item = {name: str(cells[index[name]]).strip() for name in REGISTRY_HEADER}
        if item["criticality"] not in CRITICALITY:
            errors.append({"line": line_no, "code": "M_REGISTRY_BAD_LEVEL",
                           "detail": f"优先级 {item['criticality']} 不在 P1..P3"})
            item["criticality"] = "P3"
        item["ref_table"] = item["ref_table"].lower()
        item["ref_column"] = item["ref_column"]
        item["ref_mode"] = item["ref_mode"] or ("column" if item["ref_column"]
                                                else "table")
        key = (item["object_id"], item["ref_table"], item["ref_column"])
        if key in seen:
            errors.append({"line": line_no, "code": "M_REGISTRY_DUP_ROW",
                           "detail": f"{item['object_id']} 重复导出，保留首行"})
            continue
        seen.add(key)
        item["line"] = line_no
        rows.append(item)
    return {
        "rows": rows,
        "total": len(rows),
        "dups": len([row for row in errors if row["code"] == "M_REGISTRY_DUP_ROW"]),
        "errors": errors,
        "header": header,
        "missing": missing,
    }


# ---------- 唯一性：空值策略 ----------

def uniqueness(rows: list, columns: list, null_policy: str = "sql") -> dict:
    """null_policy=sql：含空值的键免检（与 UNIQUE 约束一致）；strict：空值当普通值。"""
    tuples = []
    exempt = 0
    for row in rows:
        values = tuple(row.get(name) for name in columns)
        if null_policy == "sql" and any(value is None for value in values):
            exempt += 1
            continue
        tuples.append(values)
    distinct = len(set(tuples))
    counted = len(tuples)
    return {
        "policy": null_policy,
        "counted": counted,
        "distinct": distinct,
        "exempt": exempt,
        "empty": counted == 0,
        "unique": counted > 0 and distinct == counted,
    }


def verdicts(rows: list, columns: list) -> dict:
    """两种空值策略都算一遍；结论相反时标歧义，交给人工裁定。"""
    sql = uniqueness(rows, columns, "sql")
    strict = uniqueness(rows, columns, "strict")
    return {
        "sql": sql,
        "strict": strict,
        "ambiguous": sql["unique"] != strict["unique"],
        "empty": sql["empty"],
    }


def partial_null_rows(rows: list, columns: list) -> int:
    """组合键里「部分为空」的行数：这是两种策略唯一会分叉的来源。"""
    count = 0
    for row in rows:
        values = [row.get(name) for name in columns]
        nulls = sum(1 for value in values if value is None)
        if 0 < nulls < len(values):
            count += 1
    return count


# ---------- 大小写折叠 ----------

def folded_columns(names: list) -> list:
    """同表内仅大小写不同的列名分组，每组按出现顺序。"""
    groups: dict[str, list] = {}
    for name in names:
        groups.setdefault(str(name).casefold(), []).append(name)
    return [group for group in groups.values() if len(group) > 1]


def case_pseudo_unique(rows: list, keep: str, fold: str) -> dict:
    """把两列折叠成一列后的唯一性：折叠会吃掉取值差异，得到的「唯一」是伪唯一。"""
    keep_values = [row.get(keep) for row in rows if row.get(keep) is not None]
    fold_values = [row.get(fold) for row in rows if row.get(fold) is not None]
    merged = [str(value) for value in keep_values + fold_values]
    strict_distinct = len(set(merged))
    folded_distinct = len({value.casefold() for value in merged})
    folded_unique = bool(merged) and folded_distinct == len(merged)
    pseudo = (folded_distinct < strict_distinct) or (not fold_values and bool(keep_values))
    return {
        "keep": keep,
        "fold": fold,
        "keep_values": len(keep_values),
        "fold_values": len(fold_values),
        "strict_distinct": strict_distinct,
        "folded_distinct": folded_distinct,
        "folded_unique": folded_unique,
        "pseudo": pseudo,
    }


# ---------- 影响面 ----------

def severity(*, breaking: bool, criticality: str, mode: str) -> str:
    if mode in ("case", "table"):
        return "review"
    if mode == "join":
        return "break" if criticality == "P1" else "review"
    if breaking:
        return "break" if criticality == "P1" else "review"
    return "review" if criticality == "P1" else "info"


def join_targets(bundle: dict, change: dict) -> list:
    """父键变更会打断 join：沿引用边找引用子表该列的下游对象。"""
    if change["kind"] not in ("add_unique", "alter_type", "rename_column",
                              "dedupe_rows"):
        return []
    hits = []
    for fk in bundle["keys"]:
        if fk["kind"] != "fk":
            continue
        if (fk["parent_table"] or "").lower() != change["table"].lower():
            continue
        parent_columns = [name.lower() for name in fk.get("parent_columns") or []]
        touched = [name.lower() for name in change["columns"]]
        if not set(parent_columns) & set(touched):
            continue
        hits.append({"table": fk["child_table"], "column": fk["child_columns"][0]})
    return hits


def evaluate(change: dict, registry_rows: list, targets: list) -> dict:
    """一条变更对下游对象的影响：按引用方式分四种匹配，各给级别。"""
    rows, unknown, pseudo = [], [], []
    table = change["table"].lower()
    columns = list(change["columns"])
    lowered = {name.lower(): name for name in columns}
    targets = targets or []
    for entry in registry_rows:
        mode = ""
        if entry["ref_table"] == table and entry["ref_column"]:
            if entry["ref_column"] in columns:
                mode = "column"
            elif entry["ref_column"].lower() in lowered:
                mode = "case"
            else:
                continue
        elif entry["ref_table"] == table:
            mode = "table"
        else:
            for hop in targets:
                if (entry["ref_table"] == hop["table"].lower()
                        and entry["ref_column"] == hop["column"]):
                    mode = "join"
                    break
            if not mode:
                continue
        level = severity(breaking=change["breaking"],
                         criticality=entry["criticality"], mode=mode)
        note = {
            "column": f"引用 {entry['ref_table']}.{entry['ref_column']}",
            "case": f"引用列 {entry['ref_column']} 与 {lowered.get(entry['ref_column'].lower(), '')} 仅大小写不同",
            "table": f"整表引用 {entry['ref_table']}，影响列未知",
            "join": f"经 {entry['ref_table']}.{entry['ref_column']} 关联改动的父键",
        }[mode]
        row = {
            "change_id": change["change_id"],
            "kind": change["kind"],
            "table": change["table"],
            "columns": list(columns),
            "object_id": entry["object_id"],
            "object_type": entry["object_type"],
            "object_name": entry["object_name"],
            "owner": entry["owner"],
            "criticality": entry["criticality"],
            "ref": f"{entry['ref_table']}.{entry['ref_column'] or '*'}",
            "mode": mode,
            "level": level,
            "note": note,
        }
        rows.append(row)
        if mode == "table":
            unknown.append(row)
        if mode == "case":
            pseudo.append(row)
    counts = {level: len([row for row in rows if row["level"] == level])
              for level in LEVEL_ORDER}
    return {"rows": rows, "counts": counts, "unknown": unknown, "pseudo": pseudo}
