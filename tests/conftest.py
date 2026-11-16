"""测试固定装置：临时库 + 样例目录 + 一次完整运行的批次。

约定：所有断言都用样例数据算出的确定值，不读 datetime.now()，不用随机数。
"""
from __future__ import annotations

import pathlib
import sys

import pytest
from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import config                                  # noqa: E402
from app.main import create_app                         # noqa: E402
from app.modules.m5_batches import service as batch_service  # noqa: E402
from app.store import Store                             # noqa: E402


@pytest.fixture()
def sample_dir() -> pathlib.Path:
    return ROOT / "sample_data"


@pytest.fixture()
def settings(tmp_path, sample_dir) -> config.Settings:
    return config.Settings(db_path=str(tmp_path / "starmodel.sqlite"),
                           sample_dir=str(sample_dir))


@pytest.fixture()
def store(settings) -> Store:
    return Store(settings.db_path)


@pytest.fixture()
def run(settings, store) -> dict:
    return batch_service.run(settings=settings, store=store, source="sample")


@pytest.fixture()
def batch_id(run) -> str:
    return run["batch_id"]


@pytest.fixture()
def bundle(store, batch_id) -> dict:
    return batch_service.bundle(store, batch_id)


@pytest.fixture()
def app(settings):
    return create_app(settings)


@pytest.fixture()
def client(app) -> TestClient:
    return TestClient(app)


@pytest.fixture()
def client_with_run(client) -> TestClient:
    response = client.post("/api/import", json={"source": "sample"})
    assert response.status_code == 200
    assert response.json()["status"] == "done"
    return client


def column_of(bundle: dict, table: str, name: str) -> dict:
    for row in bundle["columns"]:
        if row["table_name"] == table and row["column_name"] == name:
            return row
    raise AssertionError(f"列不存在：{table}.{name}")


def key_of(bundle: dict, kind: str, child: str) -> dict:
    for row in bundle["keys"]:
        if row["kind"] == kind and row["child_table"] == child:
            return row
    raise AssertionError(f"键结论不存在：{kind} {child}")


def star_of(bundle: dict, table: str) -> dict:
    for row in bundle["star"]:
        if row["table_name"] == table:
            return row
    raise AssertionError(f"星型对象不存在：{table}")
