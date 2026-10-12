"""pytest 共享夹具。"""

from __future__ import annotations

import pytest

from learning_agent.config import Settings
from tests.support.local_doc_server import TEST_HOST, LocalDocServer


@pytest.fixture
def local_server():
    server = LocalDocServer()
    try:
        yield server
    finally:
        server.close()


def build_test_settings(**overrides: object) -> Settings:
    """构造测试用配置（默认指向本地可控文档服务器，不访问真实网络）。"""

    base: dict[str, object] = {
        "model_api_key": "test-key",
        "model_name": "test-model",
        "model_base_url": "https://api.example.com/v1",
        "allowed_document_hosts": (TEST_HOST,),
        "log_level": "WARNING",
    }
    base.update(overrides)
    return Settings(**base)


@pytest.fixture
def settings(tmp_path) -> Settings:
    return build_test_settings(
        database_url=f"sqlite:///{(tmp_path / 'test.db').as_posix()}"
    )
