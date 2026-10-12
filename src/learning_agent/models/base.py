"""模型客户端契约：与供应商解耦的消息、工具调用与用量结构。"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from learning_agent.domain.entities import UsageRecord


@dataclass(frozen=True)
class ToolCallRequest:
    """模型发起的工具调用请求（参数为原始 JSON 字符串，未经信任）。"""

    id: str
    name: str
    arguments: str


@dataclass(frozen=True)
class ChatMessage:
    """对话消息；覆盖 system / user / assistant / tool 四种角色。"""

    role: str
    content: str | None = None
    tool_calls: tuple[ToolCallRequest, ...] = ()
    tool_call_id: str | None = None

    def to_api_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"role": self.role, "content": self.content or ""}
        if self.tool_calls:
            payload["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": call.name, "arguments": call.arguments},
                }
                for call in self.tool_calls
            ]
        if self.tool_call_id is not None:
            payload["tool_call_id"] = self.tool_call_id
        return payload


@dataclass(frozen=True)
class ModelResponse:
    """一次模型调用的结果。"""

    message: ChatMessage
    usage: UsageRecord
    model_name: str | None = None
    provider_request_id: str | None = None


@runtime_checkable
class ModelClient(Protocol):
    """模型客户端协议：只暴露 Runtime 需要的最小能力。"""

    async def complete(
        self,
        *,
        messages: Sequence[ChatMessage],
        tools: Sequence[dict[str, Any]],
        max_output_tokens: int,
        timeout_seconds: float,
    ) -> ModelResponse:
        """提交一次对话补全请求。"""

        ...
