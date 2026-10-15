"""模型故障处理测试：真实错误路径 + 本地可控端点验证重试与错误映射。

说明：涉及 api.deepseek.com 的用例使用真实错误（错误密钥、不存在的模型名），
只验证错误映射，不产生 token 费用；重试次数与超时用本地可控 HTTP 端点验证，
因为需要确定性的状态码序列。
"""

from __future__ import annotations

import json

import pytest
from tests.support.local_doc_server import LocalDocServer, Route

from learning_agent.config import Settings
from learning_agent.domain.errors import AppError, ErrorCode
from learning_agent.models.base import ChatMessage
from learning_agent.models.provider_adapter import OpenAICompatibleModelClient

_COMPLETION_BODY = json.dumps(
    {
        "id": "chatcmpl-local-1",
        "object": "chat.completion",
        "created": 0,
        "model": "test-model",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "本地端点回答"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
    }
).encode("utf-8")

_MESSAGES = [ChatMessage(role="user", content="你好")]

_EMPTY_CHOICES_BODY = json.dumps(
    {
        "id": "chatcmpl-empty",
        "object": "chat.completion",
        "created": 0,
        "model": "test-model",
        "choices": [],
        "usage": {"prompt_tokens": 3, "completion_tokens": 0, "total_tokens": 3},
    }
).encode("utf-8")

_CONTEXT_ERROR_BODY = json.dumps(
    {
        "error": {
            "message": (
                "This model's maximum context length is 65536 tokens. "
                "However, your messages resulted in 70000 tokens. "
                "Please reduce the length of the messages."
            ),
            "type": "invalid_request_error",
            "code": "context_length_exceeded",
        }
    }
).encode("utf-8")


def deepseek_client(
    settings: Settings, *, api_key: str, model_name: str, max_retries: int = 0
) -> OpenAICompatibleModelClient:
    return OpenAICompatibleModelClient(
        api_key=api_key,
        base_url=settings.model_base_url,
        model_name=model_name,
        max_retries=max_retries,
    )


@pytest.mark.real_model
async def test_invalid_api_key_maps_to_model_error() -> None:
    settings = Settings()
    client = deepseek_client(
        settings, api_key="sk-invalid-for-test", model_name=settings.model_name
    )
    try:
        with pytest.raises(AppError) as excinfo:
            await client.complete(
                messages=_MESSAGES,
                tools=[],
                max_output_tokens=16,
                timeout_seconds=30,
            )
    finally:
        await client.aclose()
    assert excinfo.value.code is ErrorCode.MODEL_ERROR
    assert "HTTP" in excinfo.value.message


@pytest.mark.real_model
async def test_unknown_model_name_maps_to_model_error() -> None:
    settings = Settings()
    client = deepseek_client(
        settings, api_key=settings.model_api_key, model_name="definitely-not-a-model"
    )
    try:
        with pytest.raises(AppError) as excinfo:
            await client.complete(
                messages=_MESSAGES,
                tools=[],
                max_output_tokens=16,
                timeout_seconds=30,
            )
    finally:
        await client.aclose()
    assert excinfo.value.code is ErrorCode.MODEL_ERROR


async def test_rate_limit_is_retried_then_succeeds(
    local_server: LocalDocServer,
) -> None:
    """本地可控端点：先返回两次 429，再返回正常补全，验证有限重试与退避。"""

    path = "/v1/chat/completions"
    local_server.add_post_sequence(
        path,
        [
            Route(
                status=429, content_type="application/json", body=b'{"error":"rate"}'
            ),
            Route(
                status=429, content_type="application/json", body=b'{"error":"rate"}'
            ),
            Route(status=200, content_type="application/json", body=_COMPLETION_BODY),
        ],
    )
    client = OpenAICompatibleModelClient(
        api_key="test-key",
        base_url=f"http://127.0.0.1:{local_server.port}/v1",
        model_name="test-model",
        max_retries=2,
        backoff_seconds=0.01,
    )
    try:
        response = await client.complete(
            messages=_MESSAGES, tools=[], max_output_tokens=16, timeout_seconds=10
        )
    finally:
        await client.aclose()

    assert response.message.content == "本地端点回答"
    assert response.usage.input_tokens == 3
    assert local_server.requests.count(("POST", path)) == 3


