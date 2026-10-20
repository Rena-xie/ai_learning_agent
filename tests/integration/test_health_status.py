"""就绪检查中的知识库状态。"""

from __future__ import annotations

from pathlib import Path

from tests.conftest import build_test_settings
from tests.support.web_app import running_client

from learning_agent import __version__
from learning_agent.main import create_app


class FakeKnowledgeClient:
    def __init__(self, healthy: bool) -> None:
        self._healthy = healthy

    async def health(self) -> bool:
        return self._healthy

    async def aclose(self) -> None:
        return None


def _settings(tmp_path: Path, **overrides: object):
    return build_test_settings(
        database_url=f"sqlite:///{(tmp_path / 'health.db').as_posix()}",
        **overrides,
    )


async def test_ready_reports_knowledge_base_ok(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path), knowledge_client=FakeKnowledgeClient(True))

    async with running_client(app) as client:
        body = (await client.get("/health/ready")).json()

    assert body["status"] == "ok"
    assert body["knowledge_base"] == "ok"


async def test_ready_reports_knowledge_base_unavailable(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path), knowledge_client=FakeKnowledgeClient(False))

    async with running_client(app) as client:
        body = (await client.get("/health/ready")).json()

    assert body["status"] == "ok"
    assert body["knowledge_base"] == "unavailable"


async def test_ready_reports_knowledge_base_disabled(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path, knowledge_base_enabled=False))

    async with running_client(app) as client:
        body = (await client.get("/health/ready")).json()

    assert body["knowledge_base"] == "disabled"


def test_app_version_matches_v33_release(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path, knowledge_base_enabled=False))

    assert __version__ == "3.3.1"
    assert app.version == "3.3.1"
