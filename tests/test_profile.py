"""M3 质量剖析：空值率定义、哨兵不计入合计、处置优先级、重复键冲突、四账。"""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from app.modules.m3_profile import profile, quarantine, rules
from fixtures import expected as X

LEDGER = Path(__file__).resolve().parent / "fixtures" / "expected_ledger.json"


def test_null_rate_counts_empty_and_sentinel_together(bundle):
    """空值率 = （空串 + 哨兵）÷ 样本行数；两者分开计，加起来才是分子。"""
    column = next(row for row in bundle["columns"]
                  if row["table_name"] == "odl_sale_order_item"
                  and row["column_name"] == "discount_amount")
    assert column["empty_count"] == X.DISCOUNT_EMPTY
    assert column["sentinel_count"] == X.DISCOUNT_SENTINEL
    assert column["null_total"] == X.DISCOUNT_NULL_TOTAL
    assert str(column["null_rate"]) == "0.5000"
    assert str(column["unique_rate"]) == X.DISCOUNT_UNIQUE
    assert column["distinct_count"] == 3


def test_trap07_sentinel_excluded_from_metric_total(bundle):
    """TRAP-07：哨兵不是 0，不进合计；积分合计只有 4 个可解析取值。"""
    column = next(row for row in bundle["columns"]
                  if row["table_name"] == "odl_member"
                  and row["column_name"] == "points")
    assert column["null_total"] == 1
    assert str(column["metric_total"]) == X.MEMBER_POINTS_SUM
    assert Decimal(X.MEMBER_POINTS_SUM) == Decimal("1280") + Decimal("920") \
        + Decimal("1460") + Decimal("980.5")
    assert str(column["metric_total"]) != Decimal("4640.50") + Decimal("0")


def test_disposition_priority_rejects_before_pending(bundle):
    """处置优先级：整列空判拒绝；键组合冲突的键列判待定。"""
    rejected = next(row for row in bundle["columns"]
                    if row["table_name"] == "odl_store"
                    and row["column_name"] == "Store_Code")
    assert rejected["disposition"] == "rejected"
    assert str(rejected["null_rate"]) == "1.0000"
    assert "Q_ALL_NULL" in rejected["reason_codes"]

    pending = next(row for row in bundle["columns"]
                   if row["table_name"] == "odl_inventory_snapshot"
                   and row["column_name"] == "snapshot_date")
    assert pending["disposition"] == "pending"
    assert "Q_KEY_COLUMN_PENDING" in pending["reason_codes"]


def test_trap14_duplicate_business_key_conflict(bundle, client_with_run):
    """TRAP-14：同一天同店同品出现两次不同在手数，冲突进冲突表并可导出。"""
    conflicts = bundle["conflicts"]
    assert len(conflicts) == 1
    conflict = conflicts[0]
    assert conflict["table_name"] == "odl_inventory_snapshot"
    assert conflict["key_columns"] == ["snapshot_date", "store_code", "product_code"]
    assert conflict["key_value"] == "2026-11-10|0012|00021"
    assert conflict["kept"]["on_hand_qty"] == "120"
    assert conflict["dropped"]["on_hand_qty"] == "118"
    assert conflict["note"] == "首见行 54，冲突行 58"

    export = client_with_run.get("/api/conflicts/export.csv")
    assert export.status_code == 200
    assert export.content.startswith(b"\xef\xbb\xbf")
    text = export.content.decode("utf-8-sig")
    assert "on_hand_qty=120" in text
    assert "on_hand_qty=118" in text


def test_row_ledger_matches_frozen_fixture(bundle):
    """行账三分类与冻结的 JSON 逐表一致：总行 = 有效 + 空值行 + 隔离。"""
    expected = json.loads(LEDGER.read_text(encoding="utf-8"))
    ledger = {row["table_name"]: row for row in bundle["row_ledger"]}
    for name, want in expected["per_table"].items():
        row = ledger[name]
        assert (row["total"], row["valid"], row["null_row"], row["quarantined"]) == (
            want["total"], want["valid"], want["null_row"], want["quarantined"])
    totals = rules.row_accounts(bundle["row_ledger"])
    assert totals["total"] == expected["row_total"]
    assert totals["valid"] == expected["valid"]
    assert totals["balanced"] is True


