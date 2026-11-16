"""M7 编排：下游登记表 → 派生变更 → 唯一性裁定 → 影响面 → 迁移顺序 → 回滚。

影响评估要行级数据才能判唯一性，所以这里按批次输入重算一遍采信与类型归一
（只读，不落库、不改批次），其余结论全部来自已落库的键与列剖析。
"""
from __future__ import annotations

import pathlib

from ... import errors, manifest
from ..m1_ingest import service as m1_service
from ..m2_contract import service as m2_service
from ..m5_batches import service as batch_service
from . import impact, rollback, topology

REGISTRY_NAME = "downstream_map.csv"
SEED_CODES = ("M_NULL_KEY_AMBIGUOUS", "M_CASE_PSEUDO_UNIQUE", "M_KEY_NO_ROWS",
              "M_MIGRATION_CYCLE")


def registry_path(settings) -> pathlib.Path:
    return pathlib.Path(settings.sample_dir) / REGISTRY_NAME


def load_registry(settings) -> dict:
    path = registry_path(settings)
    if not path.exists():
        return {"name": REGISTRY_NAME, "digest": "", "rows": [], "total": 0,
                "dups": 0, "errors": [{"line": 0, "code": "M_REGISTRY_BAD_LEVEL",
                                       "detail": f"缺少 {REGISTRY_NAME}"}],
                "header": [], "missing": list(impact.REGISTRY_HEADER)}
    raw = path.read_bytes()
    parsed = impact.parse_registry(raw.decode("utf-8-sig"))
    parsed["name"] = REGISTRY_NAME
    parsed["digest"] = manifest.digest_bytes(raw)
    return parsed


def context(settings, store, batch_id: str) -> dict:
    """一次装配：批次详情 + 行级数据 + 下游登记表。"""
    bundle = batch_service.bundle(store, batch_id)
    intake = m1_service.intake(settings)
    coerced = m2_service.build(intake, settings)
    return {
        "bundle": bundle,
        "coerced": coerced,
        "rows_by_table": {table.name: table.rows_norm for table in coerced.tables},
        "registry": load_registry(settings),
    }


def _seeds(changes: list, rows_by_table: dict) -> dict:
    """规则层阻断：唯一性结论随空值策略翻转、大小写折叠伪唯一、有效行 0。"""
    seeds: dict = {}
    for change in changes:
        rows = rows_by_table.get(change["table"], [])
        if change["kind"] == "add_unique":
            verdict = impact.verdicts(rows, change["columns"])
            partial = impact.partial_null_rows(rows, change["columns"])
            if verdict["ambiguous"] and partial:
                seeds[change["change_id"]] = {
                    "code": "M_NULL_KEY_AMBIGUOUS",
                    "reason": (f"组合键有 {partial} 行部分为空，"
                               f"免检 {verdict['sql']['counted']} 行、"
                               f"全算 {verdict['strict']['distinct']} 去重"),
                }
            elif verdict["empty"]:
                seeds[change["change_id"]] = {
                    "code": "M_KEY_NO_ROWS",
                    "reason": "有效行 0，不能据此判唯一",
                }
        elif change["kind"] == "rename_column":
            target = change.get("target") or {}
            keep = (target.get("columns") or change["columns"])[0]
            folded = impact.case_pseudo_unique(rows, keep, change["columns"][0])
            if folded["pseudo"]:
                seeds[change["change_id"]] = {
                    "code": "M_CASE_PSEUDO_UNIQUE",
                    "reason": (f"折叠后去重 {folded['folded_distinct']}，"
                               f"被折叠列有值 {folded['fold_values']} 个"),
                }
    return seeds


def plan(*, bundle: dict, changes: list, rows_by_table: dict, registry: dict) -> dict:
    seeds = _seeds(changes, rows_by_table)
    deps = topology.dependencies(changes, bundle["keys"])
    ordered = topology.order(changes, deps)
    for cycle in ordered["cycles"]:
        for change_id in cycle:
            seeds.setdefault(change_id, {
                "code": topology.CYCLE_CODE,
                "reason": "依赖成环：" + " → ".join(cycle),
            })
    blocked = topology.blockers(changes, deps, seeds)
    blocked_ids = {row["change_id"] for row in blocked}

    evaluated = []
    unknown: list = []
    pseudo: list = []
    for change in changes:
        targets = impact.join_targets(bundle, change)
        result = impact.evaluate(change, registry["rows"], targets)
        evaluated.append({
            "change": change,
            "rows": result["rows"],
            "counts": result["counts"],
            "objects": len({row["object_id"] for row in result["rows"]}),
            "level": _level(result["counts"]),
            "blocked": next((row for row in blocked
                             if row["change_id"] == change["change_id"]), None),
        })
        unknown.extend(result["unknown"])
        pseudo.extend(result["pseudo"])

    order_rows = []
    by_id = {change["change_id"]: change for change in changes}
    for position, change_id in enumerate(
            [item for item in ordered["order"] if item not in blocked_ids], 1):
        change = by_id[change_id]
        order_rows.append({
            "position": position,
            "change_id": change_id,
            "label": change["label"],
            "phase": change["phase"],
            "phase_label": change["phase_label"],
            "table": change["table"],
            "columns": change["columns"],
            "breaking": change["breaking"],
        })

    rollback_rows = rollback.plan(changes, ordered["order"], blocked_ids)
    summary = _summary(changes, order_rows, blocked, evaluated, unknown, pseudo,
                       rollback_rows, ordered, registry)
    return {
        "batch_id": bundle["batch"]["batch_id"],
        "batch_key": bundle["batch"]["batch_key"],
        "registry": {key: registry.get(key) for key in
                     ("name", "digest", "total", "dups", "errors")},
        "changes": evaluated,
        "order": order_rows,
        "blocked": blocked,
        "cycles": ordered["cycles"],
        "rollback": rollback_rows,
        "unknown": unknown,
        "pseudo": pseudo,
        "summary": summary,
        "notes": _notes(summary, len(rollback_rows)),
    }


