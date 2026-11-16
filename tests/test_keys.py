"""M4 键推断与星型建议：主键唯一性、外键包含度、单向引用、别名归并、SCD。"""
from __future__ import annotations

from decimal import Decimal

from app import config
from app.modules.m2_contract import coerce
from app.modules.m4_keys import keys, star
from fixtures import expected as X

SALE_STORE_RAW = ["0012", "0012", "0034", "0107", "107", "0034", "0115", "0012"]
STORE_CODES = ["0012", "0034", "0107", "0115", "0203", "0241"]


def test_trap08_leading_zero_padding_changes_inclusion():
    """TRAP-08：不补零时包含度 0.80 低于阈值，补零后 1.00 才判成立外键。"""
    padded = [coerce.normalize_code(value, 4)[0] for value in SALE_STORE_RAW]
    assert padded.count("0107") == 2
    with_padding = keys.inclusion(padded, STORE_CODES)
    without = keys.inclusion(SALE_STORE_RAW, STORE_CODES)
    assert with_padding["inclusion"] == Decimal("1")
    assert with_padding["child_distinct"] == 4
    assert without["inclusion"] == Decimal("0.8000")
    assert without["child_distinct"] == 5
    assert without["inclusion"] < config.FK_INCLUSION_MIN
    assert with_padding["inclusion"] >= config.FK_INCLUSION_MIN


def test_primary_keys_confirmed_for_declared_tables(bundle):
    """8 张表都拿到主键结论：6 单列 + 1 组合键一致，库存组合键冲突。"""
    pks = {row["child_table"]: row for row in bundle["keys"] if row["kind"] == "pk"}
    assert len(pks) == len(X.TABLE_ORDER)
    assert pks["odl_store"]["child_columns"] == ["store_code"]
    assert pks["odl_store"]["status"] == "confirmed"
    assert pks["odl_payment"]["child_columns"] == ["id"]
    assert pks["odl_payment"]["status"] == "confirmed"
    assert pks["odl_sale_order_item"]["child_columns"] == ["order_id", "line_no"]
    assert pks["odl_sale_order_item"]["status"] == "confirmed"
    assert pks["odl_inventory_snapshot"]["status"] == "conflict"
    assert pks["odl_inventory_snapshot"]["reason_code"] == "K_DUP_KEY_CONFLICT"


def test_trap21_unidirectional_reference_detected(bundle):
    """TRAP-21：DDL 声明了 store→region，样本里有区域 09 不存在，判单向。"""
    fks = {(row["child_table"], row["child_columns"][0]): row
           for row in bundle["keys"] if row["kind"] == "fk"}
    region = fks[("odl_store", "region_code")]
    assert region["declared"] is True
    assert region["status"] == "unidirectional"
    assert str(region["inclusion"]) == X.REGION_INCLUSION
    assert region["reason_code"] == "K_FK_UNIDIRECTIONAL"

    inferred = [row for row in bundle["keys"]
                if row["kind"] == "fk" and row["status"] == "inferred"]
    assert len(inferred) == X.INFERRED_FK
    assert inferred[0]["child_table"] == "odl_payment"
    assert inferred[0]["parent_table"] == "odl_sale_order"
    assert inferred[0]["declared"] is False


def test_declared_reference_accounts_balance(bundle):
    """引用账：声明 7 = 一致 6 + 单向 1，另推断出 1 条。"""
    accounts = bundle["accounts"]["references"]
    assert accounts["declared"] == X.DECLARED_FK
    assert accounts["confirmed"] == X.CONFIRMED_FK
    assert accounts["unidirectional"] == X.UNIDIRECTIONAL_FK
    assert accounts["inferred"] == X.INFERRED_FK
    assert accounts["balanced"] is True


def test_trap22_alias_merge_is_order_independent(bundle):
    """TRAP-22：渠道别名归并后去重 3 个，打乱输入顺序结果不变。"""
    samples = ["APP", "app", "小程序", "微信小程序", "门店", "App", "门店", "APP"]
    canonical = [coerce.alias_of(value, "channel")[0] for value in samples]
    assert len(set(canonical)) == X.CHANNEL_DISTINCT_CANONICAL
    assert len(set(samples)) == X.CHANNEL_DISTINCT_RAW

    reversed_order = [coerce.alias_of(value, "channel")[0] for value in reversed(samples)]
    assert sorted(set(reversed_order)) == sorted(set(canonical))
    assert coerce.alias_of("APP端", "channel") == ("APP", True)
    assert coerce.alias_of("APP", "channel") == ("APP", False)
    assert coerce.alias_of("app", "pay_method") == ("app", False)
    assert coerce.alias_of("App", "tier") == ("App", False)

    column = next(row for row in bundle["columns"]
                  if row["table_name"] == "odl_sale_order"
                  and row["column_name"] == "channel")
    assert column["distinct_count"] == X.CHANNEL_DISTINCT_CANONICAL
    assert "K_ALIAS_MERGED" in column["reason_codes"]