async def test_rate_limit_exhausts_retries_and_maps_error(
    local_server: LocalDocServer,
) -> None:
    path = "/v1/chat/completions"
    local_server.add_post_sequence(
        path,
        [Route(status=429, content_type="application/json", body=b'{"error":"rate"}')],
    )
    client = OpenAICompatibleModelClient(
        api_key="test-key",
        base_url=f"http://127.0.0.1:{local_server.port}/v1",
        model_name="test-model",
        max_retries=2,
        backoff_seconds=0.01,
    )
    try:
        with pytest.raises(AppError) as excinfo:
            await client.complete(
                messages=_MESSAGES, tools=[], max_output_tokens=16, timeout_seconds=10
            )
    finally:
        await client.aclose()
    assert excinfo.value.code is ErrorCode.MODEL_RATE_LIMITED
    assert local_server.requests.count(("POST", path)) == 3  # 初次 + 2 次重试


async def test_model_timeout_maps_to_timeout(local_server: LocalDocServer) -> None:
    path = "/v1/chat/completions"
    local_server.add_post_sequence(
        path,
        [
            Route(
                status=200,
                content_type="application/json",
                body=_COMPLETION_BODY,
                delay_seconds=2.0,
            )
        ],
    )
    client = OpenAICompatibleModelClient(
        api_key="test-key",
        base_url=f"http://127.0.0.1:{local_server.port}/v1",
        model_name="test-model",
        max_retries=0,
    )
    try:
        with pytest.raises(AppError) as excinfo:
            await client.complete(
                messages=_MESSAGES, tools=[], max_output_tokens=16, timeout_seconds=0.3
            )
    finally:
        await client.aclose()
    assert excinfo.value.code is ErrorCode.MODEL_TIMEOUT


async def test_unreachable_endpoint_maps_to_model_error() -> None:
    client = OpenAICompatibleModelClient(
        api_key="test-key",
        base_url="http://127.0.0.1:9/v1",
        model_name="test-model",
        max_retries=0,
    )
    try:
        with pytest.raises(AppError) as excinfo:
            await client.complete(
                messages=_MESSAGES, tools=[], max_output_tokens=16, timeout_seconds=5
            )
    finally:
        await client.aclose()
    assert excinfo.value.code is ErrorCode.MODEL_ERROR


async def test_empty_choices_maps_to_model_error(
    local_server: LocalDocServer,
) -> None:
    """本地可控端点：200 但 choices 为空时映射为 model_error，而非内部错误。"""

    path = "/v1/chat/completions"
    local_server.add_post_sequence(
        path,
        [
            Route(
                status=200,
                content_type="application/json",
                body=_EMPTY_CHOICES_BODY,
            )
        ],
    )
    client = OpenAICompatibleModelClient(
        api_key="test-key",
        base_url=f"http://127.0.0.1:{local_server.port}/v1",
        model_name="test-model",
        max_retries=0,
    )
    try:
        with pytest.raises(AppError) as excinfo:
            await client.complete(
                messages=_MESSAGES, tools=[], max_output_tokens=16, timeout_seconds=10
            )
    finally:
        await client.aclose()
    assert excinfo.value.code is ErrorCode.MODEL_ERROR


async def test_context_length_error_maps_to_context_exceeded(
    local_server: LocalDocServer,
) -> None:
    """本地可控端点：400 + 上下文超限正文映射为 model_context_exceeded。"""

    path = "/v1/chat/completions"
    local_server.add_post_sequence(
        path,
        [
            Route(
                status=400,
                content_type="application/json",
                body=_CONTEXT_ERROR_BODY,
            )
        ],
    )
    client = OpenAICompatibleModelClient(
        api_key="test-key",
        base_url=f"http://127.0.0.1:{local_server.port}/v1",
        model_name="test-model",
        max_retries=0,
    )
    try:
        with pytest.raises(AppError) as excinfo:
            await client.complete(
                messages=_MESSAGES, tools=[], max_output_tokens=16, timeout_seconds=10
            )
    finally:
        await client.aclose()
    assert excinfo.value.code is ErrorCode.MODEL_CONTEXT_EXCEEDED
