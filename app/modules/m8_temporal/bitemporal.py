"""双时间纯函数：更正链、当前投影、维度版本、时点解析、规定时点账。

两条时间轴分开记，互不替代：
- 业务生效时间 effective：业务上「这条记录什么时候成立」——库存是快照日
  （日终盘点，按日末对齐），订单是下单时刻；
- 入库时间 ingest：系统「什么时候知道这条记录」——样本里的 updated_at。

版本身份 = 业务键 + 生效时间 + 入库时间 + 全部归一取值的摘要：同一更正重跑
必然算出同一个身份，不新增版本；取值或时间不同的更正必然是新版本。
"""
from __future__ import annotations

from decimal import Decimal

from ... import manifest


def correction_id(row: dict, columns: list) -> str:
    """更正身份摘要：全部归一列值参与，列序固定，与行序无关。"""
    parts = [f"{name}={row.get(name)}" for name in columns]
    return manifest.digest_parts(parts)[:16]


def build_chain(rows: list, *, key_columns: list, effective_column: str,
                ingest_column: str, measure_column: str, identity_columns: list,
                line_numbers: list | None = None) -> dict:
    """按业务键分组编版本：组内按 (入库时间, 行序) 排序，同身份去重。

    返回 versions（业务键 + 版本号 + 当前标记）、duplicates（同身份重复条数）、
    skipped（键或双时间缺失、无法编版的行数）。
    """
    seen: dict[str, dict] = {}
    duplicates = 0
    skipped = 0
    for index, row in enumerate(rows):
        if any(row.get(name) is None for name in key_columns) \
                or row.get(effective_column) is None \
                or row.get(ingest_column) is None:
            skipped += 1
            continue
        identity = correction_id(row, identity_columns)
        if identity in seen:
            duplicates += 1
            continue
        seen[identity] = {
            "version_id": identity,
            "key": {name: row.get(name) for name in key_columns},
            "effective": row.get(effective_column),
            "ingest": row.get(ingest_column),
            "measure": row.get(measure_column),
            "values": dict(row),
            "row_index": index,
            "line_no": (line_numbers[index]
                        if line_numbers and index < len(line_numbers) else None),
        }
    groups: dict[tuple, list] = {}
    for version in seen.values():
        key = tuple(str(version["key"].get(name)) for name in key_columns)
        groups.setdefault(key, []).append(version)
    versions = []
    for key in sorted(groups):
        members = sorted(groups[key],
                         key=lambda v: (str(v["ingest"]), v["row_index"]))
        current = members[-1]
        for position, version in enumerate(members, 1):
            version["version_no"] = position
            version["is_current"] = version is current
            version["key_text"] = "|".join(
                str(version["key"].get(name)) for name in key_columns)
            versions.append(version)
    versions.sort(key=lambda v: (v["key_text"], v["version_no"]))
    return {"versions": versions, "duplicates": duplicates, "skipped": skipped,
            "business_keys": len(groups)}


def grain_text_of(version: dict, grain_columns: list) -> str:
    return "|".join(str(version["values"].get(name)) for name in grain_columns)


def project_current(versions: list, *, grain_columns: list) -> list:
    """当前库存：每个粒度键恰好一行，取 (业务生效时间, 入库时间) 最大者。"""
    best: dict[tuple, tuple] = {}
    for version in versions:
        grain = tuple(str(version["values"].get(name)) for name in grain_columns)
        mark = (str(version["effective"]), str(version["ingest"]))
        if grain not in best or mark > best[grain][0]:
            best[grain] = (mark, version)
    rows = []
    for grain in sorted(best):
        version = best[grain][1]
        rows.append({
            "grain": {name: version["values"].get(name) for name in grain_columns},
            "grain_text": "|".join(grain),
            "effective": version["effective"],
            "ingest": version["ingest"],
            "measure": version["measure"],
            "version_id": version["version_id"],
            "version_no": version["version_no"],
            "key_text": version["key_text"],
        })
    return rows


