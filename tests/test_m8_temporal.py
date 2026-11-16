"""M8 双时间建模：更正链、当前库存、关系版本、规定时点账、幂等重跑。

断言值全部来自样例数据可独立复算的行：
- 库存 5 行有效行，业务键 4 个，其中 2026-11-10|0012|00021 有两条更正
  （120 @11-10 23:00 → 118 @11-11 23:05）；
- 当前库存按 (业务生效, 入库) 最大者投影：0012|00021 = 96（11-11 快照），
  不是 118（11-10 的更正）——业务生效时间优先于入库时间；
- 门店维每店只有一行，版本链 valid_from = updated_at，订单时刻全部早于
  首版，只能按首版兜底；库存快照按日末对齐后全部命中版本区间。
"""
from __future__ import annotations

import pytest

from app.modules.m5_batches import service as batch_service
from app.modules.m8_temporal import bitemporal, service

ROWS = [
    {"snapshot_date": "2026-11-10", "store_code": "0012", "product_code": "00021",
     "on_hand_qty": "120", "updated_at": "2026-11-10 23:00:00", "batch_no": None},
    {"snapshot_date": "2026-11-10", "store_code": "0034", "product_code": "00021",
     "on_hand_qty": "80", "updated_at": "2026-11-10 23:00:00", "batch_no": None},
    {"snapshot_date": "2026-11-10", "store_code": "0012", "product_code": "00034",
     "on_hand_qty": "45", "updated_at": "2026-11-10 23:00:00", "batch_no": None},
    {"snapshot_date": "2026-11-11", "store_code": "0012", "product_code": "00021",
     "on_hand_qty": "96", "updated_at": "2026-11-11 23:00:00", "batch_no": None},
    {"snapshot_date": "2026-11-10", "store_code": "0012", "product_code": "00021",
     "on_hand_qty": "118", "updated_at": "2026-11-11 23:05:00", "batch_no": None},
]
CHAIN_ARGS = dict(key_columns=["snapshot_date", "store_code", "product_code"],
                  effective_column="snapshot_date", ingest_column="updated_at",
                  measure_column="on_hand_qty",
                  identity_columns=["snapshot_date", "store_code", "product_code",
                                    "on_hand_qty", "updated_at", "batch_no"])
NEW_CORRECTION = {"snapshot_date": "2026-11-12", "store_code": "0012",
                  "product_code": "00021", "on_hand_qty": "90",
                  "updated_at": "2026-11-12 08:00:00", "batch_no": None}


def _relation(payload: dict, child_table: str, child_column: str) -> dict:
    for row in payload["relationships"]:
        if row["child_table"] == child_table and row["child_column"] == child_column:
            return row
    raise AssertionError(f"关系不存在：{child_table}.{child_column}")


def test_chain_keeps_every_correction_version():
    """同一业务键的多条更正全部保留：5 行 → 5 版 4 键，各键 1 个当前版。"""
    chain = bitemporal.build_chain(ROWS, **CHAIN_ARGS)
    assert chain["business_keys"] == 4
    assert len(chain["versions"]) == 5
    assert chain["duplicates"] == 0
    key = "2026-11-10|0012|00021"
    members = [v for v in chain["versions"] if v["key_text"] == key]
    assert [(v["version_no"], v["measure"], v["is_current"]) for v in members] == [
        (1, "120", False), (2, "118", True)]
    assert members[0]["effective"] == members[1]["effective"] == "2026-11-10"
    assert members[0]["ingest"] < members[1]["ingest"]


def test_rerun_same_corrections_adds_no_version():
    """同一更正重跑：行集翻倍重算，版本身份相同，不新增版本。"""
    once = bitemporal.build_chain(ROWS, **CHAIN_ARGS)
    twice = bitemporal.build_chain(ROWS + ROWS, **CHAIN_ARGS)
    assert len(twice["versions"]) == len(once["versions"]) == 5
    assert twice["duplicates"] == 5
    assert [v["version_id"] for v in twice["versions"]] == \
           [v["version_id"] for v in once["versions"]]
    updated = bitemporal.build_chain(ROWS + [NEW_CORRECTION], **CHAIN_ARGS)
    assert len(updated["versions"]) == 6
    replayed = bitemporal.build_chain(ROWS + [NEW_CORRECTION, NEW_CORRECTION],
                                      **CHAIN_ARGS)
    assert len(replayed["versions"]) == 6
    assert replayed["duplicates"] == 1


