"""飞书 Webhook 客户端测试。"""

from __future__ import annotations

import json

import httpx
import pytest

from learning_agent.digest.feishu import FeishuNotifier


@pytest.mark.asyncio
async def test_send_text_with_signature() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"code": 0})

    notifier = FeishuNotifier(
        webhook_url="https://feishu.example/webhook",
        secret="test-secret",
        transport=httpx.MockTransport(handler),
    )
    try:
        result = await notifier.send("AI 日报")
    finally:
        await notifier.close()

    assert result.status == "delivered"
    body = captured["body"]
    assert body["msg_type"] == "text"
    assert body["content"] == {"text": "AI 日报"}
    assert body["sign"]
    assert body["timestamp"]


@pytest.mark.asyncio
async def test_retries_after_server_error() -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(500)
        return httpx.Response(200, json={"code": 0})

    notifier = FeishuNotifier(
        webhook_url="https://feishu.example/webhook",
        max_retries=1,
        transport=httpx.MockTransport(handler),
    )
    try:
        result = await notifier.send("AI 日报")
    finally:
        await notifier.close()

    assert result.status == "delivered"
    assert result.attempts == 2
