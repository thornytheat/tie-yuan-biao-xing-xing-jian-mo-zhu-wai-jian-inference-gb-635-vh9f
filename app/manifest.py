"""运行清单：run_id、输入文件摘要、规则版本、参数、耗时，可复算。

run_id = 前缀 + UTC 时间戳 + 4 位随机尾，人读短、机读唯一；
batch_key 只由输入摘要与规则版本决定，同一输入必然算出同一个键。
"""
from __future__ import annotations

import datetime as dt
import hashlib
import uuid

from . import config


def new_run_id(prefix: str = "SM") -> str:
    stamp = dt.datetime.now(config.UTC).strftime("%Y%m%d%H%M%S")
    return f"{prefix}-{stamp}-{uuid.uuid4().hex[:4]}"


def digest_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def digest_parts(parts: list[str]) -> str:
    h = hashlib.sha256()
    for part in parts:
        h.update(part.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


def batch_key(*, rule_version: str, inputs: list[dict], params: dict) -> str:
    """同一批输入 + 同一规则版本 + 同一参数 → 同一批次键。"""
    parts = [rule_version]
    for item in sorted(inputs, key=lambda i: i["name"]):
        parts.append(f"{item['name']}:{item['sha256']}")
    for key in sorted(params):
        parts.append(f"{key}={params[key]}")
    return digest_parts(parts)[:32]


def build(*, run_id: str, batch_id: str, batch_key_value: str, settings,
          inputs: list[dict], params: dict, started_at: str, finished_at: str,
          elapsed_ms: int, stages: list[dict], counters: dict,
          input_digest: str) -> dict:
    return {
        "run_id": run_id,
        "batch_id": batch_id,
        "batch_key": batch_key_value,
        "rule_version": settings.rule_version,
        "input_digest": input_digest,
        "inputs": [
            {"name": i["name"], "role": i["role"], "encoding": i["encoding"],
             "bytes": i["bytes"], "sha256": i["sha256"], "rows": i.get("rows", 0)}
            for i in inputs
        ],
        "params": params,
        "started_at": started_at,
        "finished_at": finished_at,
        "elapsed_ms": elapsed_ms,
        "stages": stages,
        "counters": counters,
        "decimals": "金额与用量为 Decimal，最小刻度 0.01",
        "replayable": "同 batch_key 重跑返回同一批次",
    }