def visible_at(versions: list, as_of: str) -> list:
    """时点 T 的可见集：入库时间 ≤ T 的版本（T 是入库轴上的点，不是业务轴）。"""
    return [version for version in versions if str(version["ingest"]) <= as_of]


def reconcile(versions: list, current_rows: list, *, as_of: str,
              grain_columns: list) -> dict:
    """规定时点 T 对账：明细可见集重投影 vs 当前事实表，逐粒度键对比。

    T 取最大入库时间（即「现在」）时必须逐键一致；T 早于现在时差异条目
    等于 T 之后到达的更正版本，差异不是错误，是双时间的正常表现。
    """
    expected_rows = project_current(visible_at(versions, as_of),
                                    grain_columns=grain_columns)
    expected = {row["grain_text"]: row for row in expected_rows}
    actual = {row["grain_text"]: row for row in current_rows}
    by_grain: dict[str, list] = {}
    for version in versions:
        by_grain.setdefault(grain_text_of(version, grain_columns), []).append(version)
    rows = []
    matched = 0
    for grain_text in sorted(set(expected) | set(actual)):
        left = expected.get(grain_text)
        right = actual.get(grain_text)
        after = len([1 for version in by_grain.get(grain_text, [])
                     if str(version["ingest"]) > as_of])
        if left is None or right is None:
            status = "only_expected" if right is None else "only_actual"
        elif str(left["measure"]) == str(right["measure"]):
            status = "matched"
        else:
            status = "diff"
        if status == "matched":
            matched += 1
        delta = None
        if left is not None and right is not None:
            delta = str(Decimal(str(right["measure"])) - Decimal(str(left["measure"])))
        rows.append({
            "grain_text": grain_text,
            "expected": None if left is None else left["measure"],
            "actual": None if right is None else right["measure"],
            "delta": delta,
            "status": status,
            "corrections_after": after,
        })
    diff = len(rows) - matched
    return {"as_of": as_of, "rows": rows, "matched": matched, "diff": diff,
            "balanced": diff == 0}


def dim_versions(rows: list, *, key_column: str, ingest_column: str) -> dict:
    """维度版本链：按键分组，入库时间排序，valid_to = 下一版 valid_from。"""
    groups: dict[str, list] = {}
    for index, row in enumerate(rows):
        key = row.get(key_column)
        if key is None or row.get(ingest_column) is None:
            continue
        groups.setdefault(str(key), []).append((index, row))
    versions = {}
    for key, members in groups.items():
        ordered = sorted(members,
                         key=lambda item: (str(item[1].get(ingest_column)), item[0]))
        chain = []
        for position, (index, row) in enumerate(ordered):
            valid_to = (ordered[position + 1][1].get(ingest_column)
                        if position + 1 < len(ordered) else None)
            chain.append({"key": key, "version_no": position + 1,
                          "valid_from": row.get(ingest_column),
                          "valid_to": valid_to, "values": dict(row)})
        versions[key] = chain
    return versions


def resolve_as_of(facts: list, chains: dict) -> dict:
    """逐事实行解析维度版本：区间内命中 / 早于首版按首版兜底 / 维度无此键。

    版本区间首尾相接（valid_to = 下一版 valid_from），所以只有「早于首版」
    一种兜底；每种解析结果都计数，规格单与页面用同一组数。
    """
    rows = []
    counts = {"matched": 0, "fallback": 0, "missing": 0}
    for item in facts:
        chain = chains.get(str(item["key"]))
        if not chain:
            counts["missing"] += 1
            rows.append({**item, "status": "missing", "version_no": None})
            continue
        moment = str(item["moment"])
        hit = None
        for version in chain:
            if str(version["valid_from"]) <= moment and (
                    version["valid_to"] is None
                    or moment < str(version["valid_to"])):
                hit = version
                break
        if hit is None:
            hit = chain[0]
            counts["fallback"] += 1
            rows.append({**item, "status": "fallback",
                         "version_no": hit["version_no"]})
        else:
            counts["matched"] += 1
            rows.append({**item, "status": "matched",
                         "version_no": hit["version_no"]})
    return {"rows": rows, "counts": counts}
