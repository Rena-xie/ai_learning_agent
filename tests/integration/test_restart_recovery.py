"""重启恢复测试：应用重启后历史对话、消息与来源仍可读取（真实模型链路）。"""

from __future__ import annotations

import httpx
import pytest

from learning_agent.config import Settings
from learning_agent.main import create_app

pytestmark = pytest.mark.real_model

DOC_URL = "https://liaoxuefeng.com/books/python/introduction/index.html"


async def test_history_survives_restart(tmp_path) -> None:
    database_url = f"sqlite:///{(tmp_path / 'restart.db').as_posix()}"
    settings = Settings().model_copy(update={"database_url": database_url})

    first_app = create_app(settings)
    async with first_app.router.lifespan_context(first_app):
        transport = httpx.ASGITransport(app=first_app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
            timeout=httpx.Timeout(300),
        ) as client:
            conversation_id = (
                await client.post("/api/v1/conversations", json={})
            ).json()["id"]
            sent = await client.post(
                f"/api/v1/conversations/{conversation_id}/messages",
                json={"content": f"请阅读 {DOC_URL} ，用一句话说明这份文档的主题。"},
            )
            assert sent.status_code == 201, sent.text
            payload = sent.json()

    # 模拟重启：同一个数据库文件，全新的应用实例
    second_app = create_app(settings)
    async with second_app.router.lifespan_context(second_app):
        transport = httpx.ASGITransport(app=second_app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
            timeout=httpx.Timeout(300),
        ) as client:
            conversations = (await client.get("/api/v1/conversations")).json()
            history = (
                await client.get(f"/api/v1/conversations/{conversation_id}/messages")
            ).json()
            run = (await client.get(f"/api/v1/runs/{payload['run_id']}")).json()

    assert any(item["id"] == conversation_id for item in conversations)
    assert [message["role"] for message in history] == ["user", "assistant"]
    assert history[1]["content"] == payload["message"]["content"]
    assert history[1]["sources"][0]["url"] == payload["sources"][0]["url"]
    assert run["status"] == "completed"
    assert run["tool_calls"][0]["tool_name"] == "fetch_document"
