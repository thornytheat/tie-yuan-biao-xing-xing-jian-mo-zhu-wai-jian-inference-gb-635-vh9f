"""M2 类型规则：千分位、全角、哨兵、编码保形、日期归一、类型放宽与列改名。"""
from __future__ import annotations

from decimal import Decimal

from app.modules.m2_contract import coerce, contract, evolve
from app.modules.m1_ingest import service as ingest_service
from fixtures import expected as X


def test_trap06_thousands_and_fullwidth_normalize():
    """TRAP-06：千分位与全角数字都要归一成同一个 Decimal。"""
    assert coerce.parse_decimal("1,234.50") == (Decimal("1234.50"), None)
    assert coerce.parse_decimal("１９９.９０") == (Decimal("199.90"), None)
    assert coerce.parse_decimal("１２３４.５") == (Decimal("1234.5"), None)
    assert coerce.parse_decimal("88.50") == (Decimal("88.50"), None)


def test_trap06_counterexample_naive_float_fails(settings, store, bundle):
    """反例：直接 float() 会抛错；用 float 累加也得不到 2173.90。"""
    try:
        float("1,234.50")
    except ValueError:
        naive_failed = True
    else:
        naive_failed = False
    assert naive_failed is True

    column = next(row for row in bundle["columns"]
                  if row["column_name"] == "order_amount")
    assert str(column["metric_total"]) == X.ORDER_AMOUNT_SUM

    naive = 0.0
    for value in ("1234.50", "235.00", "88.50", "42.00", "199.90", "56.00",
                  "318.00", "0.00"):
        naive += float(value)
    # float 累加得到的不是 2173.90，Decimal 才是
    assert Decimal(naive) != Decimal(X.ORDER_AMOUNT_SUM)
    assert Decimal(str(column["metric_total"])) == Decimal("1234.50") + Decimal("235.00") \
        + Decimal("88.50") + Decimal("42.00") + Decimal("199.90") + Decimal("56.00") \
        + Decimal("318.00") + Decimal("0.00")


def test_blank_and_sentinel_classified_separately():
    """空串与哨兵分开计：两者都不参与去重，但计数各走一列。"""
    assert coerce.classify_raw("") == "empty"
    assert coerce.classify_raw("   ") == "empty"
    assert coerce.classify_raw("N/A") == "sentinel"
    assert coerce.classify_raw("未知") == "sentinel"
    assert coerce.classify_raw("-") == "sentinel"
    assert coerce.classify_raw("0") == "value"
    assert coerce.is_sentinel("null") is True
    assert coerce.is_blank(None) is True


def test_trap17_renamed_column_mapped_to_ddl(settings, bundle):
    """TRAP-17：样本表头是旧列名 store_id，映射后该列不能是整列空值。"""
    data = ingest_service.intake(settings)
    table = data.ddl.table("odl_store")
    mapped = evolve.map_headers(table, data.sections["odl_store"].header)
    assert mapped.renames == [("store_id", "store_code")]
    assert mapped.index_of("store_code") == 0
    assert mapped.missing == ["Store_Code"]

    column = next(row for row in bundle["columns"]
                  if row["table_name"] == "odl_store"
                  and row["column_name"] == "store_code")
    assert column["sample_rows"] == 6
    assert str(column["null_rate"]) == "0.0000"
    assert column["distinct_count"] == X.STORE_CODE_DISTINCT


def test_trap17_widened_type_points(bundle):
    """TRAP-17：积分列声明 int，样本出现 980.5，类型放宽为数值并留痕。"""
    column = next(row for row in bundle["columns"]
                  if row["table_name"] == "odl_member"
                  and row["column_name"] == "points")
    assert column["kind"] == "decimal"
    assert column["ddl_type"] == "int"
    assert "E_TYPE_WIDENED" in column["reason_codes"]
    assert str(column["metric_total"]) == X.MEMBER_POINTS_SUM
    assert column["null_total"] == 1


