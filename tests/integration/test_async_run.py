"""异步提交（后台执行 + 轮询）测试。

离线用例用「不可达的模型端点」和「预置活动运行」确定性地覆盖 202 受理与 409 冲突；
正常完成路径用真实模型覆盖（标记 real_model）。
"""

from __future__ import annotations

import asyncio
import time

import pytest
from tests.conftest import build_test_settings
from tests.support.web_app import running_client

from learning_agent.config import Settings
from learning_agent.main import create_app

DOC_URL = "https://liaoxuefeng.com/books/python/introduction/index.html"
_TERMINAL = {"completed", "failed", "cancelled"}


async def wait_for_run(client, run_id: str, *, timeout_seconds: float = 30.0) -> dict:
    """轮询运行状态直到终态。"""

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        response = await client.get(f"/api/v1/runs/{run_id}")
        assert response.status_code == 200, response.text
        run = response.json()
        if run["status"] in _TERMINAL:
            return run
        await asyncio.sleep(0.05)
    raise AssertionError("运行未在预期时间内结束")


async def test_async_submission_returns_202_then_reports_failure(tmp_path) -> None:
    """异步提交立即返回 202；模型不可达时运行以后台失败收尾。"""

    app = create_app(
        build_test_settings(
            database_url=f"sqlite:///{(tmp_path / 'async.db').as_posix()}",
            model_base_url="http://127.0.0.1:9/v1",
            model_max_retries=0,
        )
    )
    async with running_client(app) as client:
        conversation_id = (await client.post("/api/v1/conversations", json={})).json()[
            "id"
        ]
        response = await client.post(
            f"/api/v1/conversations/{conversation_id}/messages?mode=async",
            json={"content": "请解释什么是依赖注入"},
        )
        assert response.status_code == 202, response.text
        accepted = response.json()
        assert accepted["run_id"]

        run = await wait_for_run(client, accepted["run_id"])
        history = (
            await client.get(f"/api/v1/conversations/{conversation_id}/messages")
        ).json()

    assert run["status"] == "failed"
    assert run["error_code"] == "model_error"
    # 用户消息已落库，失败运行不产生助手消息
    assert [message["role"] for message in history] == ["user"]


async def test_async_submission_rejected_when_run_active(tmp_path) -> None:
    """已有活动运行时，异步提交立即返回 409，且不写入新用户消息。"""

    app = create_app(
        build_test_settings(
            database_url=f"sqlite:///{(tmp_path / 'busy-async.db').as_posix()}"
        )
    )
    repositories = app.state.repositories
    async with running_client(app) as client:
        conversation_id = (await client.post("/api/v1/conversations", json={})).json()[
            "id"
        ]
        repositories.create_run(
            run_id="44444444-4444-4444-4444-444444444444",
            conversation_id=conversation_id,
            model_name="test-model",
            prompt_version="v1",
        )
        before = len(repositories.list_messages(conversation_id))
        blocked = await client.post(
            f"/api/v1/conversations/{conversation_id}/messages?mode=async",
            json={"content": "并发第二次提交"},
        )
        assert blocked.status_code == 409, blocked.text
        assert blocked.json()["error"]["code"] == "run_in_progress"
        assert len(repositories.list_messages(conversation_id)) == before


@pytest.mark.real_model
async def test_async_run_completes_with_real_model(tmp_path) -> None:
    """真实模型链路：异步提交后轮询到 completed，消息与来源齐备。"""

    settings = Settings().model_copy(
        update={"database_url": f"sqlite:///{(tmp_path / 'async-real.db').as_posix()}"}
    )
    app = create_app(settings)
    async with running_client(app, timeout_seconds=300) as client:
        conversation_id = (await client.post("/api/v1/conversations", json={})).json()[
            "id"
        ]
        response = await client.post(
            f"/api/v1/conversations/{conversation_id}/messages?mode=async",
            json={"content": f"请阅读 {DOC_URL} ，说明这份文档的主题。"},
        )
        assert response.status_code == 202, response.text
        run = await wait_for_run(client, response.json()["run_id"], timeout_seconds=280)
        history = (
            await client.get(f"/api/v1/conversations/{conversation_id}/messages")
        ).json()

    assert run["status"] == "completed", run
    assert [message["role"] for message in history] == ["user", "assistant"]
    assert history[-1]["sources"]
