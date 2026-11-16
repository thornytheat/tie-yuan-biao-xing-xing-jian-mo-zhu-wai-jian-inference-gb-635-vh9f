"""编码探测与回退链：先信声明，再按固定顺序逐个试，全程保留命中编码与原因码。

真实导出件最常见的问题是声明 utf-8 而字节是 GB18030（门店系统导出），
直接 open() 会把中文列名读成乱码，后续所有按列名匹配都落空。
"""
from __future__ import annotations

from typing import NamedTuple

from ... import config


class Decoded(NamedTuple):
    text: str
    encoding: str
    declared: str
    bom_stripped: bool
    codes: list


def declared_encoding(raw: bytes, content_type: str = "") -> str:
    """声明编码：BOM 优先，其次 content-type，最后按 UTF-8。"""
    if raw.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    lowered = (content_type or "").lower()
    for alias, canonical in config.CHARSET_ALIASES.items():
        if alias in lowered:
            return canonical
    return "utf-8"


def decode_bytes(raw: bytes, content_type: str = "") -> Decoded:
    declared = declared_encoding(raw, content_type)
    chain = []
    for name in (declared,) + config.ENCODING_CHAIN:
        if name not in chain:
            chain.append(name)
    codes: list[str] = []
    for name in chain:
        try:
            text = raw.decode(name)
        except (UnicodeDecodeError, LookupError):
            continue
        # utf-8-sig 解码时自己会吃掉 BOM，所以字节层也要看一次
        bom_stripped = raw.startswith(b"\xef\xbb\xbf") or text.startswith("\ufeff")
        if text.startswith("\ufeff"):
            text = text.lstrip("\ufeff")
        if bom_stripped:
            codes.append("E_BOM_STRIPPED")
        if name != declared:
            codes.append("E_CHARSET_FALLBACK")
        return Decoded(text, name, declared, bom_stripped, codes)
    return Decoded("", "unknown", declared, False, ["E_DECODE_FAILED"])


def canonical_charset(name: str) -> str:
    return config.CHARSET_ALIASES.get((name or "").lower(), (name or "").lower())
