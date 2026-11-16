"""样本分段嗅探：分段标记、表头、重复表头、分隔符、列数不齐与末行截断。

样本文件是「Excel 逐表另存再拼起来」的产物，所以：
- 每段以 ## table=表名 开头，段内第一行是表头；
- 分段导出会重复写一次表头，重复表头不能当数据；
- 末段被截断时最后一行没有行尾换行，列数也少。
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field

SECTION_RE = re.compile(r"^##\s*table\s*=\s*([A-Za-z0-9_]+)\s*$")
COMMENT_PREFIX = "#"


@dataclass
class SampleRow:
    line_no: int
    cells: list


@dataclass
class Section:
    name: str
    header_line: int
    header: list = field(default_factory=list)
    rows: list = field(default_factory=list)
    dup_headers: int = 0
    short_rows: list = field(default_factory=list)
    long_rows: list = field(default_factory=list)
    delimiter: str = ","

    @property
    def data_rows(self) -> int:
        return len(self.rows)


class _LineFeed:
    """把带行号的行喂给 csv.reader，同时记住每条记录起始物理行号。"""

    def __init__(self, lines: list[tuple[int, str]]):
        self.lines = lines
        self.index = 0

    def __iter__(self):
        return self

    def __next__(self) -> str:
        if self.index >= len(self.lines):
            raise StopIteration
        text = self.lines[self.index][1]
        self.index += 1
        return text


def sniff_delimiter(header_line: str) -> str:
    """逗号和制表符谁多听谁的；样本里逗号出现在引号内也算，故先粗略比较。"""
    commas = header_line.count(",")
    tabs = header_line.count("\t")
    return "\t" if tabs > commas else ","


def parse_records(lines: list[tuple[int, str]], delimiter: str,
                  codes: list | None = None) -> list[tuple[int, list]]:
    feed = _LineFeed(lines)
    reader = csv.reader(feed, delimiter=delimiter, skipinitialspace=False)
    out = []
    while True:
        start = feed.index
        try:
            record = next(reader)
        except StopIteration:
            break
        except csv.Error:
            break
        line_no = lines[start][0] if start < len(lines) else lines[-1][0]
        if codes is not None and any(cell.startswith("\ufeff") for cell in record):
            if "E_BOM_STRIPPED" not in codes:
                codes.append("E_BOM_STRIPPED")
        out.append((line_no, [cell.strip().lstrip("\ufeff") for cell in record]))
    return out


def split_sections(text: str, codes: list | None = None) -> list[Section]:
    """按 # 分段，返回每段的表头与数据行，并记录重复表头与列数异常。"""
    codes = codes if codes is not None else []
    lines = text.splitlines()
    buckets: list[tuple[str, int, list[tuple[int, str]]]] = []
    current = None
    for index, raw in enumerate(lines, 1):
        marker = SECTION_RE.match(raw.strip())
        if marker:
            current = (marker.group(1), index, [])
            buckets.append(current)
            continue
        if current is None:
            if raw.strip() and not raw.strip().startswith(COMMENT_PREFIX):
                codes.append("Q_ROW_TRUNCATED")
            continue
        current[2].append((index, raw))

    sections: list[Section] = []
    for name, marker_line, body in buckets:
        if not body:
            sections.append(Section(name=name, header_line=marker_line))
            continue
        delimiter = sniff_delimiter(body[0][1])
        records = parse_records(body, delimiter, codes)
        if not records:
            sections.append(Section(name=name, header_line=marker_line, delimiter=delimiter))
            continue
        header_line, header = records[0]
        section = Section(name=name, header_line=header_line, header=header,
                          delimiter=delimiter)
        for line_no, cells in records[1:]:
            if cells == header:
                section.dup_headers += 1
                continue
            if len(cells) < len(header):
                section.short_rows.append(line_no)
                continue
            if len(cells) > len(header):
                section.long_rows.append(line_no)
                continue
            section.rows.append(SampleRow(line_no=line_no, cells=cells))
        sections.append(section)
    return sections


def section_bounds(text: str) -> int:
    """分段数：用于证据里说明「这份样本拼了几段」。"""
    return sum(1 for raw in text.splitlines() if SECTION_RE.match(raw.strip()))


def is_blank_row(cells: list) -> bool:
    return all((cell or "").strip() == "" for cell in cells)


def read_text_lines(raw: bytes, encoding: str) -> str:
    return io.TextIOWrapper(io.BytesIO(raw), encoding=encoding, newline="").read()
