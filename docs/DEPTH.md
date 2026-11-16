# 深度登记 starmodel

- 规模：中型（7 个功能模块，81 个文件，83 条测试）
- 核心规则：候选主键 = 唯一率 1 且无空值；外键 = 包含度 ≥ 0.95 且父列是单列主键（推断边另加基数比区间）；星型按「含度量 + 外键数」定事实或维度，维度按可变更列占比定 SCD；影响评估按「引用方式 × 优先级 × 变更破坏性」定中断或核对，迁移顺序取依赖拓扑序。
- 使用者与工作流：禾丰零售数仓组（7 人）导入贴源层 DDL + 样本 CSV + 编码导出件 → 逐列剖析 → 主键外键推断 → 星型建议 → 打印规格单 → 变更前评估下游影响与迁移顺序。

## 功能模块

| 模块 | 使用者动作 | 入口 | 核心文件 | 测试 |
|---|---|---|---|---|
| M1 元数据接入 | 放好 DDL 与样本，跑一次接入 | POST /api/import | `app/modules/m1_ingest/decode.py` `app/modules/m1_ingest/sniff.py` `app/modules/m1_ingest/ddl.py` `app/modules/m1_ingest/reader.py` | `tests/test_ingest.py` |
| M2 类型规则 | 查列契约与阈值，试跑单列归一 | GET /api/contract | `app/modules/m2_contract/coerce.py` `app/modules/m2_contract/evolve.py` `app/modules/m2_contract/contract.py` | `tests/test_contract.py` |
| M3 质量剖析 | 看空值率与唯一率，导出隔离区 | GET /api/profile | `app/modules/m3_profile/profile.py` `app/modules/m3_profile/rules.py` `app/modules/m3_profile/quarantine.py` | `tests/test_profile.py` |
| M4 键推断与建议 | 看主键外键结论与依据数值 | GET /api/model | `app/modules/m4_keys/keys.py` `app/modules/m4_keys/star.py` `app/modules/m4_keys/evidence.py` | `tests/test_keys.py` |
| M5 版本与重跑 | 查历史、续跑、幂等重跑 | GET /api/batches | `app/modules/m5_batches/state.py` `app/modules/m5_batches/resume.py` `app/modules/m5_batches/service.py` | `tests/test_batches.py` |
| M6 规格单导出 | 打印规格单，导出列规格与证据 | GET /api/spec | `app/modules/m6_spec/spec_out.py` `app/modules/m6_spec/csv_out.py` `app/modules/m6_spec/json_out.py` | `tests/test_spec.py` |
| M7 建模影响评估 | 看变更影响面、迁移顺序与回滚提示，提交一条变更试算 | GET /api/impact · POST /api/impact/propose · GET /impact | `app/modules/m7_impact/impact.py` `app/modules/m7_impact/topology.py` `app/modules/m7_impact/rollback.py` | `tests/test_m7_impact.py` |

端到端：`tests/test_e2e.py`（6 条，脏样例一路走到规格单导出，并覆盖七个页面渲染）。

## 陷阱登记

