"""M7 建模影响评估：下游影响面、迁移拓扑序、回滚提示、空值与大小写两条深规则。"""
from __future__ import annotations

import json
from pathlib import Path

from app import config, reasons
from app.modules.m1_ingest import service as m1_service
from app.modules.m2_contract import service as m2_service
from app.modules.m7_impact import impact, rollback, service, topology

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"
FIX = json.loads((FIXTURE_DIR / "m7_impact.json").read_text(encoding="utf-8"))
CASES = json.loads((FIXTURE_DIR / "m7_cases.json").read_text(encoding="utf-8"))

DEDUPE = FIX["order"][0]
ADD_UNIQUE = FIX["order"][6]
ADD_FK = FIX["order"][7]
CASE_CHANGE = FIX["case_change"]


def _registry_text(sample_dir: Path) -> str:
    return (sample_dir / service.REGISTRY_NAME).read_text(encoding="utf-8-sig")


def _synthetic_plan(changes: list, rows_by_table: dict) -> dict:
    return service.plan(bundle=CASES["synthetic_bundle"], changes=changes,
                        rows_by_table=rows_by_table,
                        registry=CASES["synthetic_registry"])


def test_registry_parsed_with_duplicate_and_upper_case(sample_dir):
    """下游登记表：重复导出保留首行，大写表名归一，列名大小写原样保留。"""
    parsed = impact.parse_registry(_registry_text(sample_dir))
    assert parsed["total"] == FIX["registry"]["total"] == 19
    assert parsed["dups"] == FIX["registry"]["dups"] == 1
    assert [row["code"] for row in parsed["errors"]] == FIX["registry"]["error_codes"]
    assert [row["line"] for row in parsed["errors"]] == FIX["registry"]["error_lines"]

    rows = {row["object_id"]: row for row in parsed["rows"]}
    assert rows["RPT-017"]["ref_table"] == "odl_sale_order_item"
    assert rows["RPT-016"]["ref_column"] == "Store_Code"
    assert rows["DW-V-001"]["ref_column"] == "order_amount"
    assert len(rows) == parsed["total"]


def test_derived_changes_come_from_key_and_column_findings(settings, store, bundle):
    """派生变更逐条能追到键或列结论，同输入两次派生顺序一致。"""
    coerced = m2_service.build(m1_service.intake(settings), settings)
    changes = impact.derive(bundle, coerced)
    ids = [change["change_id"] for change in changes]
    assert len(changes) == FIX["summary"]["changes"] == 9
    assert ids[0] == DEDUPE
    assert ADD_UNIQUE in ids
    assert ADD_FK in ids
    assert "fix_orphan:odl_store:region_code" in ids
    assert CASE_CHANGE in ids
    case_change = next(item for item in changes if item["change_id"] == CASE_CHANGE)
    assert case_change["columns"] == FIX["case_columns"]
    assert case_change["target"]["columns"] == ["store_code"]

    again = [change["change_id"] for change in impact.derive(bundle, coerced)]
    assert again == ids


def test_impact_level_follows_criticality_and_reference_mode(sample_dir):
    """同一变更：P1 精确引用判中断，P2 降为核对，整表引用一律核对。"""
    registry = impact.parse_registry(_registry_text(sample_dir))
    change = impact.make_change("dedupe_rows", "odl_inventory_snapshot",
                                ["snapshot_date", "store_code", "product_code"])
    result = impact.evaluate(change, registry["rows"], [])
    levels = {row["object_id"]: row["level"] for row in result["rows"]}
    modes = {row["object_id"]: row["mode"] for row in result["rows"]}
    assert levels["DW-V-019"] == FIX["criticality_levels"]["DW-V-019"] == "break"
    assert levels["MTR-010"] == FIX["criticality_levels"]["MTR-010"] == "review"
    assert levels["DW-SQL-008"] == FIX["criticality_levels"]["DW-SQL-008"] == "review"
    assert modes["DW-SQL-008"] == "table"
    assert [row["object_id"] for row in result["unknown"]] == ["DW-SQL-008"]
    assert impact.severity(breaking=False, criticality="P3", mode="column") == "info"


