"""SQLite 存储：批次、表元数据、列剖析、键、星型建议、隔离、冲突、阶段、合计、清单。

约定：
- 比率一律 TEXT（Decimal 字符串），不落 float；
- 一次运行的所有行在同一个事务里「先删后插」，失败整体回滚，不留半批；
- 读数按 batch_id 过滤，页面只展示一个批次。
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS batches (
    batch_id TEXT PRIMARY KEY,
    batch_key TEXT NOT NULL,
    run_id TEXT NOT NULL,
    source TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    finished_at TEXT,
    input_digest TEXT NOT NULL,
    resume_of TEXT,
    params_json TEXT NOT NULL DEFAULT '{}',
    note TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_batches_key ON batches(batch_key, status);
CREATE TABLE IF NOT EXISTS tables_meta (
    batch_id TEXT NOT NULL, table_name TEXT NOT NULL, ordinal INTEGER NOT NULL,
    engine TEXT NOT NULL DEFAULT 'ddl', sample_rows INTEGER NOT NULL DEFAULT 0,
    column_count INTEGER NOT NULL DEFAULT 0,
    declared_pk_json TEXT NOT NULL DEFAULT '[]',
    role TEXT NOT NULL DEFAULT 'pending',
    role_reason TEXT NOT NULL DEFAULT '',
    scd TEXT NOT NULL DEFAULT '',
    scd_reason TEXT NOT NULL DEFAULT '',
    metric_count INTEGER NOT NULL DEFAULT 0,
    fk_count INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS columns_meta (
    batch_id TEXT NOT NULL, table_name TEXT NOT NULL, column_name TEXT NOT NULL,
    ordinal INTEGER NOT NULL, ddl_type TEXT NOT NULL, base_type TEXT NOT NULL,
    width INTEGER NOT NULL DEFAULT 0, nullable INTEGER NOT NULL DEFAULT 1,
    comment TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL DEFAULT 'text',
    sample_rows INTEGER NOT NULL DEFAULT 0, null_total INTEGER NOT NULL DEFAULT 0,
    empty_count INTEGER NOT NULL DEFAULT 0, sentinel_count INTEGER NOT NULL DEFAULT 0,
    distinct_count INTEGER NOT NULL DEFAULT 0, unparsable INTEGER NOT NULL DEFAULT 0,
    null_rate TEXT NOT NULL DEFAULT '0', unique_rate TEXT NOT NULL DEFAULT '0',
    measurement TEXT NOT NULL DEFAULT '',
    metric_total TEXT,
    disposition TEXT NOT NULL DEFAULT 'adopted',
    disposition_order INTEGER NOT NULL DEFAULT 0,
    reason_codes_json TEXT NOT NULL DEFAULT '[]',
    evidence_json TEXT NOT NULL DEFAULT '{}',
    sample_value TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS key_findings (
    batch_id TEXT NOT NULL, kind TEXT NOT NULL, child_table TEXT NOT NULL,
    child_columns_json TEXT NOT NULL DEFAULT '[]', parent_table TEXT,
    parent_columns_json TEXT NOT NULL DEFAULT '[]',
    declared INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL,
    inclusion TEXT, cardinality_ratio TEXT, reason_code TEXT NOT NULL DEFAULT '',
    evidence_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS star_objects (
    batch_id TEXT NOT NULL, table_name TEXT NOT NULL, role TEXT NOT NULL,
    scd TEXT NOT NULL DEFAULT '', reason_code TEXT NOT NULL DEFAULT '',
    metric_columns_json TEXT NOT NULL DEFAULT '[]',
    fk_count INTEGER NOT NULL DEFAULT 0,
    degenerate_json TEXT NOT NULL DEFAULT '[]',
    evidence_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS quarantine (
    batch_id TEXT NOT NULL, code TEXT NOT NULL, stage TEXT NOT NULL, level TEXT NOT NULL,
    table_name TEXT, column_name TEXT, row_ref TEXT, raw_value TEXT, detail TEXT
);
CREATE TABLE IF NOT EXISTS conflicts (
    batch_id TEXT NOT NULL, table_name TEXT NOT NULL, key_columns_json TEXT NOT NULL,
    key_value TEXT NOT NULL, kept_json TEXT NOT NULL DEFAULT '{}',
    dropped_json TEXT NOT NULL DEFAULT '{}', note TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS row_ledger (
    batch_id TEXT NOT NULL, table_name TEXT NOT NULL, total INTEGER NOT NULL,
    valid INTEGER NOT NULL, null_row INTEGER NOT NULL, quarantined INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS stages (
    batch_id TEXT NOT NULL, stage TEXT NOT NULL, in_count INTEGER NOT NULL,
    out_count INTEGER NOT NULL, quarantine_count INTEGER NOT NULL,
    status TEXT NOT NULL, elapsed_ms INTEGER NOT NULL, note TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS totals (
    batch_id TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS manifests (
    batch_id TEXT PRIMARY KEY, json TEXT NOT NULL
);
"""