def test_current_inventory_prefers_business_effective_time():
    """当前库存每店品一行：取 (业务生效, 入库) 最大者，不是入库最新者。"""
    chain = bitemporal.build_chain(ROWS, **CHAIN_ARGS)
    current = bitemporal.project_current(
        chain["versions"], grain_columns=["store_code", "product_code"])
    assert len(current) == 3
    by_grain = {row["grain_text"]: row for row in current}
    assert by_grain["0012|00021"]["measure"] == "96"
    assert by_grain["0012|00021"]["effective"] == "2026-11-11"
    assert by_grain["0012|00034"]["measure"] == "45"
    assert by_grain["0034|00021"]["measure"] == "80"


def test_upstream_update_recomputes_current():
    """上游记录更新并入后，当前库存从台账整体重算，旧版本仍在。"""
    chain = bitemporal.build_chain(ROWS + [NEW_CORRECTION], **CHAIN_ARGS)
    current = bitemporal.project_current(
        chain["versions"], grain_columns=["store_code", "product_code"])
    by_grain = {row["grain_text"]: row for row in current}
    assert by_grain["0012|00021"]["measure"] == "90"
    assert by_grain["0012|00021"]["effective"] == "2026-11-12"
    assert len(chain["versions"]) == 6


def test_reconcile_at_now_balances_and_past_shows_late_corrections():
    """规定时点对账：T=现在必须平衡；T=过去差异等于时点后到达的更正。"""
    chain = bitemporal.build_chain(ROWS, **CHAIN_ARGS)
    grain = ["store_code", "product_code"]
    current = bitemporal.project_current(chain["versions"], grain_columns=grain)
    now = bitemporal.reconcile(chain["versions"], current,
                               as_of="2026-11-11 23:05:00", grain_columns=grain)
    assert now["balanced"] is True
    assert now["matched"] == 3 and now["diff"] == 0
    past = bitemporal.reconcile(chain["versions"], current,
                                as_of="2026-11-11 12:00:00", grain_columns=grain)
    assert past["balanced"] is False
    assert past["matched"] == 2 and past["diff"] == 1
    row = next(item for item in past["rows"] if item["grain_text"] == "0012|00021")
    assert row["expected"] == "120" and row["actual"] == "96"
    assert row["delta"] == "-24"
    assert row["corrections_after"] == 2


def test_temporal_build_on_sample_batch(settings, store, batch_id):
    """批次上的总装：版本账、粒度账、时点账都自洽，当前库存恢复为事实表。"""
    payload = service.build(settings, store, batch_id)
    summary = payload["summary"]
    assert summary["versions"] == 5
    assert summary["business_keys"] == 4
    assert summary["superseded"] == 1
    assert summary["current_rows"] == 3
    assert summary["balanced"] is True
    assert payload["as_of"] == "2026-11-11 23:05:00"
    assert payload["as_of_now"] is True
    assert payload["temporal"]["restored"]["role"] == "fact"
    assert payload["temporal"]["restored"]["grain_unique"] is True
    assert all(row["balanced"] for row in payload["accounts"])
    version = next(v for v in payload["versions"]
                   if v["key_text"] == "2026-11-10|0012|00021"
                   and v["version_no"] == 2)
    assert version["measure"] == "118" and version["is_current"] is True
    assert version["line_no"] == 58


def test_temporal_build_with_as_of_parameter(settings, store, batch_id):
    """接口级时点参数：日期按日末对齐，时间戳原样，坏格式给 4xx。"""
    payload = service.build(settings, store, batch_id, as_of="2026-11-11 12:00:00")
    assert payload["reconcile"]["diff"] == 1
    assert payload["summary"]["balanced"] is False
    day = service.build(settings, store, batch_id, as_of="2026-11-11")
    assert day["as_of"] == "2026-11-11 23:59:59"
    assert day["summary"]["balanced"] is True
    with pytest.raises(Exception) as excinfo:
        service.build(settings, store, batch_id, as_of="昨天")
    assert getattr(excinfo.value, "code", "") == "T_BAD_AS_OF"


def test_relationship_version_policy_and_checks(settings, store, batch_id):
    """每条关系用了哪一版、为什么：时点版带核验计数，当前版带原因。"""
    payload = service.build(settings, store, batch_id)
    relations = payload["relationships"]
    assert len(relations) == 7

    order_store = _relation(payload, "odl_sale_order", "store_code")
    assert order_store["policy"] == "as_of"
    assert order_store["as_of_column"] == "order_time"
    assert order_store["reason_code"] == "T_REL_AS_OF"
    assert order_store["check"] == {"matched": 0, "fallback": 8, "missing": 0}
    assert "order_time" in order_store["why"]

    inv_store = _relation(payload, "odl_inventory_snapshot", "store_code")
    assert inv_store["policy"] == "as_of"
    assert inv_store["as_of_column"] == "snapshot_date"
    assert inv_store["check"] == {"matched": 5, "fallback": 0, "missing": 0}

    member = _relation(payload, "odl_sale_order", "member_id")
    assert member["policy"] == "current"
    assert member["reason_code"] == "T_DIM_NO_VERSION"
    assert member["check"] is None

    payment = _relation(payload, "odl_payment", "order_id")
    assert payment["policy"] == "current"
    assert payment["reason_code"] == "T_REL_CURRENT"

    for row in relations:
        assert row["why"]