def test_trap28_case_pseudo_unique_blocks_column_merge(settings, store, batch_id):
    """TRAP-28：两列仅大小写不同，折叠后看起来「合并仍唯一」，实际是伪唯一。"""
    payload = service.build(settings, store, batch_id)
    case_change = next(item for item in payload["changes"]
                       if item["change"]["change_id"] == CASE_CHANGE)
    assert case_change["blocked"]["code"] == FIX["case_block"] == "M_CASE_PSEUDO_UNIQUE"
    modes = {row["object_id"]: row["mode"] for row in case_change["rows"]}
    assert modes["RPT-016"] == "column"
    assert modes["DW-V-006"] == "case"
    assert modes["RPT-007"] == "case"
    assert [row["object_id"] for row in payload["pseudo"]] == FIX["pseudo_objects"]
    assert CASE_CHANGE not in [row["change_id"] for row in payload["order"]]
    assert payload["summary"]["blocked"] == 1


def test_trap28_folded_distinct_loses_real_values():
    """折叠比较把大小写不同的取值并成一个，去重数变少，据此判唯一就是伪唯一。"""
    rows = CASES["fold_rows"]
    folded = impact.case_pseudo_unique(rows, keep="code", fold="old")
    assert folded["strict_distinct"] == 4
    assert folded["folded_distinct"] == 3
    assert folded["pseudo"] is True
    assert folded["folded_unique"] is False
    folded_values = [row["code"] for row in rows] + [row["old"] for row in rows
                                                     if row["old"]]
    assert len({value.casefold() for value in folded_values}) == folded["folded_distinct"]
    assert len(set(folded_values)) == folded["strict_distinct"]
    assert impact.folded_columns(["store_code", "Store_Code", "region_code"]) \
        == [["store_code", "Store_Code"]]
    assert impact.folded_columns(["a", "b"]) == []


def test_trap27_partial_null_key_blocks_unique_change():
    """TRAP-27：组合键有部分为空，两种空值规则结论相反，变更必须阻断待裁定。"""
    changes = [impact.make_change(item["kind"], item["table"], item["columns"],
                                  target=item.get("target") or {})
               for item in CASES["synthetic_changes"]]
    payload = _synthetic_plan(changes, {"t_demo": CASES["partial_null_rows"]})
    blocked = {row["change_id"]: row["code"] for row in payload["blocked"]}
    add_unique_id = impact.change_id("add_unique", "t_demo", ["a", "b"])
    add_fk_id = impact.change_id("add_fk", "t_child", ["demo_id"])
    assert blocked[add_unique_id] == "M_NULL_KEY_AMBIGUOUS"
    assert blocked[add_fk_id] == "M_MIGRATION_BLOCKED"
    assert len(blocked) == 2
    assert [row["change_id"] for row in payload["order"]] == ["dedupe_rows:t_demo:a|b"]
    assert payload["summary"]["executable"] == 1
    assert payload["summary"]["balanced"] is True
    assert payload["summary"]["changes"] == 3


def test_trap27_naive_set_equality_fails_on_empty_and_null_exempt():
    """反例：len(set(t)) == len(t) 对空集返回真，对免检行也会给出相反结论。"""
    rows = CASES["partial_null_rows"]
    sql = impact.uniqueness(rows, ["a", "b"], "sql")
    strict = impact.uniqueness(rows, ["a", "b"], "strict")
    assert (sql["counted"], sql["exempt"]) == (1, 2)
    assert sql["unique"] is True
    assert strict["unique"] is False
    assert impact.verdicts(rows, ["a", "b"])["ambiguous"] is True
    assert impact.partial_null_rows(rows, ["a", "b"]) == 2

    assert len(set()) == len([])  # 朴素写法在空集上返回真
    empty_rows = CASES["all_null_key_rows"]
    empty = impact.uniqueness(empty_rows, ["a", "b"], "sql")
    assert (empty["counted"], empty["exempt"], empty["empty"]) == (0, 2, True)
    assert empty["unique"] is False
    assert impact.verdicts(empty_rows, ["a", "b"])["empty"] is True


