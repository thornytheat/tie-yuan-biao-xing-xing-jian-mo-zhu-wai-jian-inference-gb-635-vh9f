"""M5 版本与重跑：批次键、幂等重跑、阶段水位、中断留痕、运行清单。"""
from __future__ import annotations

import pytest

from app import manifest
from app.modules.m5_batches import service as batch_service, state
from app.modules.m7_impact import service as impact_service
from fixtures import expected as X


def test_invariant_rerun_same_digest_keeps_counts(settings, store, run):
    """不变量：同一输入再跑一次复用批次，行数与合计一个都不变。"""
    first = run
    before = store.count_rows(first["batch_id"])
    totals_before = store.load_totals(first["batch_id"])
    second = batch_service.run(settings=settings, store=store, source="sample")
    assert second["replayed"] is True
    assert second["code"] == "B_IDEMPOTENT_REPLAY"
    assert second["batch_id"] == first["batch_id"]
    assert store.count_rows(second["batch_id"]) == before
    totals_after = store.load_totals(second["batch_id"])
    assert totals_after["metric_total"] == totals_before["metric_total"]
    assert totals_after["columns_total"] == totals_before["columns_total"]
    assert len(store.list_batches()) == 1


def test_batch_key_depends_only_on_inputs_and_rule_version():
    """批次键只由输入摘要 + 规则版本 + 参数决定，与调用次数无关。"""
    inputs = [{"name": "a.sql", "sha256": "1" * 64},
              {"name": "b.csv", "sha256": "2" * 64}]
    params = {"sample_cap": 500}
    key = manifest.batch_key(rule_version="starmodel/1.0.0", inputs=inputs, params=params)
    again = manifest.batch_key(rule_version="starmodel/1.0.0",
                               inputs=list(reversed(inputs)), params=params)
    assert key == again
    changed = manifest.batch_key(rule_version="starmodel/1.0.0", inputs=inputs,
                                 params={"sample_cap": 50})
    assert changed != key


def test_stage_watermark_reaches_spec(run):
    """阶段按固定顺序跑完，水位落在最后一段规格单。"""
    stages = run["stages"]
    assert [row["stage"] for row in stages] == ["import", "contract", "profile",
                                               "keys", "spec"]
    for row in stages:
        assert row["status"] == "done"
        assert row["elapsed_ms"] >= 0
    mark = state.watermark(stages)
    assert mark["stage"] == "spec"
    assert mark["index"] == 4


def test_manifest_records_inputs_and_digest(run, store):
    """运行清单带 run_id、3 个输入文件的 sha256 与输入摘要。"""
    doc = store.load_manifest(run["batch_id"])
    assert doc["run_id"].startswith("SM-")
    assert len(doc["inputs"]) == 3
    for item in doc["inputs"]:
        assert len(item["sha256"]) == 64
    assert len(doc["input_digest"]) == 64
    assert doc["rule_version"] == "starmodel/1.0.0"
    assert doc["params"]["sample_cap"] == 500
    assert doc["replayable"].startswith("同 batch_key")


def test_resume_rejected_for_finished_batch(client_with_run):
    """已完成批次不能续跑，返回 409 与稳定原因码。"""
    listed = client_with_run.get("/api/batches").json()["rows"]
    batch_id = listed[0]["batch_id"]
    response = client_with_run.post(f"/api/batches/{batch_id}/resume")
    assert response.status_code == 409
    assert response.json()["error"] == "B_IDEMPOTENT_REPLAY"


def test_aborted_batch_is_recorded(settings, store, monkeypatch):
    """流水线异常时批次留 aborted 记录，不留半批结果。"""
    def boom(*args, **kwargs):
        raise RuntimeError("模拟类型阶段中断")

    monkeypatch.setattr(batch_service.m2_service, "build", boom)
    with pytest.raises(RuntimeError):
        batch_service.run(settings=settings, store=store, source="sample")
    batches = store.list_batches()
    assert len(batches) == 1
    assert batches[0]["status"] == "aborted"
    assert "模拟类型阶段中断" in batches[0]["note"]


def test_batch_detail_exposes_accounts_and_totals(client_with_run):
    """批次详情一次给出阶段、四账、水位与合计。"""
    listed = client_with_run.get("/api/batches").json()["rows"]
    batch_id = listed[0]["batch_id"]
    payload = client_with_run.get(f"/api/batches/{batch_id}").json()
    assert payload["batch"]["status"] == "done"
    assert payload["watermark"]["stage"] == "spec"
    assert payload["accounts"]["balanced"] is True
    assert payload["totals"]["rows_total"] == "50"
    assert len(payload["stages"]) == 5


def test_batches_api_rejects_bad_limit(client_with_run):
    """分页参数越界直接 4xx，不做静默截断。"""
    assert client_with_run.get("/api/batches?limit=0").status_code == 400
    assert client_with_run.get("/api/batches?limit=500").status_code == 400
    assert client_with_run.get("/api/batches?limit=5").status_code == 200


def test_empty_state_returns_404_before_first_run(client):
    """还没有批次时接口给 404 与 B_EMPTY，页面给空态。"""
    assert client.get("/api/profile").json()["error"] == "B_EMPTY"
    assert client.get("/api/model").status_code == 404
    assert client.get("/api/spec").status_code == 404
    page = client.get("/profile")
    assert page.status_code == 200
    assert "还没有列剖析结果。" in page.text


def test_invariant_impact_evaluation_is_repeatable(settings, store, run):
    """不变量：同一批次算两次影响评估完全一致，且评估不回写批次行。"""
    before = store.count_rows(run["batch_id"])
    first = impact_service.build(settings, store, run["batch_id"])
    second = impact_service.build(settings, store, run["batch_id"])
    assert [row["change_id"] for row in second["order"]] \
        == [row["change_id"] for row in first["order"]]
    assert second["summary"] == first["summary"]
    assert second["registry"]["digest"] == first["registry"]["digest"]
    assert second["notes"] == first["notes"]
    assert first["summary"]["changes"] == (first["summary"]["executable"]
                                          + first["summary"]["blocked"])
    assert store.count_rows(run["batch_id"]) == before
    counts = store.count_rows(run["batch_id"])
    assert counts == run["counts"]
    assert counts["tables_meta"] == len(X.TABLE_ORDER)
    assert counts["star_objects"] == X.OBJECT_TOTAL
    assert counts["conflicts"] == 1