def test_four_accounts_balanced_and_metric_total(client_with_run):
    """四账自洽：列、行、引用、对象四本账各自闭合，金额合计 2500.10。"""
    payload = client_with_run.get("/api/reconcile").json()
    assert payload["columns"] == {"total": X.COLUMN_TOTAL, "adopted": X.ADOPTED,
                                  "rejected": X.REJECTED, "pending": X.PENDING,
                                  "balanced": True}
    assert payload["rows"]["total"] == X.ROW_TOTAL
    assert payload["references"]["declared"] == X.DECLARED_FK
    assert payload["objects"]["total"] == X.OBJECT_TOTAL
    assert payload["metric_total"] == X.FACT_METRIC_TOTAL
    assert payload["balanced"] is True
    assert len(payload["rows_page"]) == 4
    assert [row["id"] for row in payload["rows_page"]] == ["A1", "A2", "A3", "A4"]


def test_quarantine_csv_export_carries_reason_codes(client_with_run):
    """隔离区导出带原因码、行号引用与原值，可直接复核。"""
    export = client_with_run.get("/api/quarantine/export.csv")
    assert export.status_code == 200
    text = export.content.decode("utf-8-sig")
    header = text.splitlines()[0]
    assert header.startswith("阶段,级别,原因码,表,列,行号引用,原值,说明")
    assert "Q_ROW_TRUNCATED" in text
    assert "K_DUP_KEY_CONFLICT" in text
    rows = quarantine.to_csv_text([
        {"code": "Q_CODE_PADDED", "table_name": "t", "column_name": "c",
         "row_ref": "12", "raw_value": "107", "detail": "补零"}])
    assert "contract,提示,Q_CODE_PADDED" in rows


def test_unparsable_majority_rejects_column():
    """不可解析占比过半直接判拒绝，不给「部分可用」的幻觉。"""
    stats = {"sample_rows": 10, "null_total": 0, "null_rate": Decimal("0"),
             "unparsable_rate": Decimal("0.6"), "unique_rate": Decimal("0.9")}
    disposition, codes = rules.disposition(stats, key_conflict=False,
                                           is_key_column=False, column_name="amount")
    assert (disposition, codes) == ("rejected", ["Q_UNPARSABLE_MAJORITY"])

    short = dict(stats, sample_rows=2, unparsable_rate=Decimal("0"))
    assert rules.disposition(short, key_conflict=False, is_key_column=False,
                             column_name="amount") == ("pending", ["Q_SAMPLE_SHORT"])


def test_profile_api_filters_by_disposition(client_with_run):
    """按处置筛选：拒绝 3 列、待定 3 列、采纳 41 列。"""
    rejected = client_with_run.get("/api/profile?disposition=rejected").json()
    assert rejected["column_total"] == X.REJECTED
    pending = client_with_run.get("/api/profile?disposition=pending").json()
    assert pending["column_total"] == X.PENDING
    adopted = client_with_run.get("/api/profile?disposition=adopted").json()
    assert adopted["column_total"] == X.ADOPTED
    names = {row["column_name"] for row in rejected["rows"]}
    assert names == {"Store_Code", "coupon_code", "batch_no"}


def test_profile_summary_counts_rates_with_decimal():
    """比率用 Decimal 量化到 4 位，避免页面与导出两种写法。"""
    assert profile.rate(1, 3) == Decimal("0.3333")
    assert profile.rate(0, 0) == Decimal("0")
    assert profile.distinct_count(["a", "a", "b"]) == 2


def test_disposition_thresholds_use_half_open_comparisons():
    """边界：空值率等于 1.0 才拒；不可解析率 0.50 就拒；样本不足优先于空值率。

    反例：把阈值写成 > 1.0 或 > 0.5，会让「整列空」「半数不可解析」两列漏判。
    """
    def stats(**kwargs):
        base = {"sample_rows": 10, "null_rate": Decimal("0.0000"),
                "null_total": 0, "unparsable_rate": Decimal("0"),
                "unique_rate": Decimal("1.0000")}
        base.update(kwargs)
        return base

    def run(**kwargs):
        return rules.disposition(stats(**kwargs), key_conflict=False,
                                 is_key_column=False, column_name="brand")

    assert run(null_rate=Decimal("1"))[0] == "rejected"
    assert run(null_rate=Decimal("1"))[1] == ["Q_ALL_NULL"]
    assert run(null_rate=Decimal("0.9999"))[0] == "adopted"
    assert run(unparsable_rate=Decimal("0.5"))[0] == "rejected"
    assert run(unparsable_rate=Decimal("0.49"))[0] == "adopted"
    assert run(sample_rows=2, null_rate=Decimal("1")) == ("pending", ["Q_SAMPLE_SHORT"])
    assert rules.disposition(stats(unique_rate=Decimal("0.5")), key_conflict=True,
                             is_key_column=True, column_name="store_code") == (
        "pending", ["Q_KEY_COLUMN_PENDING"])