def test_empty_key_verdict_blocks_through_service():
    """有效行 0 时不能判唯一：提交的加唯一约束被阻断，原因码单独一条。"""
    changes = [impact.make_change(item["kind"], item["table"], item["columns"])
               for item in CASES["synthetic_empty_changes"]]
    payload = _synthetic_plan(changes, {"t_empty": []})
    assert [row["code"] for row in payload["blocked"]] == ["M_KEY_NO_ROWS"]
    assert payload["order"] == []
    assert payload["summary"]["changes"] == 1
    assert payload["summary"]["balanced"] is True


def test_migration_order_is_phase_monotone(settings, store, batch_id):
    """迁移顺序：阶段不倒退，去重先于加唯一约束，阻断项不进顺序表。"""
    payload = service.build(settings, store, batch_id)
    order = [row["change_id"] for row in payload["order"]]
    assert order == FIX["order"]
    assert [row["position"] for row in payload["order"]] == list(range(1, len(order) + 1))
    by_id = {item["change"]["change_id"]: item["change"] for item in payload["changes"]}
    ranks = [impact.PHASES.index(by_id[change_id]["phase"]) for change_id in order]
    assert ranks == sorted(ranks)
    assert order.index(DEDUPE) < order.index(ADD_UNIQUE)
    assert order[-1] == ADD_FK


def test_topology_reports_each_cycle_once():
    """依赖成环时排不出顺序；同一个环只报一条路径，其余按前置被阻断。"""
    left = impact.make_change("add_unique", "t_a", ["k"])
    right = impact.make_change("add_unique", "t_b", ["k"])
    deps = {left["change_id"]: {right["change_id"]},
            right["change_id"]: {left["change_id"]}}
    result = topology.order([left, right], deps)
    assert result["order"] == []
    assert result["layers"] == []
    assert len(result["cycles"]) == 1
    cycle = result["cycles"][0]
    assert cycle[0] == cycle[-1]
    assert sorted(cycle[:-1]) == sorted([left["change_id"], right["change_id"]])

    blockers = topology.blockers(
        [left, right], deps,
        {left["change_id"]: {"code": "M_MIGRATION_CYCLE", "reason": "成环"}})
    codes = {row["change_id"]: row["code"] for row in blockers}
    assert codes[left["change_id"]] == "M_MIGRATION_CYCLE"
    assert codes[right["change_id"]] == "M_MIGRATION_BLOCKED"


def test_rollback_is_reverse_order_with_reversibility(settings, store, batch_id):
    """回滚按迁移逆序：先撤外键，最后才碰删行；删行与孤儿值清洗不可原地回滚。"""
    payload = service.build(settings, store, batch_id)
    order = [row["change_id"] for row in payload["order"]]
    rows = payload["rollback"]
    assert [row["change_id"] for row in rows] == list(reversed(order))
    assert [row["position"] for row in rows] == list(range(1, len(rows) + 1))
    fixed = {row["change_id"]: row for row in rows}
    assert fixed[ADD_FK]["reversible"] is True
    assert fixed[ADD_FK]["sql"] == "ALTER TABLE odl_payment DROP CONSTRAINT " \
                                 "fk_odl_payment_order_id"
    assert fixed[DEDUPE]["reversible"] is False
    assert fixed[DEDUPE]["needs_snapshot"] is True
    assert fixed["fix_orphan:odl_store:region_code"]["reversible"] is False
    assert rollback.REVERSIBLE["dedupe_rows"] is False
    assert payload["summary"]["irreversible"] == 2


