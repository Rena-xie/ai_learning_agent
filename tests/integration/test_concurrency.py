"""并发一致性测试：同一对话同一时间只允许一个活动运行。"""

from __future__ import annotations

import asyncio

import pytest
from tests.conftest import build_test_settings
from tests.support.web_app import running_client

from learning_agent.config import Settings
from learning_agent.domain.entities import UsageRecord
from learning_agent.domain.enums import RunStatus
from learning_agent.main import create_app

DOC_URL = "https://liaoxuefeng.com/books/python/introduction/index.html"


async def test_second_submission_is_rejected_while_run_active(tmp_path) -> None:
    """离线路径：已有活动运行时再次提交返回 409，且不写入新的用户消息。"""

    app = create_app(
        build_test_settings(
            database_url=f"sqlite:///{(tmp_path / 'busy.db').as_posix()}"
        )
    )
    repositories = app.state.repositories
    async with running_client(app) as client:
        conversation_id = (await client.post("/api/v1/conversations", json={})).json()[
            "id"
        ]
        repositories.create_run(
            run_id="33333333-3333-3333-3333-333333333333",
            conversation_id=conversation_id,
            model_name="test-model",
            prompt_version="v1",
        )
        before = len(repositories.list_messages(conversation_id))
        blocked = await client.post(
            f"/api/v1/conversations/{conversation_id}/messages",
            json={"content": "运行进行中的第二次提交"},
        )
        assert blocked.status_code == 409
        assert blocked.json()["error"]["code"] == "run_in_progress"
        assert len(repositories.list_messages(conversation_id)) == before
        repositories.finish_run(
            run_id="33333333-3333-3333-3333-333333333333",
            status=RunStatus.COMPLETED,
            usage=UsageRecord(),
        )


@pytest.mark.real_model
async def test_concurrent_real_run_rejects_second_submission(tmp_path) -> None:
    """真实模型链路：运行进行中拒绝并发提交，消息顺序与数量保持一致。"""

    settings = Settings().model_copy(
        update={"database_url": f"sqlite:///{(tmp_path / 'race.db').as_posix()}"}
    )
    app = create_app(settings)
    repositories = app.state.repositories
    async with running_client(app, timeout_seconds=300) as client:
        conversation_id = (await client.post("/api/v1/conversations", json={})).json()[
            "id"
        ]
        task = asyncio.create_task(
            client.post(
                f"/api/v1/conversations/{conversation_id}/messages",
                json={"content": f"请阅读 {DOC_URL} ，说明这份文档的主题。"},
            )
        )
        for _ in range(160):
            if repositories.get_active_run(conversation_id) is not None:
                break
            await asyncio.sleep(0.25)
        else:
            pytest.fail("运行未在预期时间内开始")

        conflict = await client.post(
            f"/api/v1/conversations/{conversation_id}/messages",
            json={"content": "运行进行中的第二次提交"},
        )
        first = await task
        history = (
            await client.get(f"/api/v1/conversations/{conversation_id}/messages")
        ).json()

    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "run_in_progress"
    assert first.status_code == 201, first.text
    assert [message["role"] for message in history] == ["user", "assistant"]
    assert "第二次提交" not in "".join(message["content"] for message in history)
