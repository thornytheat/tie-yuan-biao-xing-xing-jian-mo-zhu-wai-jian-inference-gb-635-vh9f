"""取值归一：全角转半角、千分位、空值哨兵、编码保形、日期与 Decimal。

归一函数都返回 (值, 原因码)，调用方决定是留痕还是隔离；这里不抛异常，
因为真实导出件里「一行坏值」不能让整份样本失败。
"""
from __future__ import annotations

import datetime as dt
import re
from decimal import Decimal, InvalidOperation

from ... import config

FULLWIDTH_BASE = 0xFEE0
DATE_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d", "%Y.%m.%d")
TS_FORMATS = (
    "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M",
)
THOUSANDS_RE = re.compile(r"^[+-]?\d{1,3}(,\d{3})+(\.\d+)?$")
PLAIN_NUMBER_RE = re.compile(r"^[+-]?\d+(\.\d+)?$")
BIG_INT = 2 ** 53


def to_halfwidth(text: str) -> str:
    """全角数字、字母、小数点、正负号转半角；其余字符原样。"""
    out = []
    for ch in text:
        code = ord(ch)
        if code == 0x3000:
            out.append(" ")
        elif 0xFF10 <= code <= 0xFF19 or 0xFF21 <= code <= 0xFF3A or 0xFF41 <= code <= 0xFF5A:
            out.append(chr(code - FULLWIDTH_BASE))
        elif code == 0xFF0E:
            out.append(".")
        elif code == 0xFF0C:
            out.append(",")
        elif code == 0xFF0D:
            out.append("-")
        else:
            out.append(ch)
    return "".join(out)


def is_blank(value: str | None) -> bool:
    return value is None or value.strip() == ""


def is_sentinel(value: str | None) -> bool:
    return not is_blank(value) and value.strip().lower() in config.SENTINELS


def classify_raw(value: str | None) -> str:
    """空值三态第一层：empty（真空值）/ sentinel（哨兵）/ value（有值）。"""
    if is_blank(value):
        return "empty"
    if is_sentinel(value):
        return "sentinel"
    return "value"


def pad_width(values: list) -> int:
    """列内左补零的宽度：该列最长纯数字取值的长度，与行序无关。"""
    widths = [len(value) for value in values if value and value.isdigit()]
    return max(widths) if widths else 0


def normalize_code(value: str, width: int) -> tuple[str, str | None]:
    """编码列按字符串保形；纯数字且短于列内最长宽度时左补零。"""
    text = to_halfwidth((value or "").strip())
    if width > 0 and text.isdigit() and len(text) < width:
        return text.zfill(width), "Q_CODE_PADDED"
    return text, "Q_CODE_PRESERVED"


def parse_decimal(value: str) -> tuple[Decimal | None, str | None]:
    text = to_halfwidth((value or "").strip())
    if not text:
        return None, None
    if THOUSANDS_RE.match(text):
        text = text.replace(",", "")
    if not PLAIN_NUMBER_RE.match(text):
        return None, "Q_BAD_NUMBER"
    try:
        return Decimal(text), None
    except InvalidOperation:
        return None, "Q_BAD_NUMBER"


def _date_only(text: str) -> dt.date | None:
    for fmt in DATE_FORMATS:
        try:
            return dt.datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def parse_date(value: str) -> tuple[dt.date | None, str | None]:
    text = to_halfwidth((value or "").strip())
    if not text:
        return None, None
    head = text.split(" ")[0].split("T")[0]
    parsed = _date_only(head)
    if parsed is None:
        return None, "Q_BAD_DATE"
    return parsed, None


def parse_timestamp(value: str) -> tuple[dt.datetime | None, str | None]:
    text = to_halfwidth((value or "").strip()).replace("T", " ")
    if not text:
        return None, None
    for fmt in TS_FORMATS:
        try:
            return dt.datetime.strptime(text, fmt), None
        except ValueError:
            continue
    parsed = _date_only(text.split(" ")[0])
    if parsed is not None:
        return dt.datetime(parsed.year, parsed.month, parsed.day), None
    return None, "Q_BAD_DATE"


def alias_of(value: str, column_name: str) -> tuple[str, bool]:
    """同一主体的多个写法合并到代表元；只看登记过的列，其余原样返回。"""
    if column_name.lower() not in config.ALIAS_COLUMNS:
        return value, False
    stripped = (value or "").strip()
    for canonical, variants in config.ALIAS_GROUPS.items():
        if stripped == canonical or stripped in variants:
            return canonical, stripped != canonical
        if stripped.lower() == canonical.lower():
            return canonical, stripped != canonical
    return stripped, False


def is_big_integer(values: list[str]) -> bool:
    for value in values:
        try:
            if abs(int(value)) > BIG_INT:
                return True
        except (TypeError, ValueError):
            return False
    return False


def _all_parse(values: list[str], parser) -> bool:
    if not values:
        return False
    for value in values:
        parsed, _ = parser(value)
        if parsed is None:
            return False
    return True


def infer_kind(values: list[str], base_type: str) -> tuple[str, list[str]]:
    """样本类型推断：编码列优先保形，其次整数/大整数/小数，再看日期与时间。

    values 已去掉空值与哨兵；base_type 是 DDL 声明，只用于判断「声明为字符型」。
    """
    if not values:
        return "text", []
    declare_char = base_type in ("varchar", "char", "nvarchar", "text")

    if _all_parse(values, parse_date) and any(
            len(value) == 8 or "/" in value or "-" in value or "." in value
            for value in values):
        if _all_parse(values, parse_timestamp) and any(
                " " in value or "T" in value for value in values):
            return "timestamp", []
        return "date", []

    all_digits = all(value.isdigit() for value in values)
    if all_digits:
        leading_zero = any(len(value) > 1 and value.startswith("0") for value in values)
        long_digits = all(len(value) >= 8 for value in values)
        if leading_zero or (declare_char and long_digits):
            return "code", ["Q_CODE_PRESERVED"]
        if is_big_integer(values):
            return "bigint", ["Q_BIGINT_TEXT"]
        return "int", []

    numbers = [parse_decimal(value) for value in values]
    if all(item[0] is not None for item in numbers):
        integral = all(item[0] == item[0].to_integral_value() for item in numbers)
        return ("int" if integral else "decimal"), []
    return "text", []


def kind_label(kind: str) -> str:
    return {
        "code": "编码", "int": "整数", "bigint": "大整数", "decimal": "数值",
        "date": "日期", "timestamp": "时间戳", "text": "文本", "bool": "布尔",
    }.get(kind, kind)


def measurement_of(value: Decimal | None) -> str:
    if value is None:
        return ""
    return f"{value.quantize(config.DECIMAL_QUANT)}"
