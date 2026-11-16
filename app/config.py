"""starmodel 全局配置：阈值、编码链、哨兵清单、别名表、类型宽度表。

这里是规则的唯一来源，其他模块只读不改：
- 编码回退链先按声明、再按固定顺序尝试；
- 空值哨兵集中定义，空串与哨兵分开计，二者都不参与合计与去重；
- 编码列一律按字符串保形，并左补零到 DDL 声明宽度后再做包含度比对；
- 键与星型的判定阈值全部是可复算的小数，不写死在业务函数里。
"""
from __future__ import annotations

import datetime as dt
import os
from dataclasses import dataclass, replace
from decimal import Decimal
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CHINA_TZ = dt.timezone(dt.timedelta(hours=8), "Asia/Shanghai")
UTC = dt.timezone.utc

RULE_VERSION = "starmodel/1.0.0"
DECIMAL_QUANT = Decimal("0.01")
MIN_SAMPLE_ROWS = 3
MAX_SAMPLE_ROWS_PER_TABLE = 500

STAGES = ("import", "contract", "profile", "keys", "spec")
STAGE_LABELS = {
    "import": "元数据接入",
    "contract": "类型规则",
    "profile": "质量剖析",
    "keys": "键推断",
    "spec": "规格单",
    "impact": "影响评估",
}

# 编码回退链：gb18030 是 gbk/gb2312 的超集，放在最后兜底
ENCODING_CHAIN = ("utf-8-sig", "utf-8", "gb18030")
CHARSET_ALIASES = {
    "gb2312": "gb18030",
    "gbk": "gb18030",
    "gb18030": "gb18030",
    "utf8": "utf-8",
    "utf-8": "utf-8",
    "utf-8-sig": "utf-8-sig",
}

# 空值哨兵：与真空值（空串）分开统计，都不计入去重与合计
SENTINELS = frozenset(
    {"-", "--", "—", "/", "n/a", "na", "null", "none", "nil",
     "未知", "无", "暂无", "待定", "不适用"}
)

# 列处置优先级：命中即停，顺序写死避免依赖代码书写顺序
DISPOSITION_ORDER = ("rejected", "pending", "adopted")
DISPOSITION_LABELS = {"adopted": "采纳", "rejected": "拒绝", "pending": "待定"}

# 主键 / 外键阈值
PK_UNIQUE_RATE = Decimal("1")
PK_NULL_TOTAL = 0
FK_INCLUSION_MIN = Decimal("0.95")
FK_CARDINALITY_MIN = Decimal("0.02")
FK_CARDINALITY_MAX = Decimal("0.95")

# 列处置阈值
REJECT_NULL_RATE = Decimal("1")
REJECT_UNPARSABLE_RATE = Decimal("0.5")

# 星型建议阈值
FACT_MIN_FOREIGN_KEYS = 2
METRIC_UNIQUE_RATE_MAX = Decimal("0.9")
SCD2_CHANGE_RATIO = Decimal("0.50")

# 类型归一宽度：同名同义列跨表收敛时取最宽
TYPE_WIDTH = {
    "varchar": 1,
    "char": 1,
    "text": 3,
    "int": 2,
    "bigint": 2,
    "numeric": 2,
    "date": 2,
    "timestamp": 2,
}

# 别名等价类：键是代表元，值是该主体的其他写法；代表元与输入顺序无关
ALIAS_GROUPS = {
    "APP": ("app", "App", "APP端", "app端"),
    "小程序": ("微信小程序", "微信小程序端"),
    "门店": ("门店POS", "pos"),
}
# 只有这些列做别名合并，其余列原样保留（避免把自由文本也归并）
ALIAS_COLUMNS = frozenset({"channel"})

# 度量列的候选类型：只有数值型才可能是度量
METRIC_KINDS = ("int", "bigint", "decimal")
DESCRIPTIVE_KINDS = ("text", "code", "date", "timestamp", "bool")

# 事实表 / 维度表 / 待定
ROLE_LABELS = {"fact": "事实表", "dim": "维度表", "pending": "待定"}
SCD_LABELS = {"SCD0": "SCD0 静态", "SCD1": "SCD1 覆盖", "SCD2": "SCD2 版本"}


@dataclass(frozen=True)
class Settings:
    """运行参数；测试用 replace() 覆盖，不走环境变量分支。"""

    db_path: str = str(PROJECT_ROOT / "var" / "starmodel.sqlite")
    sample_dir: str = str(PROJECT_ROOT / "sample_data")
    ddl_name: str = "warehouse_ddl.sql"
    dump_names: tuple = ("sample_dump.csv", "sample_dump_gb18030.csv")
    rule_version: str = RULE_VERSION
    export_dir: str = str(PROJECT_ROOT / "var" / "export")

    @classmethod
    def from_env(cls) -> "Settings":
        base = cls()
        db_path = os.getenv("STARMODEL_DB") or base.db_path
        sample_dir = os.getenv("STARMODEL_SAMPLE_DIR") or base.sample_dir
        return replace(cls(), db_path=db_path, sample_dir=sample_dir)

    @property
    def data_dir(self) -> Path:
        return Path(self.db_path).resolve().parent

    @property
    def ddl_path(self) -> Path:
        return Path(self.sample_dir) / self.ddl_name

    def dump_paths(self) -> list[Path]:
        return [Path(self.sample_dir) / name for name in self.dump_names]
