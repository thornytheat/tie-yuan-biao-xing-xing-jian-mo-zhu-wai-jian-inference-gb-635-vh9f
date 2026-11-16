"""原因码唯一来源：跨模块共享，页面、导出、测试都从这里取文案。

一个原因码 = 级别 + 所属阶段 + 中文结论 + 复核建议。级别三档：
info（已自动处理，留痕）、warn（需人工看一眼）、error（已隔离或已判拒绝）。
"""
from __future__ import annotations

from typing import NamedTuple

INFO = "info"
WARN = "warn"
ERROR = "error"
LEVEL_ORDER = {ERROR: 0, WARN: 1, INFO: 2}
LEVEL_LABELS = {ERROR: "隔离", WARN: "提示", INFO: "留痕"}


class Reason(NamedTuple):
    code: str
    level: str
    stage: str
    title: str
    hint: str


def _r(code, level, stage, title, hint):
    return Reason(code, level, stage, title, hint)


REASONS = {
    # ---- M1 元数据接入 ----
    "E_CHARSET_FALLBACK": _r(
        "E_CHARSET_FALLBACK", INFO, "import",
        "声明编码解不通，已按回退链换成实际编码", "导出端声明与实际字节不符是常态"),
    "E_BOM_STRIPPED": _r(
        "E_BOM_STRIPPED", INFO, "import",
        "首行或分段表头带 UTF-8 BOM，已剥离", "本系统导出的 CSV 也带 BOM，导回需同样剥离"),
    "E_DECODE_FAILED": _r(
        "E_DECODE_FAILED", ERROR, "import",
        "回退链内所有编码都无法解码该文件", "确认来源系统编码后补进回退链"),
    "E_DDL_UNPARSED": _r(
        "E_DDL_UNPARSED", ERROR, "import",
        "DDL 里有一段 CREATE TABLE 无法解析", "多半是括号或类型里混了中文逗号"),
    "Q_SECTION_HEADER": _r(
        "Q_SECTION_HEADER", INFO, "import",
        "样本分段标记，按表名切开", "分段标记形如 ## table=表名"),
    "Q_DUP_HEADER": _r(
        "Q_DUP_HEADER", INFO, "import",
        "分段内重复出现的表头行已剔除", "分段导出时每段都会重写一次表头"),
    "Q_COLUMN_COUNT": _r(
        "Q_COLUMN_COUNT", ERROR, "import",
        "列数多于表头（行尾多出分隔符）", "该行进隔离区，好行继续"),
    "Q_ROW_TRUNCATED": _r(
        "Q_ROW_TRUNCATED", ERROR, "import",
        "列数少于表头（末行被截断）", "回源重导该段，不要补默认值"),
    "Q_TABLE_NO_SAMPLE": _r(
        "Q_TABLE_NO_SAMPLE", WARN, "import",
        "DDL 里的表在样本里没有分段", "该表全部列按空值处理，进待定或拒绝"),
    "Q_SECTION_UNKNOWN": _r(
        "Q_SECTION_UNKNOWN", WARN, "import",
        "样本分段在 DDL 里找不到同名表", "先补 DDL 再看该段"),
    # ---- M2 类型规则 ----
    "Q_COLUMN_CASE_CONFLICT": _r(
        "Q_COLUMN_CASE_CONFLICT", ERROR, "contract",
        "同一张表里列名仅大小写不同", "两个团队各建了一次，保留有样本的那个"),
    "Q_SENTINEL_VALUE": _r(
        "Q_SENTINEL_VALUE", INFO, "contract",
        "命中空值哨兵，按空值计，不参与去重与合计", "哨兵不是 0"),
    "Q_BAD_NUMBER": _r(
        "Q_BAD_NUMBER", ERROR, "contract",
        "数值列取值无法归一为数字", "检查是否混入单位后缀或说明文字"),
    "Q_BAD_DATE": _r(
        "Q_BAD_DATE", ERROR, "contract",
        "日期列取值无法归一为日期", "支持 年/月/日、年-月-日、年月日三种写法"),
    "Q_CODE_PADDED": _r(
        "Q_CODE_PADDED", WARN, "contract",
        "编码列缺前导零，已左补零到声明宽度", "补零前后包含度结论不同，证据里两值都留"),
    "Q_CODE_PRESERVED": _r(
        "Q_CODE_PRESERVED", INFO, "contract",
        "编码列按字符串保形，不做整数化", "整数化会丢前导零，对不上父表"),
    "Q_BIGINT_TEXT": _r(
        "Q_BIGINT_TEXT", WARN, "contract",
        "整数超过 2^53，按字符串保存", "JSON 数值往返会丢精度"),
    "E_TYPE_WIDENED": _r(
        "E_TYPE_WIDENED", WARN, "contract",
        "同名或同列类型需要放宽", "放宽方向写进列契约，向下取整会丢数据"),
    "E_COLUMN_RENAMED": _r(
        "E_COLUMN_RENAMED", INFO, "contract",
        "样本表头是旧列名，已映射到 DDL 列", "映射表在列契约里可查"),
    # ---- M3 质量剖析 ----
    "Q_ALL_NULL": _r(
        "Q_ALL_NULL", ERROR, "profile",
        "整列空值或哨兵，或样本里没有该列", "列判拒绝，不进规格单正表"),
    "Q_UNPARSABLE_MAJORITY": _r(
        "Q_UNPARSABLE_MAJORITY", ERROR, "profile",
        "不可解析取值占比过半", "列判拒绝，先修来源再建模"),
    "Q_LOW_UNIQUE": _r(
        "Q_LOW_UNIQUE", WARN, "profile",
        "唯一率低于主键阈值", "只能做属性或需要组合键"),
    "Q_SAMPLE_SHORT": _r(
        "Q_SAMPLE_SHORT", WARN, "profile",
        "样本行数少于最小样本数", "列判待定，补足样本再判主键"),
    "Q_KEY_COLUMN_PENDING": _r(
        "Q_KEY_COLUMN_PENDING", WARN, "profile",
        "所在表的键列组合有重复取值", "键列判待定，人工确认保留哪条"),
    # ---- M4 键推断 ----
    "K_DUP_KEY_CONFLICT": _r(
        "K_DUP_KEY_CONFLICT", ERROR, "keys",
        "重复业务键出现冲突取值", "保留首见行，其余进冲突表并导出"),
    "K_PK_CONFIRMED": _r(
        "K_PK_CONFIRMED", INFO, "keys",
        "唯一率 1 且无空值，判为候选主键", "证据列给出唯一率与去重数"),
    "K_FK_CONFIRMED": _r(
        "K_FK_CONFIRMED", INFO, "keys",
        "包含度达标且父列是主键候选", "证据列给出包含度与基数比"),
    "K_FK_INFERRED": _r(
        "K_FK_INFERRED", INFO, "keys",
        "DDL 未声明，样本包含度达标推断为外键", "需建模复核后补进 DDL"),
    "K_FK_UNIDIRECTIONAL": _r(
        "K_FK_UNIDIRECTIONAL", ERROR, "keys",
        "DDL 声明了外键，但样本包含度不足", "父表缺值，单向引用，不能直接建模"),
    "K_FK_LOW_INCLUSION": _r(
        "K_FK_LOW_INCLUSION", WARN, "keys",
        "包含度低于 0.95，不足以判外键", "多半是编码宽度或清洗不一致"),
    "K_FK_CARD_OUT": _r(
        "K_FK_CARD_OUT", WARN, "keys",
        "基数比不在 [0.02, 0.95] 区间", "取值太集中或几乎一一对应，不像外键"),
    "K_ALIAS_MERGED": _r(
        "K_ALIAS_MERGED", INFO, "keys",
        "同一主体多个写法已合并为代表元", "合并前后去重数不同，两值都留痕"),
    "K_NO_PK": _r(
        "K_NO_PK", WARN, "keys",
        "该表没有候选主键", "只能按待定处理，不做事实表"),
    "K_FACT_FK_SHORT": _r(
        "K_FACT_FK_SHORT", WARN, "keys",
        "含度量但外键数少于 2，不进事实表", "要么补外键，要么按宽表处理"),
    "K_FACT_CONFIRMED": _r(
        "K_FACT_CONFIRMED", INFO, "keys",
        "含度量、外键达标且有主键，判为事实表", "证据列给出度量数与外键数"),
    "K_DIM_REFERENCED": _r(
        "K_DIM_REFERENCED", INFO, "keys",
        "被其他表引用或自身无度量，判为维度表", "证据列给出被引用标记"),
    "K_SCD_BY_RATIO": _r(
        "K_SCD_BY_RATIO", INFO, "keys",
        "按可变更列占比给出 SCD 类型", "占比大于 0.5 判 SCD2，否则 SCD1"),
    "K_NO_VERSION_COLUMN": _r(
        "K_NO_VERSION_COLUMN", WARN, "keys",
        "维度表没有更新列，按静态维度处理", "补 updated_at 后才能识别变更"),
    "K_FK_CARD_INELIGIBLE": _r(
        "K_FK_CARD_INELIGIBLE", WARN, "keys",
        "父列不是主键候选，不能作外键指向", "先修主键再判外键"),
    "K_NO_METRIC": _r(
        "K_NO_METRIC", WARN, "keys",
        "没有可识别的度量列", "不出现在事实表候选里"),
    "K_DATE_GAIN": _r(
        "K_DATE_GAIN", INFO, "keys",
        "事实表里含日期列，可作日期维关联键", "日期维不入贴源层"),
    "K_DEGENERATE": _r(
        "K_DEGENERATE", INFO, "keys",
        "事实表里的高基数码列，作退化维度", "订单号这类列不单独建维"),
    # ---- M5 版本与重跑 ----
    "B_ABORTED": _r(
        "B_ABORTED", ERROR, "batches",
        "批次在某个阶段中断", "用续跑从断点继续，已接入的元数据不重读"),
    "B_IDEMPOTENT_REPLAY": _r(
        "B_IDEMPOTENT_REPLAY", INFO, "batches",
        "同一输入摘要命中已完成批次，直接复用结果", "重跑不新增行、不重出规格单"),
    "B_RESUME_NO_STATE": _r(
        "B_RESUME_NO_STATE", WARN, "batches",
        "没有可用的断点状态，按全新批次重跑", "首次失败过早时会遇到"),
    "B_EMPTY": _r(
        "B_EMPTY", ERROR, "batches",
        "还没有任何批次", "先跑一次接入"),
    # ---- M6 规格单 ----
    "X_EMPTY_SELECTION": _r(
        "X_EMPTY_SELECTION", WARN, "spec",
        "导出的行集为空", "先跑一次接入再导出"),
    # ---- M7 建模影响评估 ----
    "M_CHANGE_DERIVED": _r(
        "M_CHANGE_DERIVED", INFO, "impact",
        "从本轮结论派生出的建议变更", "每条都能追到具体的键或列结论"),
    "M_IMPACT_BREAK": _r(
        "M_IMPACT_BREAK", WARN, "impact",
        "变更会打断下游 SQL 或报表", "先改下游再改模型"),
    "M_IMPACT_WILDCARD": _r(
        "M_IMPACT_WILDCARD", WARN, "impact",
        "下游对象整表引用，影响列判不出来", "改成按列引用后再评估"),
    "M_PSEUDO_MATCH": _r(
        "M_PSEUDO_MATCH", WARN, "impact",
        "下游引用列与变更列只有大小写不同", "按两列处理，改名会直接断"),
    "M_NULL_KEY_AMBIGUOUS": _r(
        "M_NULL_KEY_AMBIGUOUS", ERROR, "impact",
        "组合键有部分为空，唯一性结论随空值规则翻转", "先定空值是否参与唯一性"),
    "M_CASE_PSEUDO_UNIQUE": _r(
        "M_CASE_PSEUDO_UNIQUE", ERROR, "impact",
        "只有大小写不同的两列折叠后得到伪唯一", "两列各自独立，不合也不删"),
    "M_KEY_NO_ROWS": _r(
        "M_KEY_NO_ROWS", ERROR, "impact",
        "有效行 0，不能据此判唯一", "补足样本再判"),
    "M_MIGRATION_CYCLE": _r(
        "M_MIGRATION_CYCLE", ERROR, "impact",
        "变更依赖成环，排不出迁移顺序", "先拆掉环里的一条依赖"),
    "M_MIGRATION_BLOCKED": _r(
        "M_MIGRATION_BLOCKED", WARN, "impact",
        "前置变更被阻断，本条也不能排", "先解前置"),
    "M_ROLLBACK_IRREVERSIBLE": _r(
        "M_ROLLBACK_IRREVERSIBLE", WARN, "impact",
        "该变更不能原地回滚", "迁移前先建备份表"),
    "M_ROLLBACK_ORDER": _r(
        "M_ROLLBACK_ORDER", INFO, "impact",
        "回滚按迁移逆序执行", "顺序错了会撞上外键"),
    "M_REGISTRY_SHORT_ROW": _r(
        "M_REGISTRY_SHORT_ROW", WARN, "impact",
        "下游登记表某行列数不足", "按表头补齐 8 列后再导"),
    "M_REGISTRY_BAD_LEVEL": _r(
        "M_REGISTRY_BAD_LEVEL", WARN, "impact",
        "下游登记表优先级不在 P1..P3", "按影响面重新定级"),
    "M_REGISTRY_DUP_ROW": _r(
        "M_REGISTRY_DUP_ROW", INFO, "impact",
        "下游登记表有重复导出行，保留首行", "同一对象同一引用只留一条"),
    "M_NO_CHANGE": _r(
        "M_NO_CHANGE", WARN, "impact",
        "本轮结论没有可执行的变更建议", "先看键与列结论"),
    "M_BAD_CHANGE": _r(
        "M_BAD_CHANGE", ERROR, "impact",
        "提交的变更不合法", "按变更类型与表列名重新提交"),
}


def get(code: str) -> Reason:
    return REASONS.get(code, Reason(code, WARN, "unknown", code, "未登记的原因码"))


def level_of(code: str) -> str:
    return get(code).level


def label(code: str) -> str:
    return get(code).title


def stage_of(code: str) -> str:
    return get(code).stage


def is_error(code: str) -> bool:
    return level_of(code) == ERROR


def catalog() -> list[dict]:
    rows = sorted(REASONS.values(), key=lambda r: (r.stage, LEVEL_ORDER[r.level], r.code))
    return [
        {"code": r.code, "level": r.level, "stage": r.stage, "title": r.title,
         "hint": r.hint, "level_label": LEVEL_LABELS[r.level]}
        for r in rows
    ]