def test_propose_recompute_and_idempotent_replay(settings, store, batch_id):
    """上游更新试算：新更正重算当前；同一更正再提交不新增版本。"""
    first = service.propose(settings, store, batch_id, [dict(NEW_CORRECTION)])
    assert first["added"] == 1
    assert first["duplicates"] == 0
    assert first["versions_total"] == 6
    assert first["current_changes"] == [
        {"grain_text": "0012|00021", "old": "96", "new": "90"}]

    replay = service.propose(settings, store, batch_id,
                             [dict(NEW_CORRECTION), dict(NEW_CORRECTION)])
    assert replay["added"] == 1
    assert replay["duplicates"] == 1
    assert replay["versions_total"] == 6

    existing = service.propose(settings, store, batch_id, [dict(ROWS[0])])
    assert existing["added"] == 0
    assert existing["duplicates"] == 1
    assert existing["current_changes"] == []


def test_propose_normalizes_like_pipeline(settings, store, batch_id):
    """提交行与批次行同一套归一：补零、千分位、日期写法都认。"""
    raw = {"snapshot_date": "2026/11/12", "store_code": "12",
           "product_code": "21", "on_hand_qty": "1,200",
           "updated_at": "2026-11-12 08:00:00"}
    result = service.propose(settings, store, batch_id, [raw])
    assert result["added"] == 1
    change = result["current_changes"][0]
    assert change["grain_text"] == "0012|00021"
    assert change["new"] == "1200"


def test_temporal_api_and_page(client_with_run):
    """接口与页面：总览、时点参数、试算、幂等，页面带批次号。"""
    payload = client_with_run.get("/api/temporal").json()
    assert payload["summary"]["versions"] == 5
    assert payload["summary"]["balanced"] is True

    past = client_with_run.get("/api/temporal?as_of=2026-11-11 12:00:00").json()
    assert past["reconcile"]["diff"] == 1

    bad = client_with_run.get("/api/temporal?as_of=not-a-time")
    assert bad.status_code == 400
    assert bad.json()["error"] == "T_BAD_AS_OF"

    proposed = client_with_run.post(
        "/api/temporal/propose", json={"corrections": [dict(NEW_CORRECTION)]})
    assert proposed.status_code == 200
    assert proposed.json()["added"] == 1

    empty = client_with_run.post("/api/temporal/propose", json={"corrections": []})
    assert empty.status_code == 400

    page = client_with_run.get("/temporal")
    assert page.status_code == 200
    batch_id = client_with_run.get("/api/batches").json()["rows"][0]["batch_id"]
    assert batch_id in page.text
    assert "更正台账" in page.text and "关系版本" in page.text


def test_temporal_does_not_touch_batch(client_with_run):
    """M8 与 M7 一样按请求现算：不写批次、不新增阶段行，重跑结果不变。"""
    before = client_with_run.get("/api/batches").json()["rows"]
    first = client_with_run.get("/api/temporal").json()
    client_with_run.post("/api/temporal/propose",
                         json={"corrections": [dict(NEW_CORRECTION)]})
    second = client_with_run.get("/api/temporal").json()
    after = client_with_run.get("/api/batches").json()["rows"]
    assert before == after
    assert len(after) == 1
    assert first["versions"] == second["versions"]
    detail = client_with_run.get(f"/api/batches/{after[0]['batch_id']}").json()
    assert len(detail["stages"]) == 5


def test_spec_lists_relationship_versions(client_with_run):
    """规格单写清每条关系用了哪一版、为什么。"""
    spec = client_with_run.get("/api/spec").json()
    relations = spec["relationship_versions"]
    assert len(relations) == 7
    as_of = [row for row in relations if row["policy"] == "as_of"]
    assert {row["child_table"] for row in as_of} == {
        "odl_sale_order", "odl_inventory_snapshot"}
    for row in relations:
        assert row["policy_label"] in ("时点版", "当前版")
        assert row["why"]
    page = client_with_run.get("/spec")
    assert "丁 关系版本" in page.text
