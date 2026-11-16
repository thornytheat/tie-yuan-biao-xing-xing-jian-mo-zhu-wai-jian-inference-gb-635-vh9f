"""列演进：样本表头是旧列名时映射到 DDL 列；同名同义列跨表收敛到最宽类型。

两件事分开：
- 改名映射（store_id → store_code）必须在取得取值之前完成，否则该列被当成整列空值；
- 类型收敛（order_id varchar(16) / varchar(20) → varchar(20)）在列定义层面完成。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ... import config

# 只在样本侧出现的旧列名，(表名小写, 旧列名小写) → DDL 列名
COLUMN_RENAMES = {
    ("odl_store", "store_id"): "store_code",
    ("odl_store", "store_no"): "store_code",
    ("odl_member", "member_no"): "member_id",
    ("odl_product", "product_id"): "product_code",
}


@dataclass
class HeaderMap:
    table_name: str
    mapped: list = field(default_factory=list)
    renames: list = field(default_factory=list)
    unmatched: list = field(default_factory=list)
    missing: list = field(default_factory=list)

    @property
    def codes(self) -> list:
        return ["E_COLUMN_RENAMED"] if self.renames else []

    def index_of(self, column_name: str) -> int:
        """只认精确列名：大小写冲突时，靠 DDL 顺序决定谁拿到样本列。"""
        for index, name in enumerate(self.mapped):
            if name == column_name:
                return index
        return -1


def map_headers(table, header: list) -> HeaderMap:
    """三段匹配：原样、忽略大小写、登记过的旧列名。"""
    result = HeaderMap(table_name=table.name)
    ddl_names = [column.name for column in table.columns]
    first_by_lower: dict[str, str] = {}
    for name in ddl_names:
        first_by_lower.setdefault(name.lower(), name)
    for raw in header:
        name = (raw or "").strip().lstrip("\ufeff")
        if not name:
            result.mapped.append("")
            continue
        if name in ddl_names:
            result.mapped.append(name)
            continue
        if name.lower() in first_by_lower:
            target = first_by_lower[name.lower()]
            result.mapped.append(target)
            if target != name:
                result.renames.append((name, target))
            continue
        renamed = COLUMN_RENAMES.get((table.name.lower(), name.lower()))
        if renamed and renamed in ddl_names:
            result.mapped.append(renamed)
            result.renames.append((name, renamed))
            continue
        result.mapped.append("")
        result.unmatched.append(name)
    result.missing = [name for name in ddl_names if name not in result.mapped]
    return result


def converge_types(ddl) -> dict:
    """同名列在多张表里声明宽度不同时取最宽；返回 (列名 → 收敛结果)。

    收敛只影响并集比对与规格单里的列契约，不改原表的 DDL 声明文本。
    """
    seen: dict[str, dict[str, int]] = {}
    for table in ddl.tables:
        for column in table.columns:
            key = column.name.lower()
            seen.setdefault(key, {})
            width = column.width or column.precision or 0
            seen[key][table.name] = width
    findings = {}
    for key, per_table in seen.items():
        widths = {name: width for name, width in per_table.items() if width}
        if len(widths) < 2:
            continue
        widest = max(widths.values())
        if len(set(widths.values())) == 1:
            continue
        findings[key] = {
            "column": key,
            "widest": widest,
            "tables": widths,
            "narrow_tables": [name for name, width in widths.items() if width < widest],
            "code": "E_TYPE_WIDENED",
        }
    return findings


def rule_rows() -> list[dict]:
    """列演进规则表：给 /api/contract 与规格单脚注用。"""
    return [
        {"id": "R-EVO-1", "title": "旧列名映射",
         "detail": "|".join(f"{old}→{new}" for (_, old), new in COLUMN_RENAMES.items())},
        {"id": "R-EVO-2", "title": "类型收敛",
         "detail": f"同名同义列取最宽声明（{len(config.TYPE_WIDTH)} 类基础类型）"},
        {"id": "R-EVO-3", "title": "大小写冲突",
         "detail": "同表内仅大小写不同的列保留有样本的那个，其余判待定"},
    ]