| TRAP | 现象 | 朴素实现会怎样 | 本项目处理 | 处理位置 | 测试 |
|---|---|---|---|---|---|
| TRAP-01 | 会员样本声明 utf-8，字节是 GB18030 | 直接解码崩或中文姓名成乱码 | 编码回退链 + 命中编码留痕 | `app/modules/m1_ingest/decode.py:51` | `tests/test_ingest.py::test_trap01_gb18030_fallback_decodes_chinese` |
| TRAP-02 | 首行与 product 段表头都带 BOM | 首列名带 `\ufeff`，按列名取值全空 | 字节层与单元格层各剥一次 | `app/modules/m1_ingest/decode.py:45` | `tests/test_ingest.py::test_trap02_bom_stripped_from_section_header` |
| TRAP-06 | 金额写 `"1,234.50"`，还有全角 `１９９.９０` | `float()` 抛错或截断成错值 | 千分位与全角先归一，再 Decimal | `app/modules/m2_contract/coerce.py:76` | `tests/test_contract.py::test_trap06_thousands_and_fullwidth_normalize` |
| TRAP-07 | 折扣列有 `-`、`N/A`、`未知`、空串 | 哨兵被当 0 进合计 | 哨兵清单集中定义，与空串分开计数 | `app/modules/m3_profile/profile.py:35` | `tests/test_profile.py::test_trap07_sentinel_excluded_from_metric_total` |
| TRAP-08 | 门店编码 `0012`，有一行是 `107` | 整数化丢前导零，跨表对不上 | 编码列按字符串保形，按列内宽度补零 | `app/modules/m2_contract/coerce.py:68` | `tests/test_keys.py::test_trap08_leading_zero_padding_changes_inclusion` |
| TRAP-09 | 支付流水号 19 位，超过 2^53 | 走数值往返掉末位 | 按字符串保存并导出字符串 | `app/modules/m6_spec/json_out.py:20` | `tests/test_spec.py::test_trap09_bigint_survives_serialization` |
| TRAP-14 | 同日同店同品两条在手数 120 / 118 | 后写覆盖，或整批丢弃 | 保留首见行，冲突行进冲突表并可导出 | `app/modules/m3_profile/rules.py:48` | `tests/test_profile.py::test_trap14_duplicate_business_key_conflict` |
| TRAP-17 | 样本表头是旧列名 `store_id`；积分列出现 `980.5` | `KeyError` 或整列空值；int 列解析失败 | 改名映射 + 类型收敛 + 放宽留痕 | `app/modules/m2_contract/evolve.py:61` | `tests/test_contract.py::test_trap17_renamed_column_mapped_to_ddl` |
| TRAP-21 | DDL 声明 store→region，样本里有区域 `09` 不存在 | 只查声明不查数据，单向引用当成立 | 双向核验：包含度不足判单向，缺声明的推断补边 | `app/modules/m4_keys/keys.py:160` | `tests/test_keys.py::test_trap21_unidirectional_reference_detected` |
| TRAP-22 | 渠道写 `APP`/`app`/`App`/`APP端` | 合并顺序不同结果不同 | 等价类 + 固定代表元，与行序无关 | `app/modules/m2_contract/coerce.py:125` | `tests/test_keys.py::test_trap22_alias_merge_is_order_independent` |
| TRAP-27 | 组合键某一列为空（如门店+会员号），空值是否参与唯一性没定 | 按 `len(set(元组)) == len(元组)` 判：含空值的键免检时判唯一，全算时判不唯一，改约束的依据随机翻转；有效行 0 时该式还返回真 | 两种空值规则各算一遍，结论相反就阻断，等人工定规则；有效行 0 单独判不可判 | `app/modules/m7_impact/impact.py:194` | `tests/test_m7_impact.py::test_trap27_partial_null_key_blocks_unique_change` |
| TRAP-28 | `store_code` 与 `Store_Code` 仅大小写不同，被当成同一列 | 折叠后去重数变小，得出「合并后仍唯一」的伪唯一结论，据此直接改名并删除旧列 | 按大小写敏感当作两列；折叠结果只用于提示伪唯一，改名列整条阻断 | `app/modules/m7_impact/impact.py:227` | `tests/test_m7_impact.py::test_trap28_case_pseudo_unique_blocks_column_merge` |

## 对账规则

- 列账：列数 = 采纳 + 拒绝 + 待定
- 行账：样本行数 = 有效 + 空值行 + 隔离行
- 引用账：DDL 声明外键 = 一致 + 单向；另记推断边条数
- 对象账：贴源表数 = 事实 + 维度 + 待定
- 金额合计：只统计事实表里的 decimal 度量，Decimal 两位小数
- 变更账：变更条数 = 可执行 + 阻断；迁移顺序条数 = 可执行；回滚条数 = 可执行，且顺序与之相反

## 流水线义务落点

| 义务 | 落点 |
|---|---|
| 阶段化 | `app/modules/m5_batches/service.py:69`，五段各自计数并计时 |
| 幂等重跑 | `app/modules/m5_batches/resume.py:11`，同批次键直接复用 |
| 坏行隔离 | `app/modules/m1_ingest/sniff.py:123`，列数不齐行进隔离 |
| 原因码 | `app/reasons.py`（唯一来源），页面与导出都从这里取 |
| 对账 | `app/modules/m3_profile/rules.py:120`，四账 + 金额合计 |
| 运行清单 | `app/manifest.py:42`，run_id + 输入 sha256 + 参数 |
| 原子性 | `app/store.py:171`，单事务先删后插，异常整体回滚 |
| 记忆上限 | `app/modules/m1_ingest/sniff.py:71`，逐行流式解析，不整表读入 |

M7 不进批次流水线：影响评估按请求现算，只读已落库结论与行级归一结果，不写批次、
不新增阶段行（阶段计数仍是 5 段），因此重跑与连点都不改变批次内容。

## 已知不覆盖

- 样本按「每表一段」拼在一个文件里；引号内含换行的分段边界不支持，工程上按段分开导出即可。
- 星型只给事实/维度/SCD 建议，不产出物理 DDL 与索引方案。
- 外键推断只在同名列之间做，不做同义列（如 `cust_no` 与 `member_id`）的语义匹配。
- 分区、视图、代理键生成策略不在本工具范围内。
- 下游登记表 `sample_data/downstream_map.csv` 靠人工维护；影响面只沿引用边传一跳，
  不解析下游 SQL 文本本身，也不做多跳血缘。
- 迁移顺序只排本模块派生的变更（加约束、清洗、改类型、改列名），不含数据回填与
  双写切换这类工程动作。
