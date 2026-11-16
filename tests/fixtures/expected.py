"""样例数据算出的确定期望值；改样例必须同步改这里，测试会替你把关。

数量与定义全部来自 docs/DATA-DICTIONARY.md，不在这里另立一套。
"""

COLUMN_TOTAL = 47
ADOPTED = 41
REJECTED = 3
PENDING = 3

ROW_TOTAL = 50
ROW_VALID = 48
ROW_NULL = 1
ROW_QUARANTINED = 1

DECLARED_FK = 7
CONFIRMED_FK = 6
UNIDIRECTIONAL_FK = 1
INFERRED_FK = 1

OBJECT_TOTAL = 8
FACT_TABLES = 2
DIM_TABLES = 4
PENDING_TABLES = 2

# 度量合计（Decimal 字符串，两位小数）
ORDER_AMOUNT_SUM = "2173.90"
ITEM_QTY_SUM = "27.00"
ITEM_NET_SUM = "322.70"
ITEM_DISCOUNT_SUM = "3.50"
MEMBER_POINTS_SUM = "4640.50"
PRODUCT_LIST_PRICE_SUM = "58.20"
FACT_METRIC_TOTAL = "2500.10"

# 单列统计
STORE_CODE_DISTINCT = 6
SALE_STORE_CODE_DISTINCT = 4
SALE_STORE_CODE_UNIQUE = "0.5000"
DISCOUNT_NULL_TOTAL = 5
DISCOUNT_EMPTY = 1
DISCOUNT_SENTINEL = 4
DISCOUNT_UNIQUE = "0.6000"
CHANNEL_DISTINCT_CANONICAL = 3
CHANNEL_DISTINCT_RAW = 6

# 键判定
REGION_INCLUSION = "0.7500"
PAYMENT_INCLUSION = "1.0000"
PAYMENT_CARDINALITY = "0.7500"

# 星型
STORE_SCD = "SCD2"
REGION_SCD = "SCD1"
STORE_SCD_RATIO = "0.5714"
REGION_SCD_RATIO = "0.5000"

TABLE_ORDER = (
    "odl_region", "odl_store", "odl_product", "odl_member", "odl_sale_order",
    "odl_sale_order_item", "odl_payment", "odl_inventory_snapshot",
)

SECTION_ROWS = {
    "odl_region": 3,
    "odl_store": 6,
    "odl_product": 5,
    "odl_member": 5,
    "odl_sale_order": 8,
    "odl_sale_order_item": 10,
    "odl_payment": 6,
    "odl_inventory_snapshot": 6,
}

BIGINT_VALUE = "9007199254740993"
PADDED_PAIR = ("107", "0107")