class Store:
    def __init__(self, path: str):
        self.path = str(path)
        Path(self.path).resolve().parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path, timeout=15.0)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ---------- 批次 ----------
    def upsert_batch(self, batch: dict) -> None:
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO batches (batch_id, batch_key, run_id, source, status,
                        created_at, finished_at, input_digest, resume_of, params_json, note)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(batch_id) DO UPDATE SET
                     status=excluded.status, finished_at=excluded.finished_at,
                     input_digest=excluded.input_digest, note=excluded.note""",
                (batch["batch_id"], batch["batch_key"], batch["run_id"], batch["source"],
                 batch["status"], batch["created_at"], batch.get("finished_at"),
                 batch["input_digest"], batch.get("resume_of"),
                 json.dumps(batch.get("params", {}), ensure_ascii=False),
                 batch.get("note", "")),
            )

    def find_done_by_key(self, batch_key: str) -> dict | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM batches WHERE batch_key=? AND status='done' "
                "ORDER BY created_at LIMIT 1", (batch_key,)).fetchone()
        return dict(row) if row else None

    def get_batch(self, batch_id: str) -> dict | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM batches WHERE batch_id=?", (batch_id,)).fetchone()
        return dict(row) if row else None

    def latest_batch(self, status: str = "done") -> dict | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM batches WHERE status=? ORDER BY created_at DESC, rowid DESC LIMIT 1",
                (status,)).fetchone()
            if row is None:
                row = conn.execute(
                    "SELECT * FROM batches ORDER BY created_at DESC, rowid DESC LIMIT 1").fetchone()
        return dict(row) if row else None

    def list_batches(self, limit: int = 30) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM batches ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (limit,)).fetchall()
        return [dict(r) for r in rows]

    # ---------- 运行结果落库（单事务替换） ----------
    def save_run(self, *, batch: dict, tables: list[dict], columns: list[dict],
                 keys: list[dict], star: list[dict], quarantine: list[dict],
                 conflicts: list[dict], row_ledger: list[dict], stages: list[dict],
                 totals: dict, manifest: dict) -> None:
        batch_id = batch["batch_id"]
        with self.connect() as conn:
            for table in ("tables_meta", "columns_meta", "key_findings", "star_objects",
                          "quarantine", "conflicts", "row_ledger", "stages", "totals"):
                conn.execute(f"DELETE FROM {table} WHERE batch_id=?", (batch_id,))
            for t in tables:
                conn.execute(
                    """INSERT INTO tables_meta (batch_id, table_name, ordinal, engine,
                        sample_rows, column_count, declared_pk_json, role, role_reason,
                        scd, scd_reason, metric_count, fk_count)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (batch_id, t["table_name"], t["ordinal"], t.get("engine", "ddl"),
                     t["sample_rows"], t["column_count"],
                     json.dumps(t.get("declared_pk", []), ensure_ascii=False),
                     t.get("role", "pending"), t.get("role_reason", ""),
                     t.get("scd", ""), t.get("scd_reason", ""),
                     t.get("metric_count", 0), t.get("fk_count", 0)))
            for c in columns:
                conn.execute(
                    """INSERT INTO columns_meta (batch_id, table_name, column_name, ordinal,
                        ddl_type, base_type, width, nullable, comment, kind, sample_rows,
                        null_total, empty_count, sentinel_count, distinct_count, unparsable,
                        null_rate, unique_rate, measurement, metric_total, disposition,
                        disposition_order, reason_codes_json, evidence_json, sample_value)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (batch_id, c["table_name"], c["column_name"], c["ordinal"],
                     c["ddl_type"], c["base_type"], c.get("width", 0),
                     1 if c.get("nullable", True) else 0, c.get("comment", ""),
                     c["kind"], c["sample_rows"], c["null_total"], c["empty_count"],
                     c["sentinel_count"], c["distinct_count"], c["unparsable"],
                     str(c["null_rate"]), str(c["unique_rate"]),
                     c.get("measurement", ""),
                     None if c.get("metric_total") is None else str(c["metric_total"]),
                     c["disposition"],
                     {"adopted": 2, "pending": 1, "rejected": 0}[c["disposition"]],
                     json.dumps(sorted(set(c.get("reason_codes", []))), ensure_ascii=False),
                     json.dumps(c.get("evidence", {}), ensure_ascii=False),
                     c.get("sample_value", "")))
            for k in keys:
                conn.execute(
                    """INSERT INTO key_findings (batch_id, kind, child_table,
                        child_columns_json, parent_table, parent_columns_json, declared,
                        status, inclusion, cardinality_ratio, reason_code, evidence_json)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (batch_id, k["kind"], k["child_table"],
                     json.dumps(k["child_columns"], ensure_ascii=False),
                     k.get("parent_table"),
                     json.dumps(k.get("parent_columns", []), ensure_ascii=False),
                     1 if k.get("declared") else 0, k["status"],
                     None if k.get("inclusion") is None else str(k["inclusion"]),
                     None if k.get("cardinality_ratio") is None
                     else str(k["cardinality_ratio"]),
                     k.get("reason_code", ""),
                     json.dumps(k.get("evidence", {}), ensure_ascii=False)))
            for s in star:
                conn.execute(
                    """INSERT INTO star_objects (batch_id, table_name, role, scd,
                        reason_code, metric_columns_json, fk_count, degenerate_json,
                        evidence_json) VALUES (?,?,?,?,?,?,?,?,?)""",
                    (batch_id, s["table_name"], s["role"], s.get("scd", ""),
                     s.get("reason_code", ""),
                     json.dumps(s.get("metric_columns", []), ensure_ascii=False),
                     s.get("fk_count", 0),
                     json.dumps(s.get("degenerate", []), ensure_ascii=False),
                     json.dumps(s.get("evidence", {}), ensure_ascii=False)))
            for q in quarantine:
                conn.execute(
                    """INSERT INTO quarantine (batch_id, code, stage, level, table_name,
                        column_name, row_ref, raw_value, detail)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    (batch_id, q["code"], q["stage"], q["level"], q.get("table_name"),
                     q.get("column_name"), q.get("row_ref"), q.get("raw_value"),
                     q.get("detail")))
            for c in conflicts:
                conn.execute(
                    """INSERT INTO conflicts (batch_id, table_name, key_columns_json,
                        key_value, kept_json, dropped_json, note)
                       VALUES (?,?,?,?,?,?,?)""",
                    (batch_id, c["table_name"],
                     json.dumps(c["key_columns"], ensure_ascii=False), c["key_value"],
                     json.dumps(c.get("kept", {}), ensure_ascii=False),
                     json.dumps(c.get("dropped", {}), ensure_ascii=False),
                     c.get("note", "")))
            for r in row_ledger:
                conn.execute(
                    """INSERT INTO row_ledger (batch_id, table_name, total, valid,
                        null_row, quarantined) VALUES (?,?,?,?,?,?)""",
                    (batch_id, r["table_name"], r["total"], r["valid"],
                     r["null_row"], r["quarantined"]))
            for s in stages:
                conn.execute(
                    """INSERT INTO stages (batch_id, stage, in_count, out_count,
                        quarantine_count, status, elapsed_ms, note)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (batch_id, s["stage"], s["in_count"], s["out_count"],
                     s["quarantine_count"], s["status"], s["elapsed_ms"],
                     s.get("note", "")))
            for key, value in totals.items():
                conn.execute("INSERT INTO totals (batch_id, key, value) VALUES (?,?,?)",
                             (batch_id, key, str(value)))
            conn.execute(
                "INSERT INTO manifests (batch_id, json) VALUES (?,?) "
                "ON CONFLICT(batch_id) DO UPDATE SET json=excluded.json",
                (batch_id, json.dumps(manifest, ensure_ascii=False)))
            conn.execute(
                """INSERT INTO batches (batch_id, batch_key, run_id, source, status,
                        created_at, finished_at, input_digest, resume_of, params_json, note)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(batch_id) DO UPDATE SET
                     status=excluded.status, finished_at=excluded.finished_at,
                     note=excluded.note, input_digest=excluded.input_digest""",
                (batch_id, batch["batch_key"], batch["run_id"], batch["source"],
                 batch["status"], batch["created_at"], batch.get("finished_at"),
                 batch["input_digest"], batch.get("resume_of"),
                 json.dumps(batch.get("params", {}), ensure_ascii=False),
                 batch.get("note", "")))

    # ---------- 读取 ----------
    def load_tables(self, batch_id: str) -> list[dict]:
        rows = self._all(
            "SELECT * FROM tables_meta WHERE batch_id=? ORDER BY ordinal, table_name",
            (batch_id,))
        for r in rows:
            r["declared_pk"] = json.loads(r.pop("declared_pk_json") or "[]")
        return rows

    def load_columns(self, batch_id: str, table_name: str | None = None) -> list[dict]:
        if table_name:
            rows = self._all(
                "SELECT * FROM columns_meta WHERE batch_id=? AND table_name=? "
                "ORDER BY ordinal", (batch_id, table_name))
        else:
            rows = self._all(
                "SELECT * FROM columns_meta WHERE batch_id=? ORDER BY table_name, ordinal",
                (batch_id,))
        for r in rows:
            r["null_rate"] = _dec(r["null_rate"])
            r["unique_rate"] = _dec(r["unique_rate"])
            r["metric_total"] = None if r["metric_total"] is None else _dec(r["metric_total"])
            r["reason_codes"] = json.loads(r.pop("reason_codes_json") or "[]")
            r["evidence"] = json.loads(r.pop("evidence_json") or "{}")
            r["nullable"] = bool(r["nullable"])
        return rows

    def load_keys(self, batch_id: str) -> list[dict]:
        rows = self._all(
            "SELECT * FROM key_findings WHERE batch_id=? ORDER BY kind, child_table, "
            "child_columns_json", (batch_id,))
        for r in rows:
            r["child_columns"] = json.loads(r.pop("child_columns_json") or "[]")
            r["parent_columns"] = json.loads(r.pop("parent_columns_json") or "[]")
            r["declared"] = bool(r["declared"])
            r["inclusion"] = None if r["inclusion"] is None else _dec(r["inclusion"])
            r["cardinality_ratio"] = (None if r["cardinality_ratio"] is None
                                      else _dec(r["cardinality_ratio"]))
            r["evidence"] = json.loads(r.pop("evidence_json") or "{}")
        return rows

    def load_star(self, batch_id: str) -> list[dict]:
        rows = self._all("SELECT * FROM star_objects WHERE batch_id=? ORDER BY table_name",
                         (batch_id,))
        for r in rows:
            r["metric_columns"] = json.loads(r.pop("metric_columns_json") or "[]")
            r["degenerate"] = json.loads(r.pop("degenerate_json") or "[]")
            r["evidence"] = json.loads(r.pop("evidence_json") or "{}")
        return rows

    def load_quarantine(self, batch_id: str) -> list[dict]:
        return self._all(
            "SELECT * FROM quarantine WHERE batch_id=? ORDER BY stage, code, row_ref",
            (batch_id,))

    def load_conflicts(self, batch_id: str) -> list[dict]:
        rows = self._all("SELECT * FROM conflicts WHERE batch_id=? ORDER BY table_name",
                         (batch_id,))
        for r in rows:
            r["key_columns"] = json.loads(r.pop("key_columns_json") or "[]")
            r["kept"] = json.loads(r.pop("kept_json") or "{}")
            r["dropped"] = json.loads(r.pop("dropped_json") or "{}")
        return rows

    def load_row_ledger(self, batch_id: str) -> list[dict]:
        return self._all("SELECT * FROM row_ledger WHERE batch_id=? ORDER BY table_name",
                         (batch_id,))

    def load_stages(self, batch_id: str) -> list[dict]:
        from . import config
        rows = self._all("SELECT * FROM stages WHERE batch_id=? ORDER BY rowid", (batch_id,))
        for row in rows:
            row["label"] = config.STAGE_LABELS.get(row["stage"], row["stage"])
        return rows

    def load_totals(self, batch_id: str) -> dict:
        rows = self._all("SELECT key, value FROM totals WHERE batch_id=?", (batch_id,))
        return {r["key"]: r["value"] for r in rows}

    def load_manifest(self, batch_id: str) -> dict | None:
        with self.connect() as conn:
            row = conn.execute("SELECT json FROM manifests WHERE batch_id=?",
                               (batch_id,)).fetchone()
        return json.loads(row["json"]) if row else None

    def count_rows(self, batch_id: str) -> dict:
        with self.connect() as conn:
            return {
                table: conn.execute(
                    f"SELECT COUNT(*) AS n FROM {table} WHERE batch_id=?", (batch_id,)
                ).fetchone()["n"]
                for table in ("tables_meta", "columns_meta", "key_findings", "star_objects",
                              "quarantine", "conflicts", "row_ledger")
            }

    def _all(self, sql: str, args: tuple) -> list[dict]:
        with self.connect() as conn:
            return [dict(r) for r in conn.execute(sql, args).fetchall()]


def _dec(text: str) -> Decimal:
    return Decimal(text)