def test_declared_foreign_key_judged_by_inclusion_only(bundle):
    """声明外键只看包含度：子表取满父表全部取值（基数比 1.0）仍判成立。"""
    item = next(row for row in bundle["keys"]
                if row["kind"] == "fk" and row["child_table"] == "odl_sale_order_item"
                and row["child_columns"] == ["order_id"])
    assert item["status"] == "confirmed"
    assert str(item["inclusion"]) == "1.0000"
    assert str(item["cardinality_ratio"]) == "1.0000"

    payment = next(row for row in bundle["keys"]
                   if row["kind"] == "fk" and row["child_table"] == "odl_payment")
    assert str(payment["inclusion"]) == X.PAYMENT_INCLUSION
    assert str(payment["cardinality_ratio"]) == X.PAYMENT_CARDINALITY


def test_star_roles_and_scd_types(bundle):
    """事实 2 / 维 4 / 待定 2；门店 SCD2、区域 SCD1、商品无更新列 SCD0。"""
    objects = {row["table_name"]: row for row in bundle["star"]}
    assert len(objects) == X.OBJECT_TOTAL
    roles = [row["role"] for row in objects.values()]
    assert roles.count("fact") == X.FACT_TABLES
    assert roles.count("dim") == X.DIM_TABLES
    assert roles.count("pending") == X.PENDING_TABLES

    assert objects["odl_sale_order"]["role"] == "fact"
    assert objects["odl_sale_order_item"]["role"] == "fact"
    assert objects["odl_store"]["scd"] == X.STORE_SCD
    assert objects["odl_store"]["evidence"]["scd"]["ratio"] == X.STORE_SCD_RATIO
    assert objects["odl_region"]["scd"] == X.REGION_SCD
    assert objects["odl_region"]["evidence"]["scd"]["ratio"] == X.REGION_SCD_RATIO
    assert objects["odl_product"]["scd"] == "SCD0"
    assert objects["odl_payment"]["role"] == "pending"
    assert objects["odl_payment"]["reason_code"] == "K_FACT_FK_SHORT"


def test_degenerate_dimension_inside_fact(bundle):
    """事实表主键是文本编码时输出退化维度，不单独建维。"""
    fact = next(row for row in bundle["star"]
                if row["table_name"] == "odl_sale_order")
    assert [item["column"] for item in fact["degenerate"]] == ["order_id"]
    assert fact["degenerate"][0]["code"] == "K_DEGENERATE"
    item_fact = next(row for row in bundle["star"]
                     if row["table_name"] == "odl_sale_order_item")
    assert item_fact["degenerate"] == []


def test_model_api_exposes_keys_and_objects(client_with_run):
    """模型接口同时给键结论与星型对象，便于前端一次取完。"""
    payload = client_with_run.get("/api/model").json()
    assert len(payload["keys"]) == len(X.TABLE_ORDER) + X.DECLARED_FK + X.INFERRED_FK
    assert len(payload["objects"]) == X.OBJECT_TOTAL
    assert payload["role_labels"]["fact"] == "事实表"
    fact = next(row for row in payload["objects"] if row["table_name"] == "odl_sale_order")
    assert fact["fk_count"] == 2
    assert fact["metric_columns"] == ["order_amount"]


def test_scd_ratio_rule_boundary():
    """SCD 阈值是 > 0.5 判 SCD2；等于 0.5 落回 SCD1。"""
    assert config.SCD2_CHANGE_RATIO == Decimal("0.50")
    assert not Decimal("0.5000") > config.SCD2_CHANGE_RATIO
    assert not Decimal(X.REGION_SCD_RATIO) > config.SCD2_CHANGE_RATIO
    assert Decimal(X.STORE_SCD_RATIO) > config.SCD2_CHANGE_RATIO
    assert callable(star.scd_of)


def test_fk_inclusion_threshold_boundary():
    """边界：包含度 19/20 恰好 0.95 成立，18/20 就不成立；端点值算法不同结论相反。"""
    parent = [f"P{index:02d}" for index in range(20)]
    passed = keys.inclusion(parent[:19] + ["X1"], parent)
    failed = keys.inclusion(parent[:18] + ["X1", "X2"], parent)
    assert passed["inclusion"] == config.FK_INCLUSION_MIN == Decimal("0.95")
    assert failed["inclusion"] == Decimal("0.9000")
    assert keys._judge(passed, True, declared=True) == ("confirmed", "K_FK_CONFIRMED")
    assert keys._judge(failed, True, declared=True) == ("unidirectional",
                                                        "K_FK_UNIDIRECTIONAL")

    low = {"inclusion": Decimal("1"), "cardinality": config.FK_CARDINALITY_MIN}
    high = {"inclusion": Decimal("1"), "cardinality": config.FK_CARDINALITY_MAX}
    under = {"inclusion": Decimal("1"), "cardinality": Decimal("0.0100")}
    assert keys._judge(low, True, declared=False)[0] == "inferred"
    assert keys._judge(high, True, declared=False)[0] == "inferred"
    assert keys._judge(under, True, declared=False) == ("rejected", "K_FK_CARD_OUT")
    # 声明外键不设基数比区间：基数比 1.0 也判成立
    assert keys._judge(high, True, declared=True) == ("confirmed", "K_FK_CONFIRMED")
