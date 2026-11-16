# 数据字典 starmodel

本文件是列剖析与键判定的字段定义唯一来源；页面、接口、导出都引用这里，
不在模板或 handler 里另写一套说明。

## 1 取值与类型

| 字段 | 类型 | 单位 | 精度 | 可空 | 含义 |
|---|---|---|---|---|---|
| `table_name` | TEXT | — | — | 否 | 贴源表名，取自 DDL 的 `CREATE TABLE` |
| `column_name` | TEXT | — | — | 否 | 列名，大小写按 DDL 原样保留 |
| `ordinal` | INT | — | — | 否 | 列在 DDL 里的顺序，从 1 开始 |
| `ddl_type` | TEXT | — | — | 否 | DDL 声明类型原文，如 `varchar(4)`、`numeric(12,2)` |
| `kind` | TEXT | — | — | 否 | 推断类型：`code` / `int` / `bigint` / `decimal` / `date` / `timestamp` / `text` |
| `width` | INT | 字符 | — | 是 | 声明宽度，仅 `varchar`/`char` 有值 |
| `nullable` | BOOL | — | — | 否 | DDL 是否允许 `NULL` |
| `sample_rows` | INT | 行 | — | 否 | 该表有效行数（不含空值行与隔离行） |
| `null_total` | INT | 行 | — | 否 | 空串数 + 哨兵数 |
| `empty_count` | INT | 行 | — | 否 | 真空值（去掉空白后为空串） |
| `sentinel_count` | INT | 行 | — | 否 | 命中哨兵清单的取值个数 |
| `distinct_count` | INT | 个 | — | 否 | 归一后的去重取值个数，空值与哨兵不计入 |
| `unparsable` | INT | 行 | — | 否 | 该列无法按 `kind` 归一的取值个数 |
| `null_rate` | DECIMAL | — | 4 位 | 否 | `null_total ÷ sample_rows` |
| `unique_rate` | DECIMAL | — | 4 位 | 否 | `distinct_count ÷ (sample_rows − null_total)`，分母为 0 时取 0 |
| `metric_total` | DECIMAL | 元 / 件 | 2 位 | 是 | 该列可解析取值之和，仅数值型列有值 |
| `disposition` | TEXT | — | — | 否 | `adopted` 采纳 / `rejected` 拒绝 / `pending` 待定 |
| `reason_codes` | TEXT[] | — | — | 否 | 该列命中的原因码，来自 `app/reasons.py` |

## 2 比率与合计

- **空值率**：分子是空串与哨兵之和，分母是该表有效行数；空值行（整行都空）不进分母。
- **唯一率**：分母扣掉空值与哨兵，所以「一列有 2 个哨兵、4 个不同取值、共 6 行」时唯一率是 1.0000 而不是 0.6667。
- **比率**一律 Decimal 计算并量化到 4 位小数，页面显示成百分比时再乘 100。
- **金额与用量**一律 Decimal，最小刻度 0.01；对外的 JSON 与 CSV 用字符串，不用浮点。
- **大整数**：绝对值超过 2^53 = 9007199254740992 的整数按字符串保存与导出。

## 3 空值三态

| 状态 | 判定 | 是否进合计 | 是否进去重 |
|---|---|---|---|
| 真空值 | 去掉首尾空白后为空串 | 否 | 否 |
| 哨兵 | 命中哨兵清单（`-`、`--`、`—`、`/`、`n/a`、`na`、`null`、`none`、`nil`、`未知`、`无`、`暂无`、`待定`、`不适用`） | 否 | 否 |
| 有值 | 其余 | 是（可解析时） | 是 |

哨兵清单只在 `app/config.py` 的 `SENTINELS` 定义一处。

## 4 键判定

| 结论 | 条件 | 证据字段 |
|---|---|---|
| 候选主键 | 唯一率 = 1.0000 且 `null_total` = 0；列名匹配 `^id$`/`_id$`/`_code$`/`_no$`/`_key$` 或 DDL 声明 | `distinct`、`counted`、`unique_rate` |
| 一致外键 | DDL 声明 + 包含度 ≥ 0.95 + 父列是单列主键 | `inclusion`、`cardinality` |
| 推断外键 | 未声明 + 同名列 + 包含度 ≥ 0.95 + 父列是单列主键 + 基数比 ∈ [0.02, 0.95] | 同上 |
| 单向引用 | DDL 声明但包含度 < 0.95 | `inclusion`、`matched/child_distinct` |
| 冲突 | DDL 声明或组合键在样本上出现重复取值 | 冲突表的 `kept` / `dropped` |

- **包含度** = 子表去重取值中能在父表找到的个数 ÷ 子表去重取值个数。
- **基数比** = 子表去重取值个数 ÷ 父表去重取值个数。
- 声明外键只看包含度：声明即权威，基数比 1.0 也算成立；推断外键才要求基数比落在区间内。

## 5 星型与 SCD

| 判定 | 条件 |
|---|---|
| 事实表 | 含度量 且 外键数 ≥ 2 且 有主键 |
| 维度表 | 未被判为事实，且被其他表引用（含单向引用） |
| 待定 | 键冲突，或含度量但外键数 < 2，或无主键 |
| 度量 | 数值型列，列名不是键形状，且不是外键子列 |
| SCD2 | 有 `updated_at`/`valid_from`/`effective_from`/`etl_time`，且可变更列占比 > 0.50 |
| SCD1 | 有版本列，且可变更列占比 ≤ 0.50 |
| SCD0 | 没有版本列，按静态维度处理 |
| 退化维度 | 事实表的主键是单列文本/编码，且不是外键 |