def test_trap17_same_column_converges_to_widest(settings):
    """同名同义列跨表宽度不同时取最宽：order_id 16 → 20。"""
    data = ingest_service.intake(settings)
    findings = evolve.converge_types(data.ddl)
    assert "order_id" in findings
    assert findings["order_id"]["widest"] == 20
    assert "odl_sale_order" in findings["order_id"]["narrow_tables"]
    assert findings["order_id"]["code"] == "E_TYPE_WIDENED"


def test_trap08_code_column_never_integerized():
    """TRAP-08：编码列判为编码，整数化会丢前导零。"""
    kind, codes = coerce.infer_kind(["0012", "0034", "0107"], "varchar")
    assert kind == "code"
    assert "Q_CODE_PRESERVED" in codes
    assert str(int("0012")) == "12"
    assert coerce.normalize_code("0012", 4) == ("0012", "Q_CODE_PRESERVED")
    raw, code = coerce.normalize_code("107", 4)
    assert (raw, code) == ("0107", "Q_CODE_PADDED")
    assert X.PADDED_PAIR == ("107", "0107")


def test_date_formats_normalize_to_one_value():
    """三种日期写法归一成同一个日期；非法日期给原因码。"""
    assert coerce.parse_date("2023/05/06") == (coerce.dt.date(2023, 5, 6), None)
    assert coerce.parse_date("2024-01-18") == (coerce.dt.date(2024, 1, 18), None)
    assert coerce.parse_date("20230612") == (coerce.dt.date(2023, 6, 12), None)
    assert coerce.parse_date("2023-13-01")[1] == "Q_BAD_DATE"
    assert coerce.parse_timestamp("2026-11-10 23:00:00")[0].minute == 0


def test_contract_document_exposes_thresholds():
    """契约文档给出类型清单、阈值与处置规则，页面与规格单都引这里。"""
    document = contract.document()
    assert document["contract_version"] == "spec/1.0"
    kinds = [item["kind"] for item in document["kinds"]]
    assert kinds == ["code", "int", "bigint", "decimal", "date", "timestamp", "text"]
    thresholds = {item["key"]: item["value"] for item in document["thresholds"]}
    assert thresholds["外键包含度下限"] == "0.95"
    assert thresholds["事实表外键下限"] == "2"
    assert len(document["sentinels"]) == 14
    assert len(evolve.rule_rows()) == 3
    assert document["disposition_rules"][0]["level"] == "拒绝"


def test_probe_endpoint_returns_4xx_for_empty_values(client):
    """坏输入走 4xx：试跑接口不吃空数组。"""
    response = client.post("/api/contract/probe", json={"values": []})
    assert response.status_code == 400
    response = client.post("/api/contract/probe",
                           json={"values": ["1"], "kind": "money"})
    assert response.status_code == 400
    ok = client.post("/api/contract/probe", json={"values": ["1,234.50"], "kind": "decimal"})
    assert ok.status_code == 200
    assert ok.json()["normalized"] == ["1234.50"]


def test_code_padding_boundary_and_empty_states():
    """边界：恰好等于列内宽度不补零，超宽不截断，空值不补零。

    朴素实现常见两种错法：一律 zfill 会把 0012 之外的 107 补成 0107 也对，
    但对 5 位取值 zfill 反而截断；把空串也补零会凭空造出一个编码。
    """
    assert coerce.normalize_code("107", 4) == ("0107", "Q_CODE_PADDED")
    assert coerce.normalize_code("0012", 4) == ("0012", "Q_CODE_PRESERVED")
    assert coerce.normalize_code("00123", 4) == ("00123", "Q_CODE_PRESERVED")
    assert coerce.normalize_code("", 4) == ("", "Q_CODE_PRESERVED")
    assert coerce.normalize_code(" １０７ ", 4) == ("0107", "Q_CODE_PADDED")

    assert coerce.parse_decimal("") == (None, None)
    assert coerce.parse_decimal("-") == (None, "Q_BAD_NUMBER")
    assert coerce.parse_decimal("12.50") == (Decimal("12.50"), None)
    assert coerce.pad_width(["107", "0012", "", "88"]) == 4
    assert coerce.pad_width(["", "-", "未知"]) == 0
