"""M2 编排：表头映射 → 逐列归一 → 样本类型推断 → 行级空值判定。

产物是「已归一但未统计」的列运行对象，统计与处置留给 M3，键判定留给 M4。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from ... import config, reasons
from ..m1_ingest import sniff
from . import coerce, evolve


@dataclass
class ColumnRun:
    table_name: str
    name: str
    ordinal: int
    ddl_type: str
    base_type: str
    width: int
    precision: int
    scale: int
    nullable: bool
    comment: str
    kind: str
    pad_width: int = 0
    sample_rows: int = 0
    empty_count: int = 0
    sentinel_count: int = 0
    unparsable: int = 0
    present: bool = True
    values: list = field(default_factory=list)
    raw_values: list = field(default_factory=list)
    codes: list = field(default_factory=list)
    widened_from: str = ""
    padded_examples: list = field(default_factory=list)

    @property
    def null_total(self) -> int:
        return self.empty_count + self.sentinel_count

    @property
    def sample_value(self) -> str:
        return self.raw_values[0] if self.raw_values else ""


@dataclass
class TableRun:
    name: str
    ordinal: int
    declared_pk: list
    foreign_keys: list
    header_map: object
    columns: list = field(default_factory=list)
    row_total: int = 0
    valid_rows: list = field(default_factory=list)
    valid_lines: list = field(default_factory=list)
    rows_norm: list = field(default_factory=list)
    null_row_count: int = 0
    quarantined_count: int = 0
    case_conflicts: list = field(default_factory=list)
    column_widenings: list = field(default_factory=list)

    def column(self, name: str) -> ColumnRun | None:
        lowered = name.lower()
        for column in self.columns:
            if column.name.lower() == lowered:
                return column
        return None

    @property
    def column_names(self) -> list:
        return [column.name for column in self.columns]

    def values_of(self, name: str) -> list:
        column = self.column(name)
        return [] if column is None else list(column.values)


@dataclass
class Coerced:
    tables: list = field(default_factory=list)
    quarantine: list = field(default_factory=list)
    codes: list = field(default_factory=list)

    def table(self, name: str) -> TableRun | None:
        lowered = name.lower()
        for table in self.tables:
            if table.name.lower() == lowered:
                return table
        return None

    def column(self, table_name: str, column_name: str) -> ColumnRun | None:
        table = self.table(table_name)
        return None if table is None else table.column(column_name)


def _q(code, *, table=None, column=None, row_ref=None, raw_value=None, detail=None):
    return {"code": code, "stage": reasons.stage_of(code), "level": reasons.level_of(code),
            "table_name": table, "column_name": column, "row_ref": row_ref,
            "raw_value": raw_value, "detail": detail}


def _normalize(kind: str, value: str, width: int,
               column_name: str = "") -> tuple[str | None, list]:
    codes: list[str] = []
    if kind == "code":
        text, code = coerce.normalize_code(value, width)
        if code:
            codes.append(code)
        return text, codes
    if kind in ("int", "bigint"):
        number, code = coerce.parse_decimal(value)
        if number is None:
            return None, [code or "Q_BAD_NUMBER"]
        return (str(int(number)) if kind == "int" else str(number)), codes
    if kind == "decimal":
        number, code = coerce.parse_decimal(value)
        if number is None:
            return None, [code or "Q_BAD_NUMBER"]
        return str(number.quantize(config.DECIMAL_QUANT)), codes
    if kind == "date":
        parsed, code = coerce.parse_date(value)
        if parsed is None:
            return None, [code or "Q_BAD_DATE"]
        return parsed.isoformat(), codes
    if kind == "timestamp":
        parsed, code = coerce.parse_timestamp(value)
        if parsed is None:
            return None, [code or "Q_BAD_DATE"]
        return parsed.strftime("%Y-%m-%d %H:%M:%S"), codes
    canonical, merged = coerce.alias_of(value, column_name)
    if merged:
        codes.append("K_ALIAS_MERGED")
    return canonical, codes


def _widen_kind(declared: str, kind: str) -> str:
    """声明类型与样本类型不一致时给出收敛后的类型说明（仅用于证据）。"""
    if declared in ("numeric", "decimal") and kind in ("int", "decimal"):
        return ""
    if declared in ("int", "bigint") and kind == "decimal":
        return f"{declared} → decimal"
    if declared in ("varchar", "char", "nvarchar", "text") and kind in ("date", "timestamp"):
        return f"{declared} → {kind}"
    if declared in ("varchar", "char") and kind == "code":
        return ""
    return ""


def build(intake, settings=None) -> Coerced:
    result = Coerced()
    convergences = evolve.converge_types(intake.ddl)
    for table_def in intake.ddl.tables:
        header_map = evolve.map_headers(table_def, intake.header(table_def.name))
        table = TableRun(
            name=table_def.name,
            ordinal=table_def.ordinal,
            declared_pk=list(table_def.primary_key),
            foreign_keys=list(table_def.foreign_keys),
            header_map=header_map,
            case_conflicts=table_def.case_conflicts,
            row_total=_row_total(intake, table_def.name),
        )
        for rename in header_map.renames:
            result.quarantine.append(_q(
                "E_COLUMN_RENAMED", table=table_def.name, column=rename[1],
                raw_value=rename[0], detail=f"样本列名 {rename[0]} 映射为 {rename[1]}"))
            result.codes.append("E_COLUMN_RENAMED")
        for name in header_map.unmatched:
            result.quarantine.append(_q(
                "Q_SECTION_UNKNOWN", table=table_def.name, column=name,
                detail="样本列在 DDL 里没有对应列，已忽略"))
            result.codes.append("Q_SECTION_UNKNOWN")
        for group in table.case_conflicts:
            result.quarantine.append(_q(
                "Q_COLUMN_CASE_CONFLICT", table=table_def.name, column="、".join(group),
                detail=f"同表内 {len(group)} 个列名仅大小写不同"))
            result.codes.append("Q_COLUMN_CASE_CONFLICT")

        rows = intake.rows(table_def.name)
        valid_cells: list[list[str]] = []
        valid_lines: list[int] = []
        for row in rows:
            if sniff.is_blank_row(row.cells):
                table.null_row_count += 1
                continue
            valid_cells.append(list(row.cells))
            valid_lines.append(row.line_no)
        table.valid_lines = valid_lines
        section = intake.sections.get(table_def.name.lower())
        table.quarantined_count = (len(section.short_rows) + len(section.long_rows)
                                   if section else 0)
        sample_rows = len(valid_cells)

        for column_def in table_def.columns:
            index = header_map.index_of(column_def.name)
            present = index >= 0 and index < len(header_map.mapped)
            column = ColumnRun(
                table_name=table_def.name, name=column_def.name, ordinal=column_def.ordinal,
                ddl_type=_ddl_type(column_def), base_type=column_def.base_type,
                width=column_def.width, precision=column_def.precision,
                scale=column_def.scale, nullable=column_def.nullable,
                comment=column_def.comment, kind=column_def.base_type or "text",
                sample_rows=sample_rows, present=present)
            if not present:
                column.empty_count = sample_rows
                column.kind = "text"
                table.columns.append(column)
                continue
            raw_values = []
            for cells in valid_cells:
                cell = cells[index] if index < len(cells) else ""
                state = coerce.classify_raw(cell)
                if state == "empty":
                    column.empty_count += 1
                elif state == "sentinel":
                    column.sentinel_count += 1
                    column.codes.append("Q_SENTINEL_VALUE")
                else:
                    raw_values.append(cell.strip())
            kind, kind_codes = coerce.infer_kind(raw_values, column_def.base_type)
            column.kind = kind
            column.pad_width = (coerce.pad_width(raw_values) if kind == "code"
                                else column_def.width)
            column.codes.extend(kind_codes)
            widened = _widen_kind(column_def.base_type, kind)
            if widened:
                column.codes.append("E_TYPE_WIDENED")
                column.widened_from = widened
                result.codes.append("E_TYPE_WIDENED")
            for cells in valid_cells:
                cell = cells[index] if index < len(cells) else ""
                if coerce.classify_raw(cell) != "value":
                    continue
                column.raw_values.append(cell.strip())
                normalized, codes = _normalize(kind, cell, column.pad_width,
                                               column_def.name)
                if normalized is None:
                    column.unparsable += 1
                    column.codes.extend(codes)
                    result.quarantine.append(_q(
                        codes[0] if codes else "Q_BAD_NUMBER", table=table_def.name,
                        column=column_def.name, raw_value=cell.strip(),
                        detail="归一失败，该格按不可解析计"))
                    continue
                if "K_ALIAS_MERGED" in codes and "K_ALIAS_MERGED" not in column.codes:
                    column.codes.append("K_ALIAS_MERGED")
                if kind == "code":
                    if "Q_CODE_PADDED" in codes:
                        column.codes.append("Q_CODE_PADDED")
                        if len(column.padded_examples) < 3:
                            column.padded_examples.append(
                                {"raw": cell.strip(), "padded": normalized})
                    else:
                        column.codes.append("Q_CODE_PRESERVED")
                column.values.append(normalized)
            convergence = convergences.get(column_def.name.lower())
            if convergence and table_def.name in convergence["narrow_tables"]:
                column.codes.append("E_TYPE_WIDENED")
                table.column_widenings.append({
                    "column": column_def.name,
                    "declared": column_def.width,
                    "widest": convergence["widest"],
                    "code": "E_TYPE_WIDENED",
                })
                result.codes.append("E_TYPE_WIDENED")
            for code in sorted(set(column.codes)):
                if code in ("Q_CODE_PADDED", "Q_BIGINT_TEXT", "Q_CODE_PRESERVED"):
                    result.codes.append(code)
            table.columns.append(column)

        for column in table.columns:
            for code in sorted({c for c in column.codes if c in
                                ("Q_CODE_PADDED", "Q_BIGINT_TEXT")}):
                if code == "Q_CODE_PADDED" and column.padded_examples:
                    first = column.padded_examples[0]
                    result.quarantine.append(_q(
                        "Q_CODE_PADDED", table=table.name, column=column.name,
                        raw_value=first["raw"],
                        detail=f"{first['raw']} → {first['padded']}"
                               f"（列内宽度 {column.pad_width}）"))
                elif code == "Q_BIGINT_TEXT":
                    result.quarantine.append(_q(
                        "Q_BIGINT_TEXT", table=table.name, column=column.name,
                        raw_value=column.sample_value,
                        detail=f"按字符串保存，阈值 2^53 = {2 ** 53}"))
        table.valid_rows = valid_cells
        table.rows_norm = _normalized_rows(table, header_map, valid_cells)
        result.tables.append(table)
    result.codes = sorted(set(result.codes))
    return result


def _row_total(intake, table_name: str) -> int:
    """行账分母：完整行 + 列数不齐行 + 空值行，隔离开的也要算进来。"""
    section = intake.sections.get(table_name.lower())
    if section is None:
        return 0
    return len(section.rows) + len(section.short_rows) + len(section.long_rows)


def _normalized_rows(table, header_map, valid_cells) -> list:
    """对齐后的归一取值矩阵：空值、哨兵、不可解析一律 None。"""
    rows = []
    for cells in valid_cells:
        row = {}
        for column in table.columns:
            index = header_map.index_of(column.name)
            if index < 0 or index >= len(cells):
                row[column.name] = None
                continue
            cell = cells[index]
            if coerce.classify_raw(cell) != "value":
                row[column.name] = None
                continue
            value, _codes = _normalize(column.kind, cell, column.pad_width, column.name)
            row[column.name] = value
        rows.append(row)
    return rows


def _ddl_type(column_def) -> str:
    if column_def.base_type in ("varchar", "char", "nvarchar") and column_def.width:
        return f"{column_def.base_type}({column_def.width})"
    if column_def.base_type in ("numeric", "decimal") and column_def.precision:
        return f"{column_def.base_type}({column_def.precision},{column_def.scale})"
    return column_def.base_type


def decimals_of(column: ColumnRun) -> list[Decimal]:
    out = []
    for value in column.values:
        try:
            out.append(Decimal(value))
        except Exception:
            continue
    return out