可变更列 = 去掉键列、外键列、版本列之后的列；占比分母是该表全部列数。

## 6 对账

| 账 | 等式 |
|---|---|
| 列账 | `columns_total = adopted + rejected + pending` |
| 行账 | `rows_total = valid + null_row + quarantined` |
| 引用账 | `declared = confirmed + unidirectional`（推断边单列一项） |
| 对象账 | `object_total = fact + dim + pending` |
| 金额 | `metric_total = Σ 事实表 decimal 度量合计` |

任一等式不成立时，`/api/reconcile` 的 `balanced` 为 `false`，页面显示「差」。

## 7 时间与批次

- 入库时间统一 UTC（`created_at`、`finished_at`），显示按 `Asia/Shanghai (+08:00)`，由 `app/web.py` 转换。
- `batch_key` = sha256(规则版本 + 各输入文件 sha256 + 参数)，只由输入决定。
- `run_id` = `SM-YYYYmmddHHMMSS-xxxx`，人读短、机读唯一。
- `input_digest` = sha256(按文件名排序后的「文件名:sha256」序列)。
- 同一 `batch_key` 且状态为 `done` 时重跑直接复用，不新增行。

## 8 空态三态

| 情形 | 结论 |
|---|---|
| 没有任何批次 | 接口 404 + `B_EMPTY`；页面显示空态一句 |
| 有批次但某表样本 0 行 | 该表列判拒绝（空值率 1.0），并记 `Q_TABLE_NO_SAMPLE` |
| 有批次但采纳列 0 | 导出接口 404 + `X_EMPTY_SELECTION` |

## 9 变更与影响评估

下游登记表在 `sample_data/downstream_map.csv`，一个对象一行；同一对象引用多列就多行。

| 字段 | 类型 | 含义 |
|---|---|---|
| `object_id` | TEXT | 下游对象编号，如 `RPT-004`；同一编号同一引用重复导出时保留首行 |
| `object_type` | TEXT | `view` / `job` / `report` / `metric` |
| `object_name` | TEXT | 对象名，冗余列，便于人工核对 |
| `owner` | TEXT | 负责团队 |
| `criticality` | TEXT | `P1` / `P2` / `P3`；不在三档内按 `P3` 处理并记 `M_REGISTRY_BAD_LEVEL` |
| `ref_table` | TEXT | 被引用的贴源表，读取时统一小写（导出端会写成大写） |
| `ref_column` | TEXT | 被引用的列；大小写原样保留，`Store_Code` 与 `store_code` 是两列 |
| `ref_mode` | TEXT | `column` 按列引用；`table` 整表引用（`SELECT *` 或整表抽取） |

变更与结论：

| 字段 | 取值 | 含义 |
|---|---|---|
| `kind` | `dedupe_rows` `fix_orphan` `alter_type` `rename_column` `add_unique` `add_fk` | 变更类型，决定阶段与破坏性 |
| `phase` | `data_fix` `type` `rename` `key` `ref` | 迁移阶段，同表内按此顺序 |
| `breaking` | BOOL | 会改动下游 SQL 的变更（去重行、改类型、改列名） |
| `mode` | `column` `table` `case` `join` | 引用方式：精确列、整表、仅大小写不同、经父键关联 |
| `level` | `break` `review` `info` | 中断 / 核对 / 留痕 |

影响级别 = 引用方式 × 优先级 × 破坏性：

| 引用方式 | P1 | P2 / P3 |
|---|---|---|
| `column` + 破坏性变更 | 中断 | 核对 |
| `column` + 约束类变更 | 核对 | 留痕 |
| `table`（影响列未知） | 核对 | 核对 |
| `case`（仅大小写不同） | 核对 | 核对 |
| `join`（父键变更波及子表列） | 中断 | 核对 |

唯一性判定必须先定空值规则，两种规则都要算：

| 规则 | 判定 | 与 SQL 的关系 |
|---|---|---|
| `sql` | 任一键列为空的行免检 | 与 `UNIQUE` 约束一致（含空值的键不参与唯一性） |
| `strict` | 空值当普通值参与比较 | 与文本全值比较一致 |

- 两种规则结论相反（`ambiguous`）且存在部分为空的键行时，加唯一约束整条阻断，
  记 `M_NULL_KEY_AMBIGUOUS`，等人工定规则后再排。
- 有效行 0 时不能判唯一（`empty`），记 `M_KEY_NO_ROWS`；空集上 `len(set(t)) == len(t)`
  恒为真，这是朴素写法的错点。
- 列名仅大小写不同时按两列处理；折叠比较得到的「合并后仍唯一」是伪唯一，
  改名列整条阻断，记 `M_CASE_PSEUDO_UNIQUE`。

迁移顺序与回滚：

- 顺序取依赖拓扑序，同层按「阶段 → 类型 → 表 → 列」定序；成环整组阻断，记 `M_MIGRATION_CYCLE`。
- 前置被阻断时本条也阻断，记 `M_MIGRATION_BLOCKED`；变更账 = 可执行 + 阻断。
- 回滚按迁移逆序执行，只列可执行变更；删行、孤儿值清洗、类型收窄不可原地回滚，
  记 `M_ROLLBACK_IRREVERSIBLE`，迁移前必须先建备份表。
