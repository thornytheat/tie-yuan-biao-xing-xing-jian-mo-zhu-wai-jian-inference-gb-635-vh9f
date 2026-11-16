"""M7 规则二：迁移顺序。

顺序不是按书写顺序，而是按依赖边做拓扑排序：
- 同表内按阶段（清洗 → 类型 → 改名 → 键 → 引用）；
- 外键依赖父表先有键（父表加唯一约束在前，子表加外键在后）；
- 成环的变更整组阻断，并给出环路径，不许按某个顺序硬排。
"""
from __future__ import annotations

from . import impact

PHASE_RANK = {name: index for index, name in enumerate(impact.PHASES)}
KIND_RANK = {kind: index for index, kind in enumerate(impact.KINDS)}

CYCLE_CODE = "M_MIGRATION_CYCLE"
BLOCKED_CODE = "M_MIGRATION_BLOCKED"


def priority(change: dict) -> tuple:
    """同层内的稳定次序：阶段 → 类型 → 表 → 列，与派生顺序无关。"""
    return (PHASE_RANK.get(change["phase"], 9),
            KIND_RANK.get(change["kind"], 9),
            change["table"],
            "|".join(change["columns"]))


def dependencies(changes: list, keys: list) -> dict:
    """返回 {change_id: 前置 change_id 集合}。"""
    by_id = {change["change_id"]: change for change in changes}
    deps = {change_id: set() for change_id in by_id}
    ordered = sorted(changes, key=priority)

    by_table: dict[str, list] = {}
    for change in ordered:
        by_table.setdefault(change["table"].lower(), []).append(change)

    for table_changes in by_table.values():
        for index, later in enumerate(table_changes):
            for earlier in table_changes[:index]:
                deps[later["change_id"]].add(earlier["change_id"])

    for change in ordered:
        if change["kind"] != "add_fk":
            continue
        target = change.get("target") or {}
        parent = (target.get("table") or "").lower()
        parent_columns = {name.lower() for name in target.get("columns") or []}
        for candidate in by_table.get(parent, []):
            if candidate["change_id"] == change["change_id"]:
                continue
            if candidate["kind"] not in ("add_unique", "alter_type", "dedupe_rows"):
                continue
            touched = {name.lower() for name in candidate["columns"]}
            if candidate["kind"] == "add_unique" or touched & parent_columns:
                deps[change["change_id"]].add(candidate["change_id"])

    for change in ordered:
        if change["kind"] != "add_unique":
            continue
        table = change["table"].lower()
        touched = {name.lower() for name in change["columns"]}
        for candidate in by_table.get(table, []):
            if candidate["kind"] not in ("dedupe_rows", "fix_orphan", "alter_type"):
                continue
            if {name.lower() for name in candidate["columns"]} & touched:
                deps[change["change_id"]].add(candidate["change_id"])
    return deps


def order(changes: list, deps: dict) -> dict:
    """Kahn 拓扑排序；同层按 priority 取，剩余节点即环。"""
    by_id = {change["change_id"]: change for change in changes}
    pending = {change_id: set(prereq) & set(by_id)
               for change_id, prereq in deps.items()}
    layers: list = []
    order_ids: list = []
    ready = sorted([change_id for change_id, prereq in pending.items() if not prereq],
                   key=lambda change_id: priority(by_id[change_id]))
    while ready:
        layers.append(list(ready))
        order_ids.extend(ready)
        for change_id in ready:
            pending.pop(change_id, None)
        for remaining in pending.values():
            remaining.difference_update(ready)
        ready = sorted([change_id for change_id, prereq in pending.items() if not prereq],
                       key=lambda change_id: priority(by_id[change_id]))
    cycles = _cycles(sorted(pending), deps)
    return {"order": order_ids, "layers": layers, "cycles": cycles,
            "unresolved": sorted(pending)}


def _cycles(nodes: list, deps: dict) -> list:
    """同一个环只报一条代表路径，避免一个环被数成多条。"""
    remaining = set(nodes)
    seen: set = set()
    keys: set = set()
    cycles: list = []
    for start in sorted(remaining):
        if start in seen:
            continue
        path: list = []
        index: dict = {}
        node = start
        while node is not None and node in remaining and node not in index:
            index[node] = len(path)
            path.append(node)
            nxt = sorted(item for item in deps.get(node, ()) if item in remaining)
            node = nxt[0] if nxt else None
        if node is not None and node in index:
            cycle = path[index[node]:] + [node]
            key = tuple(sorted(set(cycle)))
            if key not in keys:
                keys.add(key)
                cycles.append(cycle)
        seen.update(path)
    return cycles


def blockers(changes: list, deps: dict, seeds: dict) -> list:
    """把规则层的阻断沿前置边传下去：前置不可执行，后面的也不许排。"""
    by_id = {change["change_id"]: change for change in changes}
    blocked = {change_id: dict(info) for change_id, info in seeds.items()}
    changed = True
    while changed:
        changed = False
        for change in changes:
            change_id = change["change_id"]
            if change_id in blocked:
                continue
            for prereq in deps.get(change_id, ()):
                if prereq in blocked:
                    blocked[change_id] = {
                        "code": BLOCKED_CODE,
                        "reason": f"前置 {by_id[prereq]['label']} 已阻断",
                    }
                    changed = True
                    break
    return [{"change_id": change_id, **info}
            for change_id, info in sorted(blocked.items())]
