"""列契约：可选类型清单、类型判定规则表、阈值与空值哨兵清单。

契约是「数字与时间的定义」的唯一落点，页面和规格单直接引用这里的行，
不在模板里另写一份。
"""
from __future__ import annotations

from ... import config

CONTRACT_VERSION = "spec/1.0"

KINDS = (
    {"kind": "code", "label": "编码", "note": "按字符串保形，左补零到声明宽度"},
    {"kind": "int", "label": "整数", "note": "绝对值不超过 2^53"},
    {"kind": "bigint", "label": "大整数", "note": "按字符串保存，禁止走数值往返"},
    {"kind": "decimal", "label": "数值", "note": "Decimal，最小刻度 0.01"},
    {"kind": "date", "label": "日期", "note": "归一为 YYYY-MM-DD"},
    {"kind": "timestamp", "label": "时间戳", "note": "归一为 YYYY-MM-DD HH:MM:SS"},
    {"kind": "text", "label": "文本", "note": "其余取值"},
)

TYPE_RULES = (
    {"id": "R-TYP-1", "title": "空值与哨兵先剔除",
     "detail": f"哨兵 {len(config.SENTINELS)} 个，与空串分开计数，都不参与去重与合计"},
    {"id": "R-TYP-2", "title": "千分位与全角先转半角",
     "detail": "「1,234.50」与「１２３４.５」归一后同为 1234.50"},
    {"id": "R-TYP-3", "title": "编码列不做整数化",
     "detail": "全数字且有前导零，或声明为字符型且长度 ≥ 8，判编码"},
    {"id": "R-TYP-4", "title": "超 2^53 的整数按字符串",
     "detail": f"阈值 2^53 = {2 ** 53}"},
    {"id": "R-TYP-5", "title": "日期三种写法都归一",
     "detail": "年/月/日、年-月-日、年月日；带时分秒的判时间戳"},
    {"id": "R-TYP-6", "title": "别名词表只有登记过的列生效",
     "detail": "当前登记列：" + "、".join(sorted(config.ALIAS_COLUMNS))},
)

THRESHOLDS = (
    {"key": "主键唯一率", "value": str(config.PK_UNIQUE_RATE), "note": "等于 1 才可能作主键"},
    {"key": "外键包含度下限", "value": str(config.FK_INCLUSION_MIN),
     "note": "子表取值落在父表内的比例"},
    {"key": "外键基数比区间",
     "value": f"[{config.FK_CARDINALITY_MIN}, {config.FK_CARDINALITY_MAX}]",
     "note": "子表去重数 / 父表去重数"},
    {"key": "事实表外键下限", "value": str(config.FACT_MIN_FOREIGN_KEYS),
     "note": "少于该数不进事实表"},
    {"key": "SCD2 变更列占比", "value": str(config.SCD2_CHANGE_RATIO),
     "note": "无更新列一律 SCD0"},
    {"key": "最小样本行数", "value": str(config.MIN_SAMPLE_ROWS), "note": "少于该数列判待定"},
)

DISPOSITION_RULES = (
    {"id": "R-DSP-1", "level": "拒绝", "detail": "空值率 = 1.0，或不可解析占比 ≥ 0.5"},
    {"id": "R-DSP-2", "level": "待定", "detail": "键列所在表有重复取值，或样本行数不足"},
    {"id": "R-DSP-3", "level": "采纳", "detail": "其余列"},
)


def document() -> dict:
    return {
        "contract_version": CONTRACT_VERSION,
        "kinds": list(KINDS),
        "type_rules": list(TYPE_RULES),
        "thresholds": list(THRESHOLDS),
        "disposition_rules": list(DISPOSITION_RULES),
        "sentinels": sorted(config.SENTINELS),
        "aliases": {key: list(value) for key, value in config.ALIAS_GROUPS.items()},
        "rule_version": config.RULE_VERSION,
    }
