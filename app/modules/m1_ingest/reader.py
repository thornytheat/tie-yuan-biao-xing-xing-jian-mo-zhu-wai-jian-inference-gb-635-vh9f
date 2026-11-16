"""输入读取：把 DDL 与样本文件的字节、摘要、命中编码整理成清单项。

只读不解析：解析在 sniff.py / ddl.py，二者都拿纯文本，便于单测。
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from ... import errors
from . import decode


@dataclass
class RawInput:
    name: str
    role: str
    raw: bytes
    text: str
    encoding: str
    declared: str
    codes: list

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.raw).hexdigest()

    @property
    def size(self) -> int:
        return len(self.raw)

    def manifest_item(self) -> dict:
        return {"name": self.name, "role": self.role, "encoding": self.encoding,
                "bytes": self.size, "sha256": self.sha256, "codes": list(self.codes)}


def load_file(path: Path, role: str) -> RawInput:
    path = Path(path)
    if not path.is_file():
        raise errors.NotFound(f"输入文件不存在：{path.name}", code="E_INPUT_MISSING")
    raw = path.read_bytes()
    decoded = decode.decode_bytes(raw)
    if "E_DECODE_FAILED" in decoded.codes:
        raise errors.BadRequest(f"{path.name} 在回退链内无法解码", code="E_DECODE_FAILED",
                                detail={"file": path.name})
    return RawInput(name=path.name, role=role, raw=raw, text=decoded.text,
                    encoding=decoded.encoding, declared=decoded.declared,
                    codes=list(decoded.codes))


def load_inputs(settings) -> tuple[RawInput, list[RawInput]]:
    ddl = load_file(settings.ddl_path, "ddl")
    dumps = [load_file(path, "sample") for path in settings.dump_paths() if Path(path).is_file()]
    if not dumps:
        raise errors.NotFound("sample_data 里没有样本文件", code="E_INPUT_MISSING")
    return ddl, dumps
