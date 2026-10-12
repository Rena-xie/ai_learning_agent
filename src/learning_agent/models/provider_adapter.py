"""OpenAI 兼容协议适配器（默认指向 DeepSeek），封装工具调用与错误映射。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from typing import Any

import openai
from openai import AsyncOpenAI

from learning_agent.domain.entities import UsageRecord
from learning_agent.domain.errors import ErrorCode, ModelError
from learning_agent.models.base import ChatMessage, ModelResponse, ToolCallRequest

logger = logging.getLogger(__name__)

_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


class OpenAICompatibleModelClient:
    """通过 OpenAI 兼容接口调用单一模型供应商。

    - 只实现一个主供应商（DeepSeek），不做多供应商路由；
    - 自行实现有限重试与退避，仅重试超时、连接失败、限流与 5xx；
    - 统一把 SDK 异常映射为 :class:`ModelError`，不把原始堆栈交给上层。
    """

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model_name: str,
        max_retries: int = 2,
        backoff_seconds: float = 0.5,
    ) -> None:
        self._model_name = model_name
        self._max_retries = max_retries
        self._backoff_seconds = backoff_seconds
        self._client = AsyncOpenAI(
            api_key=api_key, base_url=base_url, max_retries=0, timeout=None
        )

    @property
    def model_name(self) -> str:
        return self._model_name

    async def aclose(self) -> None:
        await self._client.close()

    async def complete(
        self,
        *,
        messages: Sequence[ChatMessage],
        tools: Sequence[dict[str, Any]],
        max_output_tokens: int,
        timeout_seconds: float,
    ) -> ModelResponse:
        request: dict[str, Any] = {
            "model": self._model_name,
            "messages": [message.to_api_payload() for message in messages],
            "max_tokens": max_output_tokens,
        }
        if tools:
            request["tools"] = list(tools)
            request["tool_choice"] = "auto"

        last_error: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                response = await self._client.chat.completions.create(
                    **request, timeout=timeout_seconds
                )
            except Exception as exc:  # noqa: BLE001 - 统一映射后再抛出
                mapped = _map_error(exc)
                last_error = mapped
                if not _is_retryable(exc) or attempt >= self._max_retries:
                    raise mapped from exc
                delay = self._backoff_seconds * (2**attempt)
                logger.warning(
                    "model_call_retry",
                    extra={
                        "extra_fields": {
                            "attempt": attempt + 1,
                            "error_code": str(mapped.code),
                            "delay_seconds": delay,
                        }
                    },
                )
                await asyncio.sleep(delay)
                continue
            return _parse_response(response, fallback_model=self._model_name)

        raise last_error or ModelError(ErrorCode.MODEL_ERROR, "模型调用失败")


def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, (openai.APITimeoutError, openai.APIConnectionError)):
        return True
    if isinstance(exc, openai.APIStatusError):
        return exc.status_code in _RETRYABLE_STATUS
    return False


def _map_error(exc: Exception) -> ModelError:
    if isinstance(exc, openai.APITimeoutError):
        return ModelError(ErrorCode.MODEL_TIMEOUT, "模型响应超时，请稍后重试")
    if isinstance(exc, openai.RateLimitError):
        return ModelError(ErrorCode.MODEL_RATE_LIMITED, "模型服务当前限流，请稍后重试")
    if isinstance(exc, openai.APIConnectionError):
        return ModelError(
            ErrorCode.MODEL_ERROR, "无法连接模型服务，请检查网络与模型配置"
        )
    if isinstance(exc, openai.APIStatusError):
        return ModelError(
            ErrorCode.MODEL_ERROR,
            f"模型服务返回错误（HTTP {exc.status_code}）",
            detail=f"status={exc.status_code}",
        )
    return ModelError(ErrorCode.MODEL_ERROR, "模型调用失败", detail=type(exc).__name__)


def _parse_response(response: Any, *, fallback_model: str) -> ModelResponse:
    choice = response.choices[0]
    message = choice.message
    tool_calls = tuple(
        ToolCallRequest(
            id=call.id,
            name=call.function.name,
            arguments=call.function.arguments or "{}",
        )
        for call in (message.tool_calls or [])
    )
    usage = UsageRecord()
    if response.usage is not None:
        usage = UsageRecord(
            input_tokens=response.usage.prompt_tokens,
            output_tokens=response.usage.completion_tokens,
        )
    return ModelResponse(
        message=ChatMessage(
            role="assistant", content=message.content, tool_calls=tool_calls
        ),
        usage=usage,
        model_name=getattr(response, "model", None) or fallback_model,
        provider_request_id=getattr(response, "id", None),
    )
