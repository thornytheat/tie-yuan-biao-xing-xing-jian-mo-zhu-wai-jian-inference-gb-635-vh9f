"""模板渲染与共享视图上下文：过滤器、导航、当前批次。

页面只读落库结果，不做业务计算；比率与金额的显示规则集中在这里。
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates

from . import config, reasons

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
STATIC_DIR = Path(__file__).resolve().parent / "static"

NAV = (
    ("/", "导入与剖析", "import"),
    ("/profile", "列剖析", "profile"),
    ("/keys", "键推断", "keys"),
    ("/star", "星型建议", "star"),
    ("/impact", "影响评估", "impact"),
    ("/batches", "版本历史", "batches"),
    ("/spec", "规格单", "spec"),
)

STATUS_LABELS = {"done": "完成", "running": "运行中", "failed": "失败", "aborted": "中断"}
DISPOSITION_ORDER = ("adopted", "pending", "rejected")


@dataclass
class Ctx:
    settings: config.Settings
    store: object

    def current_batch(self, batch_id: str | None = None) -> dict | None:
        if batch_id:
            return self.store.get_batch(batch_id)
        return self.store.latest_batch()


def get_ctx(request: Request) -> Ctx:
    return request.app.state.ctx


def now_local() -> dt.datetime:
    return dt.datetime.now(config.CHINA_TZ)


def fmt_rate(value) -> str:
    if value is None or value == "":
        return "—"
    try:
        number = Decimal(str(value))
    except Exception:
        return str(value)
    return f"{number:.4f}"


def fmt_pct(value) -> str:
    if value is None or value == "":
        return "—"
    try:
        number = Decimal(str(value))
    except Exception:
        return str(value)
    return f"{(number * 100).quantize(Decimal('0.01'))}%"


def fmt_dec(value) -> str:
    if value is None or value == "":
        return "—"
    try:
        number = Decimal(str(value))
    except Exception:
        return str(value)
    return f"{number.quantize(config.DECIMAL_QUANT):,}"


def fmt_int(value) -> str:
    if value is None or value == "":
        return "—"
    try:
        return str(int(value))
    except Exception:
        return str(value)


def fmt_ts(value) -> str:
    if not value:
        return "—"
    return str(value).replace("T", " ")[:19]


def setup(app) -> None:
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    env = templates.env
    env.filters["rate"] = fmt_rate
    env.filters["pct"] = fmt_pct
    env.filters["dec"] = fmt_dec
    env.filters["int"] = fmt_int
    env.filters["ts"] = fmt_ts
    env.filters["code"] = reasons.label
    env.filters["level"] = lambda code: reasons.LEVEL_LABELS.get(reasons.level_of(code), "")
    env.filters["disposition"] = lambda key: config.DISPOSITION_LABELS.get(key, key)
    env.filters["role"] = lambda key: config.ROLE_LABELS.get(key, key or "—")
    env.filters["scd"] = lambda key: config.SCD_LABELS.get(key, key or "—")
    env.filters["status"] = lambda key: STATUS_LABELS.get(key, key or "—")
    env.globals.update(
        nav=NAV,
        stage_labels=config.STAGE_LABELS,
        stages=config.STAGES,
        reason_catalog=reasons.catalog(),
        level_labels=reasons.LEVEL_LABELS,
        disposition_labels=config.DISPOSITION_LABELS,
        role_labels=config.ROLE_LABELS,
        scd_labels=config.SCD_LABELS,
        rule_version=config.RULE_VERSION,
        money_unit="元",
        qty_unit="件",
        timezone_label="Asia/Shanghai (+08:00)",
        generated_at=now_local().strftime("%Y-%m-%d %H:%M"),
    )
    app.state.templates = templates


def render(request: Request, name: str, **context):
    ctx: Ctx = request.app.state.ctx
    batch = context.pop("batch", None)
    if batch is None:
        batch = ctx.current_batch(context.get("batch_id"))
    stages = context.pop("stages", None)
    if stages is None and batch is not None:
        stages = ctx.store.load_stages(batch["batch_id"])
    base = {
        "request": request,
        "ctx": ctx,
        "settings": ctx.settings,
        "batch": batch,
        "stages": stages or [],
        "generated_at": now_local().strftime("%Y-%m-%d %H:%M"),
    }
    base.update(context)
    return request.app.state.templates.TemplateResponse(
        request=request, name=name, context=base)
