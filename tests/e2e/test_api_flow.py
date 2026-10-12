"""端到端测试：真实模型 + 真实公网文档，通过 HTTP API 验证完整链路。"""

from __future__ import annotations

import httpx
import pytest

from learning_agent.config import Settings
from learning_agent.main import create_app

pytestmark = pytest.mark.real_model

DOC_URL = "https://liaoxuefeng.com/books/python/introduction/index.html"
TIMEOUT = httpx.Timeout(300.0)


def e2e_settings(tmp_path) -> Settings:
    """使用 .env 的真实模型配置，只把数据库指向临时目录。"""

    return Settings().model_copy(
        update={"database_url": f"sqlite:///{(tmp_path / 'e2e.db').as_posix()}"}
    )


async def test_full_flow_with_real_document(tmp_path) -> None:
    app = create_app(e2e_settings(tmp_path))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver", timeout=TIMEOUT
        ) as client:
            assert (await client.get("/health/live")).status_code == 200
            ready = await client.get("/health/ready")
            assert ready.json()["status"] == "ok"

            created = await client.post("/api/v1/conversations", json={})
            assert created.status_code == 201
            conversation_id = created.json()["id"]

            response = await client.post(
                f"/api/v1/conversations/{conversation_id}/messages",
                json={
                    "content": (
                        f"请阅读 {DOC_URL} 这份 Python 入门文档，"
                        "用三点解释其中的核心概念。"
                    )
                },
            )
            assert response.status_code == 201, response.text
            payload = response.json()
            assert payload["status"] == "completed"
            assert payload["sources"], "回答必须包含真实来源"
            assert payload["sources"][0]["url"].startswith("https://liaoxuefeng.com/")
            assert "[S1]" in payload["message"]["content"]

            run = await client.get(f"/api/v1/runs/{payload['run_id']}")
            run_json = run.json()
            assert run_json["status"] == "completed"
            assert run_json["tool_calls"][0]["tool_name"] == "fetch_document"
            assert run_json["tool_calls"][0]["status"] == "succeeded"
            assert run_json["input_tokens"] and run_json["output_tokens"]

            history = await client.get(
                f"/api/v1/conversations/{conversation_id}/messages"
            )
            messages = history.json()
            assert [message["role"] for message in messages] == ["user", "assistant"]
            assert messages[1]["sources"][0]["source_id"] == "S1"


async def test_disallowed_domain_is_reported_honestly(tmp_path) -> None:
    app = create_app(e2e_settings(tmp_path))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver", timeout=TIMEOUT
        ) as client:
            conversation_id = (
                await client.post("/api/v1/conversations", json={})
            ).json()["id"]
            response = await client.post(
                f"/api/v1/conversations/{conversation_id}/messages",
                json={"content": "请阅读 https://example.com/knowledge 并解释内容。"},
            )
            assert response.status_code == 201, response.text
            payload = response.json()
            run_json = (await client.get(f"/api/v1/runs/{payload['run_id']}")).json()
            tool_calls = run_json["tool_calls"]
            assert tool_calls and tool_calls[0]["status"] in {"failed", "rejected"}
            assert tool_calls[0]["error_code"] == "url_not_allowed"
            assert payload["sources"] == [], "失败时不得返回伪造来源"
