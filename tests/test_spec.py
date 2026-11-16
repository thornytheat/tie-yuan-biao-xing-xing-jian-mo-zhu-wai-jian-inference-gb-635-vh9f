"""M6 规格单导出：区块完整性、CSV 带 BOM、脚注编号、Decimal 与大整数序列化。"""
from __future__ import annotations

import json
from decimal import Decimal

from app.modules.m6_spec import csv_out, json_out, spec_out
from fixtures import expected as X


def test_trap09_bigint_survives_serialization(client_with_run, bundle):
    """TRAP-09：19 位流水号按字符串导出；转 double 会掉末位。"""
    assert int(float(X.BIGINT_VALUE)) == 9007199254740992
    assert json_out.safe(9007199254740993) == "9007199254740993"
    assert json_out.safe(12) == 12
    payload = json.loads(json_out.dumps({"id": 9007199254740993}))
    assert payload["id"] == X.BIGINT_VALUE

    column = next(row for row in bundle["columns"]
                  if row["table_name"] == "odl_payment" and row["column_name"] == "id")
    assert column["kind"] == "bigint"
    assert column["sample_value"] == X.BIGINT_VALUE
    assert "Q_BIGINT_TEXT" in column["reason_codes"]


def test_decimal_serialized_as_string():
    """Decimal 一律转字符串，不让 JSON 数值参与记账。"""
    assert json_out.safe(Decimal("2173.90")) == "2173.90"
    assert json_out.safe({"total": Decimal("2500.10"), "n": [1, Decimal("0.01")]}) == {
        "total": "2500.10", "n": [1, "0.01"]}


def test_spec_payload_has_every_section(client_with_run):
    """规格单五段齐全：列、键、对象、四账、脚注。"""
    payload = client_with_run.get("/api/spec").json()
    assert payload["title"] == "贴源层星型模型规格单"
    assert len(payload["columns"]) == X.ADOPTED
    assert len(payload["keys"]) == X.OBJECT_TOTAL + X.DECLARED_FK + X.INFERRED_FK
    assert len(payload["objects"]) == X.OBJECT_TOTAL
    assert len(payload["account_rows"]) == 4
    assert payload["total_rows"] == X.ADOPTED + len(payload["keys"]) + X.OBJECT_TOTAL + 4
    assert payload["accounts"]["metric_total"] == X.FACT_METRIC_TOTAL
    assert payload["batch_id"].startswith("SM")
    assert len(payload["input_digest"]) == 64


def test_spec_csv_has_bom_and_header(client_with_run):
    """列规格 CSV 带 BOM，表头字段固定，数值列为 Decimal 字符串。"""
    export = client_with_run.get("/api/spec/export.csv")
    assert export.status_code == 200
    assert export.content.startswith(b"\xef\xbb\xbf")
    text = export.content.decode("utf-8-sig")
    lines = text.splitlines()
    assert lines[0] == "表,列,声明类型,推断类型,可空,空值率,唯一率,去重数,样本行数,处置,角色,度量合计,原因码"
    assert len(lines) == X.ADOPTED + 1
    assert "odl_sale_order,order_amount" in text
    assert X.ORDER_AMOUNT_SUM in text


def test_evidence_csv_lists_quarantine_rows(client_with_run):
    """证据 CSV 带行号引用与原值，便于回源复核。"""
    export = client_with_run.get("/api/spec/evidence.csv")
    text = export.content.decode("utf-8-sig")
    assert text.splitlines()[0] == "表,列,行号引用,原值,原因码,结论"
    assert "107" in text
    assert "Q_CODE_PADDED" in text


def test_manifest_json_keeps_inputs_and_counters(client_with_run):
    """运行清单 JSON 可复算：run_id、输入摘要、参数、计数。"""
    export = client_with_run.get("/api/spec/manifest.json")
    payload = json.loads(export.content.decode("utf-8"))
    assert payload["run_id"].startswith("SM-")
    assert len(payload["inputs"]) == 3
    assert payload["counters"]["columns_total"] == str(X.COLUMN_TOTAL)
    assert payload["accounts"]["balanced"] is True


def test_footnotes_numbered_and_match_pages(client_with_run):
    """脚注编号与对象数、四账数一致，规格单与星型页共用一套注文。"""
    payload = client_with_run.get("/api/spec").json()
    notes = payload["footnotes"]
    assert len(notes) == len(payload["keys"]) + 3
    assert any("主键：" in note for note in notes)
    assert any("→" in note and "包含度" in note for note in notes)
    assert any("列账" in note for note in notes)
    assert any("对象账" in note for note in notes)


def test_spec_page_renders_footnotes_and_print_button(client_with_run):
    """规格单页可打印：有打印按钮、脚注编号与三个导出入口。"""
    page = client_with_run.get("/spec")
    assert page.status_code == 200
    body = page.text
    assert "data-action=\"print\"" in body
    assert "id=\"fn-1\"" in body
    assert "/api/spec/export.csv" in body
    assert "戊 注" in body


def test_empty_selection_raises(client):
    """没有批次时导出接口给 404 与 X_EMPTY_SELECTION。"""
    response = client.get("/api/spec/export.csv")
    assert response.status_code == 404
    assert response.json()["error"] == "X_EMPTY_SELECTION"


def test_account_rows_flat_and_ordered():
    """四账摊平顺序固定 A1..A4，页面与导出顺序一致。"""
    accounts = {
        "columns": {"total": 3, "adopted": 2, "rejected": 1, "pending": 0, "balanced": True},
        "rows": {"total": 5, "valid": 5, "null_row": 0, "quarantined": 0, "balanced": True},
        "references": {"declared": 1, "confirmed": 1, "unidirectional": 0, "inferred": 0,
                       "balanced": True},
        "objects": {"total": 2, "fact": 1, "dim": 1, "pending": 0, "balanced": True},
    }
    rows = spec_out.account_rows(accounts)
    assert [row["id"] for row in rows] == ["A1", "A2", "A3", "A4"]
    assert all(row["balanced"] for row in rows)
    assert csv_out.to_csv_text(("a",), [("1",)]).startswith("a\n")
