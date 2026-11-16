"""M1 元数据接入：编码回退、BOM、分段、重复表头、列数不齐、末行截断。"""
from __future__ import annotations

from app import config
from app.modules.m1_ingest import decode, ddl, reader, service, sniff
from fixtures import expected as X


def _codes(data) -> set:
    return {row["code"] for row in data.quarantine}


def test_trap01_gb18030_fallback_decodes_chinese(sample_dir):
    """TRAP-01：文件声明 utf-8 但字节是 GB18030，必须回退且中文姓名可读。"""
    raw = (sample_dir / "sample_dump_gb18030.csv").read_bytes()
    decoded = decode.decode_bytes(raw, content_type="text/csv; charset=utf-8")
    assert decoded.encoding == "gb18030"
    assert decoded.declared == "utf-8"
    assert "E_CHARSET_FALLBACK" in decoded.codes
    assert "赵一鸣" in decoded.text
    assert "\ufffd" not in decoded.text


def test_trap01_member_section_parsed_after_fallback(settings):
    """回退解码后会员段能被切开，中文姓名进样本行。"""
    data = service.intake(settings)
    section = data.sections["odl_member"]
    assert section.header == ["member_id", "member_name", "phone", "join_date",
                             "tier", "points"]
    names = [row.cells[1] for row in section.rows]
    assert names == ["赵一鸣", "钱多多", "孙小满", "李有福", "吴桐"]


def test_trap02_bom_stripped_from_section_header(sample_dir):
    """TRAP-02：首行与分段表头都带 BOM，剥离后列名不能以 U+FEFF 开头。"""
    raw = (sample_dir / "sample_dump.csv").read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    decoded = decode.decode_bytes(raw)
    assert decoded.bom_stripped is True
    assert "E_BOM_STRIPPED" in decoded.codes
    sections = sniff.split_sections(decoded.text)
    product = next(item for item in sections if item.name == "odl_product")
    assert product.header[0] == "product_code"
    assert not product.header[0].startswith("\ufeff")
    assert decoded.text.startswith("## table=odl_region")


def test_duplicate_header_counted_not_parsed_as_row(settings):
    """分段导出又写了一次表头：重复表头剔除，且样本行数仍等于真实数据行。"""
    data = service.intake(settings)
    section = data.sections["odl_sale_order"]
    assert section.dup_headers == 1
    assert len(section.rows) == X.SECTION_ROWS["odl_sale_order"]
    assert "Q_DUP_HEADER" in _codes(data)


def test_truncated_tail_row_quarantined(settings):
    """TRAP-16 同类：末行被截断（列数少于表头）进隔离区，好行继续。"""
    data = service.intake(settings)
    section = data.sections["odl_inventory_snapshot"]
    assert section.short_rows == [60]
    assert len(section.rows) == X.SECTION_ROWS["odl_inventory_snapshot"]
    assert "Q_ROW_TRUNCATED" in _codes(data)


def test_section_row_counts_match_fixture(settings):
    """分段行数逐一比对，防止某段被吃掉或重复计数。"""
    data = service.intake(settings)
    assert data.section_count == len(X.SECTION_ROWS)
    for name, expected in X.SECTION_ROWS.items():
        assert len(data.sections[name].rows) == expected
    assert data.sections["odl_sale_order"].header[0] == "order_id"


def test_ddl_parses_every_table_and_reference(settings):
    """DDL 解析出 8 张表、7 条声明外键，主键按 DDL 顺序保留。"""
    data = service.intake(settings)
    names = [table.name for table in data.ddl.tables]
    assert names == list(X.TABLE_ORDER)
    assert ddl.declared_reference_count(data.ddl) == X.DECLARED_FK
    assert data.ddl.table("odl_sale_order_item").primary_key == ["order_id", "line_no"]
    assert data.ddl.table("odl_store").columns[1].name == "Store_Code"


def test_input_manifest_has_sha256_for_every_file(settings):
    """运行清单的输入项必须带 sha256 与命中编码，缺一不可复算。"""
    data = service.intake(settings)
    assert len(data.inputs) == 3
    for item in data.inputs:
        assert len(item["sha256"]) == 64
        assert item["bytes"] > 0
        assert item["encoding"] in config.ENCODING_CHAIN
    roles = {item["role"] for item in data.inputs}
    assert roles == {"ddl", "sample"}


def test_missing_input_raises_not_found(tmp_path):
    """文件缺失要显式报错，不能静默返回空表。"""
    import pytest

    from app import errors

    missing = config.Settings(db_path=str(tmp_path / "x.sqlite"),
                              sample_dir=str(tmp_path / "nowhere"))
    with pytest.raises(errors.NotFound):
        reader.load_inputs(missing)
