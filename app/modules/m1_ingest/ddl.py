"""DDL 解析：CREATE TABLE → 列定义、主键、外键；不连数据库，只读文本。

要处理的真实情况：
- 类型带精度：varchar(20)、numeric(12,2)、bigint；
- 行内注释 COMMENT '...' 与列约束混写；
- 表级 PRIMARY KEY (...) 与 FOREIGN KEY (...) REFERENCES 表(列)；
- 同一张表里两个团队各建一次列，列名只差大小写。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

CREATE_RE = re.compile(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?`?([A-Za-z0-9_]+)`?\s*\(",
                       re.IGNORECASE)
COLUMN_RE = re.compile(
    r"^`?([A-Za-z_][A-Za-z0-9_]*)`?\s+(?:(`?[A-Za-z]+`?)\s*(?:\(([^)]*)\))?)?\s*(.*)$",
    re.DOTALL)
PRIMARY_RE = re.compile(r"^PRIMARY\s+KEY\s*\(([^)]*)\)$", re.IGNORECASE)
FOREIGN_RE = re.compile(
    r"^FOREIGN\s+KEY\s*\(([^)]*)\)\s*REFERENCES\s+`?([A-Za-z0-9_]+)`?\s*\(([^)]*)\)$",
    re.IGNORECASE)
INLINE_REF_RE = re.compile(
    r"REFERENCES\s+`?([A-Za-z0-9_]+)`?\s*\(([^)]*)\)", re.IGNORECASE)
COMMENT_RE = re.compile(r"COMMENT\s+'([^']*)'", re.IGNORECASE)


@dataclass
class ColumnDef:
    name: str
    ordinal: int
    raw_type: str
    base_type: str
    width: int
    precision: int
    scale: int
    nullable: bool
    comment: str
    inline_ref: tuple | None = None
    inline_pk: bool = False


@dataclass
class TableDef:
    name: str
    ordinal: int
    columns: list = field(default_factory=list)
    primary_key: list = field(default_factory=list)
    foreign_keys: list = field(default_factory=list)

    def column(self, name: str) -> ColumnDef | None:
        lowered = name.lower()
        for col in self.columns:
            if col.name.lower() == lowered:
                return col
        return None

    @property
    def case_conflicts(self) -> list:
        """列名仅大小写不同的分组，每组按 DDL 顺序给出全部写法。"""
        groups: dict[str, list] = {}
        for col in self.columns:
            groups.setdefault(col.name.lower(), []).append(col.name)
        return [names for names in groups.values() if len(names) > 1]


@dataclass
class Ddl:
    tables: list = field(default_factory=list)
    codes: list = field(default_factory=list)

    def table(self, name: str) -> TableDef | None:
        lowered = name.lower()
        for table in self.tables:
            if table.name.lower() == lowered:
                return table
        return None


def _split_top_level(body: str) -> list[str]:
    parts, depth, buf = [], 0, []
    for ch in body:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(buf))
            buf = []
            continue
        buf.append(ch)
    if "".join(buf).strip():
        parts.append("".join(buf))
    return [part.strip() for part in parts if part.strip()]


def _matching_paren(text: str, open_index: int) -> int:
    depth = 0
    for index in range(open_index, len(text)):
        if text[index] == "(":
            depth += 1
        elif text[index] == ")":
            depth -= 1
            if depth == 0:
                return index
    return -1


def _int_list(raw: str) -> list[int]:
    return [int(m) for m in re.findall(r"\d+", raw or "")]


def _names(raw: str) -> list[str]:
    return [name.strip().strip("`") for name in raw.split(",") if name.strip()]


def parse_column(part: str, ordinal: int) -> ColumnDef | None:
    match = COLUMN_RE.match(part)
    if not match:
        return None
    name, raw_type, args, rest = match.groups()
    raw_type = (raw_type or "").strip("`").lower()
    numbers = _int_list(args or "")
    width = numbers[0] if raw_type in ("varchar", "char", "nvarchar") and numbers else 0
    precision = numbers[0] if raw_type in ("numeric", "decimal") and numbers else 0
    scale = numbers[1] if raw_type in ("numeric", "decimal") and len(numbers) > 1 else 0
    upper = rest.upper()
    ref = INLINE_REF_RE.search(rest or "")
    inline_ref = None
    if ref:
        inline_ref = (ref.group(1), _names(ref.group(2)))
    comment = COMMENT_RE.search(rest or "")
    return ColumnDef(
        name=name,
        ordinal=ordinal,
        raw_type=raw_type,
        base_type=raw_type,
        width=width,
        precision=precision,
        scale=scale,
        nullable="NOT NULL" not in upper,
        comment=comment.group(1) if comment else "",
        inline_ref=inline_ref,
        inline_pk="PRIMARY KEY" in upper,
    )


def parse_ddl(text: str) -> Ddl:
    ddl = Ddl()
    cursor = 0
    ordinal = 0
    while True:
        match = CREATE_RE.search(text, cursor)
        if not match:
            break
        open_index = text.index("(", match.start())
        close_index = _matching_paren(text, open_index)
        if close_index < 0:
            ddl.codes.append("E_DDL_UNPARSED")
            break
        body = text[open_index + 1:close_index]
        cursor = close_index + 1
        table = TableDef(name=match.group(1), ordinal=ordinal)
        ordinal += 1
        parsed_any = False
        for part in _split_top_level(body):
            primary = PRIMARY_RE.match(part)
            if primary:
                table.primary_key = _names(primary.group(1))
                parsed_any = True
                continue
            foreign = FOREIGN_RE.match(part)
            if foreign:
                table.foreign_keys.append({
                    "columns": _names(foreign.group(1)),
                    "ref_table": foreign.group(2),
                    "ref_columns": _names(foreign.group(3)),
                    "declared": True,
                })
                parsed_any = True
                continue
            column = parse_column(part, len(table.columns) + 1)
            if column is None:
                continue
            table.columns.append(column)
            parsed_any = True
            if column.inline_pk and not table.primary_key:
                table.primary_key = [column.name]
            if column.inline_ref:
                table.foreign_keys.append({
                    "columns": [column.name],
                    "ref_table": column.inline_ref[0],
                    "ref_columns": column.inline_ref[1],
                    "declared": True,
                })
        if not parsed_any or not table.columns:
            ddl.codes.append("E_DDL_UNPARSED")
            continue
        ddl.tables.append(table)
    if not ddl.tables:
        ddl.codes.append("E_DDL_UNPARSED")
    return ddl


def declared_reference_count(ddl: Ddl) -> int:
    return sum(len(table.foreign_keys) for table in ddl.tables)
