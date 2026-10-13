"""脚本化模型客户端：仅用于验证 Runtime 的循环与资源上限（非端到端替身）。

端到端链路（真实模型 + 真实文档）由 tests/e2e 与
tests/integration/test_runtime_real_model.py 覆盖；
本替身只用来确定性地触发「轮数上限 / 工具次数上限 / 总时限」等边界。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from typing import Any
from uuid import uuid4

from learning_agent.domain.entities import UsageRecord
from learning_agent.models.base import ChatMessage, ModelResponse, ToolCallRequest

ResponseFactory = ModelResponse | Callable[[], ModelResponse]


class ScriptedModelClient:
    """按脚本依次返回响应；脚本耗尽即报错（说明 Runtime 调用了超出预期的轮数）。"""

    def __init__(
        self,
        responses: Sequence[ResponseFactory],
        *,
        delay_seconds: float = 0.0,
    ) -> None:
        self._responses = list(responses)
        self._delay_seconds = delay_seconds
        self.calls = 0
        self.model_name = "scripted-model"

    async def complete(
        self,
        *,
        messages: Sequence[ChatMessage],
        tools: Sequence[dict[str, Any]],
        max_output_tokens: int,
        timeout_seconds: float,
    ) -> ModelResponse:
        if self._delay_seconds:
            await asyncio.sleep(self._delay_seconds)
        index = self.calls
        self.calls += 1
        if index >= len(self._responses):
            raise AssertionError(
                f"脚本响应不足：Runtime 第 {index + 1} 次调用模型超出预期"
            )
        response = self._responses[index]
        return response() if callable(response) else response

    async def aclose(self) -> None:
        return None


def tool_call_response(
    *calls: tuple[str, str], call_ids: Sequence[str] | None = None
) -> ModelResponse:
    """构造请求工具调用的模型响应：参数为 (工具名, 参数 JSON)。"""

    identifiers = (
        list(call_ids)
        if call_ids
        else [f"call-{uuid4().hex[:8]}" for _ in range(len(calls))]
    )
    requests = tuple(
        ToolCallRequest(id=call_id, name=name, arguments=arguments)
        for call_id, (name, arguments) in zip(identifiers, calls, strict=True)
    )
    return ModelResponse(
        message=ChatMessage(role="assistant", content=None, tool_calls=requests),
        usage=UsageRecord(input_tokens=10, output_tokens=5),
        model_name="scripted-model",
    )


def final_response(text: str) -> ModelResponse:
    return ModelResponse(
        message=ChatMessage(role="assistant", content=text),
        usage=UsageRecord(input_tokens=10, output_tokens=5),
        model_name="scripted-model",
    )
