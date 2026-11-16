"""M1 编排：读 DDL 与样本 → 分段 → 表与列清单 → 采信级诊断。

本模块只回答「文件长什么样、哪一行不能用」，不做类型、不做键判定。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from ... import reasons
from . import ddl as ddl_mod
from . import reader, sniff


@dataclass
class Intake:
    ddl: ddl_mod.Ddl
    inputs: list = field(default_factory=list)
    sections: dict = field(default_factory=dict)
    quarantine: list = field(default_factory=list)
    codes: list = field(default_factory=list)
    section_count: int = 0

    def rows(self, table_name: str) -> list:
        section = self.sections.get(table_name.lower())
        return section.rows if section else []

    def header(self, table_name: str) -> list:
        section = self.sections.get(table_name.lower())
        return section.header if section else []

    def table_names(self) -> list:
        return [table.name for table in self.ddl.tables]

    @property
    def digest_parts(self) -> list:
        return [f"{item['name']}:{item['sha256']}" for item in self.inputs]


def _q(code: str, *, table=None, column=None, row_ref=None, raw_value=None, detail=None):
    return {"code": code, "stage": reasons.stage_of(code), "level": reasons.level_of(code),
            "table_name": table, "column_name": column, "row_ref": row_ref,
            "raw_value": raw_value, "detail": detail}


def intake(settings) -> Intake:
    ddl_input, dumps = reader.load_inputs(settings)
    ddl = ddl_mod.parse_ddl(ddl_input.text)
    result = Intake(ddl=ddl)
    result.inputs.append(ddl_input.manifest_item())

    for file_codes in (ddl_input.codes,):
        for code in file_codes:
            result.quarantine.append(_q(code, detail=ddl_input.name))
            result.codes.append(code)
    for code in ddl.codes:
        result.quarantine.append(_q(code, detail=ddl_input.name))
        result.codes.append(code)

    sections: dict = {}
    for dump in dumps:
        result.inputs.append(dump.manifest_item())
        for code in dump.codes:
            result.quarantine.append(_q(code, detail=dump.name))
            result.codes.append(code)
        section_codes: list[str] = []
        parsed = sniff.split_sections(dump.text, section_codes)
        for code in section_codes:
            if code not in dump.codes:
                result.quarantine.append(_q(code, detail=dump.name))
                result.codes.append(code)
        result.section_count += len(parsed)
        for section in parsed:
            key = section.name.lower()
            if section.dup_headers:
                result.quarantine.append(_q(
                    "Q_DUP_HEADER", table=section.name,
                    row_ref=f"{dump.name}#{section.header_line}",
                    detail=f"重复表头 {section.dup_headers} 行已剔除"))
                result.codes.append("Q_DUP_HEADER")
            for line_no in section.short_rows:
                result.quarantine.append(_q(
                    "Q_ROW_TRUNCATED", table=section.name,
                    row_ref=f"{dump.name}:{line_no}",
                    detail=f"列数 {len(section.header)} 期望，实际更少"))
                result.codes.append("Q_ROW_TRUNCATED")
            for line_no in section.long_rows:
                result.quarantine.append(_q(
                    "Q_COLUMN_COUNT", table=section.name,
                    row_ref=f"{dump.name}:{line_no}",
                    detail="行尾多出分隔符"))
                result.codes.append("Q_COLUMN_COUNT")
            if key in sections:
                sections[key].rows.extend(section.rows)
                continue
            sections[key] = section

    for key, section in sections.items():
        if ddl.table(key) is None:
            result.quarantine.append(_q(
                "Q_SECTION_UNKNOWN", table=section.name,
                row_ref=f"line {section.header_line}",
                detail="DDL 里没有同名表"))
            result.codes.append("Q_SECTION_UNKNOWN")

    for table in ddl.tables:
        section = sections.get(table.name.lower())
        if section is None or not section.rows:
            result.quarantine.append(_q(
                "Q_TABLE_NO_SAMPLE", table=table.name,
                detail=f"列数 {len(table.columns)}，样本行数 0"))
            result.codes.append("Q_TABLE_NO_SAMPLE")

    result.sections = sections
    return result


def summary(settings) -> dict:
    """给导入页用：不落库也能看到每张表采到什么。"""
    data = intake(settings)
    tables = []
    for table in data.ddl.tables:
        section = data.sections.get(table.name.lower())
        tables.append({
            "table_name": table.name,
            "column_count": len(table.columns),
            "sample_rows": len(section.rows) if section else 0,
            "header_line": section.header_line if section else None,
            "declared_pk": list(table.primary_key),
            "declared_refs": len(table.foreign_keys),
            "case_conflicts": table.case_conflicts,
        })
    return {
        "inputs": data.inputs,
        "section_count": data.section_count,
        "tables": tables,
        "codes": sorted(set(data.codes)),
        "quarantine_total": len(data.quarantine),
        "input_digest": os.environ.get("STARMODEL_INPUT_DIGEST", ""),
    }
