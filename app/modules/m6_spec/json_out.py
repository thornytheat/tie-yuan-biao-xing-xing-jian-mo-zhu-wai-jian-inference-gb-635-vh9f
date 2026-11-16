"""JSON 安全序列化：Decimal 转字符串，超过 2^53 的整数保持字符串。

JSON 数值走双精度，19 位流水号往返会丢掉末位；所以编码列与大整数一律按
字符串输出，导出的值与样本逐字相同。
"""
from __future__ import annotations

import json
from decimal import Decimal

BIG_INT = 2 ** 53


def safe(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, int):
        return str(value) if abs(value) > BIG_INT else value
    if isinstance(value, dict):
        return {str(key): safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [safe(item) for item in value]
    return value


def dumps(payload, *, indent: int | None = None) -> str:
    return json.dumps(safe(payload), ensure_ascii=False, indent=indent)


def encode(text: str) -> bytes:
    return text.encode("utf-8")