def _level(counts: dict) -> str:
    for level in impact.LEVEL_ORDER:
        if counts.get(level):
            return level
    return ""


def _summary(changes, order_rows, blocked, evaluated, unknown, pseudo, rollback_rows,
             ordered, registry) -> dict:
    objects = {row["object_id"] for item in evaluated for row in item["rows"]}
    levels = {level: 0 for level in impact.LEVEL_ORDER}
    for item in evaluated:
        for level in impact.LEVEL_ORDER:
            levels[level] += item["counts"][level]
    return {
        "changes": len(changes),
        "executable": len(order_rows),
        "blocked": len(blocked),
        "cycles": len(ordered["cycles"]),
        "objects": len(objects),
        "break": levels["break"],
        "review": levels["review"],
        "info": levels["info"],
        "unknown": len(unknown),
        "pseudo": len(pseudo),
        "irreversible": len([row for row in rollback_rows if not row["reversible"]]),
        "registry_rows": registry.get("total", 0),
        "registry_dups": registry.get("dups", 0),
        "balanced": len(changes) == len(order_rows) + len(blocked),
    }


def _notes(summary: dict, rollback_total: int) -> list:
    """脚注：数字都在句子里，方便只凭页面复核。"""
    return [
        f"下游登记 {summary['registry_rows']} 行，重复导出 {summary['registry_dups']} 行",
        f"变更 {summary['changes']} 条：可执行 {summary['executable']}，"
        f"阻断 {summary['blocked']}",
        f"下游对象 {summary['objects']} 个：中断 {summary['break']}，"
        f"核对 {summary['review']}",
        f"回滚 {rollback_total} 条，不可原地回滚 {summary['irreversible']} 条",
    ]


def validate(candidate: list, bundle: dict) -> list:
    """校验提交的变更清单：类型、表、列都要真实存在，否则 4xx。"""
    if not isinstance(candidate, list) or not candidate:
        raise errors.BadRequest("changes 必须是非空数组", code="M_BAD_CHANGE")
    tables = {row["table_name"]: row for row in bundle["tables"]}
    changes, seen = [], set()
    for index, item in enumerate(candidate, 1):
        if not isinstance(item, dict):
            raise errors.BadRequest(f"第 {index} 项不是对象", code="M_BAD_CHANGE")
        kind = str(item.get("kind") or "").strip()
        table = str(item.get("table") or "").strip()
        columns = item.get("columns") or []
        if kind not in impact.KINDS:
            raise errors.BadRequest(
                f"第 {index} 项变更类型 {kind or '空'} 未登记", code="M_BAD_CHANGE")
        if table not in tables:
            raise errors.BadRequest(
                f"第 {index} 项表 {table or '空'} 不在本批次", code="M_BAD_CHANGE")
        if not isinstance(columns, list) or not columns:
            raise errors.BadRequest(f"第 {index} 项缺少列", code="M_BAD_CHANGE")
        known = {row["column_name"] for row in bundle["columns"]
                 if row["table_name"] == table}
        unknown = [name for name in columns if name not in known]
        if unknown:
            raise errors.BadRequest(
                f"第 {index} 项列 {unknown[0]} 不在 {table}", code="M_BAD_CHANGE")
        change = impact.make_change(kind, table, [str(name) for name in columns],
                                    note=str(item.get("note") or "外部提交"),
                                    reason_code="M_CHANGE_DERIVED",
                                    target=item.get("target") or {},
                                    narrowing=bool(item.get("narrowing")))
        if change["change_id"] in seen:
            continue
        seen.add(change["change_id"])
        changes.append(change)
    return changes


def build(settings, store, batch_id: str) -> dict:
    """派生变更的影响评估（页面与 /api/impact 共用）。"""
    parts = context(settings, store, batch_id)
    changes = impact.derive(parts["bundle"], parts["coerced"])
    payload = plan(bundle=parts["bundle"], changes=changes,
                   rows_by_table=parts["rows_by_table"], registry=parts["registry"])
    payload["source"] = "derived"
    if not changes:
        payload["notes"] = ["本轮结论没有可执行的变更建议"] + payload["notes"]
    return payload


def propose(settings, store, batch_id: str, candidate: list) -> dict:
    """按提交的变更清单重算影响面：同一套规则，不另写一份。"""
    parts = context(settings, store, batch_id)
    changes = validate(candidate, parts["bundle"])
    payload = plan(bundle=parts["bundle"], changes=changes,
                   rows_by_table=parts["rows_by_table"], registry=parts["registry"])
    payload["source"] = "proposed"
    return payload
