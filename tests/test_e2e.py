"""端到端：脏样例 → 接入 → 剖析 → 键 → 规格单 → 导出，数值可独立复算。"""
from __future__ import annotations

import csv
import io
from decimal import Decimal

from fixtures import expected as X

PAGES = ("/", "/profile", "/keys", "/star", "/impact", "/batches", "/spec")


def _sum_order_amount(sample_dir) -> Decimal:
    """独立复算：自己读一遍样例，不信流水线的中间值。"""
    text = (sample_dir / "sample_dump.csv").read_text(encoding="utf-8-sig")
    total = Decimal("0")
    in_order = False
    for line in text.splitlines():
        if line.strip().startswith("## table="):
            in_order = line.strip() == "## table=odl_sale_order"
            continue
        if not in_order or not line.strip():
            continue
        cells = next(csv.reader([line]))
        if cells[0] == "order_id":
            continue
        if len(cells) < 6:
            continue
        total += Decimal(cells[5].replace(",", "").replace("，", ""))
    return total


def test_dirty_sample_end_to_end(client, sample_dir):
    """脏样例一路走到规格单：四账闭合，金额合计与独立复算一致。"""
    imported = client.post("/api/import", json={"source": "sample"})
    assert imported.status_code == 200
    body = imported.json()
    assert body["status"] == "done"
    assert body["accounts"]["balanced"] is True
    assert body["accounts"]["metric_total"] == X.FACT_METRIC_TOTAL

    spec = client.get("/api/spec").json()
    assert spec["accounts"]["balanced"] is True
    assert spec["accounts"]["metric_total"] == X.FACT_METRIC_TOTAL
    assert Decimal(spec["accounts"]["metric_total"]) == (
        _sum_order_amount(sample_dir) + Decimal(X.ITEM_NET_SUM)
        + Decimal(X.ITEM_DISCOUNT_SUM))
    assert len(spec["columns"]) == X.ADOPTED


def test_seven_pages_render_with_batch_context(client_with_run):
    """七个页面都渲染成功，且每页都带当前批次号。"""
    listed = client_with_run.get("/api/batches").json()["rows"]
    batch_id = listed[0]["batch_id"]
    for path in PAGES:
        page = client_with_run.get(path)
        assert page.status_code == 200, path
        assert batch_id in page.text, path


def test_every_trap_evidence_reachable_from_api(client_with_run):
    """十条 TRAP 的证据都能从接口取到，页面只呈现这里的结果。"""
    codes = set()
    for path in ("/api/quarantine", "/api/spec/evidence.csv"):
        response = client_with_run.get(path)
        assert response.status_code == 200
    quarantine = client_with_run.get("/api/quarantine").json()
    codes.update(row["code"] for row in quarantine["rows"])
    for code in ("E_BOM_STRIPPED", "E_CHARSET_FALLBACK", "Q_DUP_HEADER",
                 "Q_ROW_TRUNCATED", "Q_CODE_PADDED", "Q_BIGINT_TEXT",
                 "Q_COLUMN_CASE_CONFLICT", "E_COLUMN_RENAMED", "E_TYPE_WIDENED",
                 "K_DUP_KEY_CONFLICT", "K_FK_UNIDIRECTIONAL", "K_FK_INFERRED"):
        assert code in codes, code


def test_spec_tables_match_import_counts(client_with_run):
    """规格单的行数与接入返回的计数一致，导出不许多也不许少。"""
    imported = client_with_run.post("/api/import", json={"source": "sample"}).json()
    batch_id = imported["batch_id"]
    spec = client_with_run.get(f"/api/spec?batch_id={batch_id}").json()
    assert spec["total_rows"] == X.ADOPTED + len(spec["keys"]) + X.OBJECT_TOTAL + 4
    csv_text = client_with_run.get(f"/api/spec/export.csv?batch_id={batch_id}").content
    rows = csv_text.decode("utf-8-sig").splitlines()
    assert len(rows) - 1 == X.ADOPTED
    ledger = client_with_run.get(f"/api/batches/{batch_id}").json()
    assert ledger["accounts"]["rows"]["total"] == X.ROW_TOTAL


def test_failed_input_returns_4xx_with_reason(client):
    """失败输入给 4xx：来源不合法、样本上限越界、试跑参数为空。"""
    bad_source = client.post("/api/import", json={"source": "http://evil.example"})
    assert bad_source.status_code == 400
    assert bad_source.json()["error"] == "E_BAD_REQUEST"

    bad_cap = client.post("/api/import", json={"source": "sample", "sample_cap": 0})
    assert bad_cap.status_code == 400
    assert "sample_cap" in bad_cap.json()["message"]

    bad_probe = client.post("/api/contract/probe", json={"values": []})
    assert bad_probe.status_code == 400

    no_batch = client.get("/api/batches")
    assert no_batch.status_code == 200
    assert no_batch.json()["rows"] == []


def test_repeated_import_is_idempotent_end_to_end(client_with_run):
    """连点两次接入：第二次复用批次，导出内容逐字相同。"""
    first = client_with_run.get("/api/spec/export.csv").content
    second_import = client_with_run.post("/api/import", json={"source": "sample"}).json()
    assert second_import["replayed"] is True
    second = client_with_run.get("/api/spec/export.csv").content
    assert second == first
    listed = client_with_run.get("/api/batches").json()["rows"]
    assert len(listed) == 1
