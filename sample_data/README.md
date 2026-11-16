# 样例数据说明

前三个文件是接入与剖析的输入，`downstream_map.csv` 是影响评估的输入，都是纯文本，
不需要外部服务。

| 文件 | 编码 | 内容 |
|---|---|---|
| `warehouse_ddl.sql` | UTF-8 | 8 张贴源表的 DDL：列、主键、外键、注释 |
| `sample_dump.csv` | UTF-8 带 BOM | 7 张表的样本行，`## table=表名` 分段 |
| `sample_dump_gb18030.csv` | GB18030 | 会员表样本，门店系统直接导出的编码 |
| `downstream_map.csv` | UTF-8 | 19 行下游对象登记：视图、调度作业、报表、指标 |

`sample_dump.csv` 是脏样例，故意留了这些坑：

1. 首行与 `odl_product` 段表头都带 BOM；
2. `odl_store` 段用旧列名 `store_id`，DDL 里叫 `store_code`；
3. `odl_store` 表里同时有 `store_code` 与 `Store_Code` 两列（两个团队各建一次）；
4. `odl_sale_order` 段中间重复写了一次表头；
5. 门店编码 `0012` 有前导零，其中一行被写成 `107`；
6. 金额有千分位 `"1,234.50"`，也有一行是全角 `１９９.９０`；
7. 折扣列混了 `-`、`N/A`、`未知` 与空串；
8. 支付流水号 19 位，超过 2^53；
9. 库存快照同一天同店同品出现两条不同在手数；
10. 最后一行被截断（列数少、文件没有结尾换行）。

`sample_dump_gb18030.csv` 声明与实际编码不符，用来验证编码回退链。

`downstream_map.csv` 也有三处真实麻烦：`DW-V-001` 重复导出一次；`RPT-017` 的表名是
大写 `ODL_SALE_ORDER_ITEM`；`RPT-016` 引用的是 `Store_Code` 而不是 `store_code`。