def test_impact_api_and_page_show_same_totals(client_with_run):
    """接口与页面共用一次装配：页面上的合计就是接口里的数字。"""
    body = client_with_run.get("/api/impact").json()
    for key, value in FIX["summary"].items():
        assert body["summary"][key] == value, key
    assert [row["change"]["change_id"] for row in body["changes"]] \
        == list(FIX["change_levels"])
    for item in body["changes"]:
        change_id = item["change"]["change_id"]
        assert [item["level"], item["objects"]] == FIX["change_levels"][change_id]
    assert [row["object_id"] for row in body["unknown"]] == FIX["unknown_objects"]

    page = client_with_run.get("/impact")
    assert page.status_code == 200
    assert body["batch_id"] in page.text
    assert "变更清单" in page.text and "迁移顺序" in page.text
    assert FIX["case_block"] in page.text


def test_propose_validates_input_and_marks_narrowing(client_with_run):
    """提交变更要过类型、表、列三关；收窄类型标为不可原地回滚。"""
    def post(payload):
        return client_with_run.post("/api/impact/propose", json=payload)

    bad_kind = post({"changes": [{"kind": "drop_table", "table": "odl_store",
                                  "columns": ["store_code"]}]})
    assert bad_kind.status_code == 400
    assert bad_kind.json()["error"] == "M_BAD_CHANGE"
    bad_table = post({"changes": [{"kind": "add_fk", "table": "odl_nope",
                                   "columns": ["store_code"]}]})
    assert bad_table.status_code == 400
    bad_column = post({"changes": [{"kind": "add_fk", "table": "odl_store",
                                    "columns": ["nope"]}]})
    assert bad_column.status_code == 400
    assert "nope" in bad_column.json()["message"]
    assert post({"changes": []}).status_code == 400

    ok = post({"changes": [{"kind": "alter_type", "table": "odl_sale_order",
                            "columns": ["order_amount"], "narrowing": True}]})
    body = ok.json()
    assert ok.status_code == 200
    assert body["source"] == "proposed"
    assert body["summary"]["changes"] == 1
    assert body["summary"]["break"] >= 1
    assert body["rollback"][0]["reversible"] is False
    assert body["rollback"][0]["needs_snapshot"] is True
    assert body["summary"]["balanced"] is True


def test_every_m7_reason_code_is_registered():
    """M7 用到与可能用到的原因码都登记在 reasons.py，阶段统一为 impact。"""
    codes = ["M_CHANGE_DERIVED", "M_IMPACT_BREAK", "M_IMPACT_WILDCARD", "M_PSEUDO_MATCH",
             "M_NULL_KEY_AMBIGUOUS", "M_CASE_PSEUDO_UNIQUE", "M_KEY_NO_ROWS",
             "M_MIGRATION_CYCLE", "M_MIGRATION_BLOCKED", "M_ROLLBACK_IRREVERSIBLE",
             "M_ROLLBACK_ORDER", "M_REGISTRY_SHORT_ROW", "M_REGISTRY_BAD_LEVEL",
             "M_REGISTRY_DUP_ROW", "M_NO_CHANGE", "M_BAD_CHANGE"]
    for code in codes:
        assert code in reasons.REASONS, code
        assert reasons.stage_of(code) == "impact"
        assert reasons.label(code) != code
    assert set(service.SEED_CODES) <= set(codes)
    assert reasons.get("M_BAD_CHANGE").level == "error"
    assert config.STAGE_LABELS["impact"] == "影响评估"


def test_impact_page_before_first_run_returns_empty_state(client):
    """还没有批次时页面给空态，接口给 404 与稳定原因码。"""
    page = client.get("/impact")
    assert page.status_code == 200
    assert "还没有批次。" in page.text
    missing = client.get("/api/impact")
    assert missing.status_code == 404
    assert missing.json()["error"] == "B_EMPTY"
