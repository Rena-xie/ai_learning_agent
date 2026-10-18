"""工具注册表：静态注册、参数 Schema 校验、调用次数限制与结果脱敏。"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ValidationError

from learning_agent.domain.contracts import ToolContext
from learning_agent.domain.entities import DocumentContent, ToolOutcome, ToolSource
from learning_agent.domain.enums import ToolCallStatus
from learning_agent.domain.errors import AppError, ErrorCode
from learning_agent.observability.logging import redact_url

logger = logging.getLogger(__name__)

# 批量入库允许一次提交多个 URL；8k 仍远低于单次工具结果上限。
_MAX_ARGUMENT_CHARS = 8_000
_MAX_SUMMARY_CHARS = 600


@runtime_checkable
class ToolHandler(Protocol):
    """可注册工具的最小契约。"""

    name: str
    description: str
    parameters_schema: dict[str, object]
    args_model: type[BaseModel]

    async def __call__(self, args: Any, context: ToolContext) -> ToolOutcome:
        """执行工具。"""

        ...


@dataclass
class ToolExecutionState:
    """一次运行内的工具调用计数。"""

    max_calls: int
    calls: int = 0

    def consume(self) -> None:
        """记一次调用；超过上限时终止本次运行。"""

        self.calls += 1
        if self.calls > self.max_calls:
            raise AppError(
                ErrorCode.TOOL_LIMIT_EXCEEDED,
                f"本次运行的工具调用次数超过上限（{self.max_calls} 次），"
                "已停止继续调用工具",
            )


@dataclass(frozen=True)
class ToolExecution:
    """一次工具调用的完整结果。"""

    tool_call_id: str
    tool_name: str
    status: ToolCallStatus
    ok: bool
    model_payload: dict[str, object]
    result_summary: dict[str, object]
    started_at: datetime
    finished_at: datetime
    duration_ms: int
    error_code: str | None = None
    source_id: str | None = None
    content: DocumentContent | None = None
    sources: tuple[ToolSource, ...] = ()


class ToolRegistry:
    """只允许执行静态注册的工具；模型无法扩大工具权限。"""

    def __init__(self, tools: Sequence[ToolHandler]) -> None:
        self._tools: dict[str, ToolHandler] = {}
        for tool in tools:
            if tool.name in self._tools:
                raise ValueError(f"工具重复注册：{tool.name}")
            self._tools[tool.name] = tool

    def schemas(self) -> list[dict[str, Any]]:
        """返回提交给模型的工具 Schema。"""

        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters_schema,
                },
            }
            for tool in self._tools.values()
        ]

    def get(self, name: str) -> ToolHandler | None:
        return self._tools.get(name)

    @property
    def names(self) -> frozenset[str]:
        return frozenset(self._tools)

    async def execute(
        self,
        *,
        tool_call_id: str,
        name: str,
        arguments: str,
        context: ToolContext,
        state: ToolExecutionState,
    ) -> ToolExecution:
        """校验并执行工具调用；未知工具与非法参数不会执行任何副作用。"""

        started_at = datetime.now(UTC)
        started_perf = time.perf_counter()

        def finish(
            *,
            status: ToolCallStatus,
            ok: bool,
            model_payload: dict[str, object],
            result_summary: dict[str, object],
            error_code: str | None = None,
            source_id: str | None = None,
            content: DocumentContent | None = None,
            sources: tuple[ToolSource, ...] = (),
        ) -> ToolExecution:
            finished_at = datetime.now(UTC)
            return ToolExecution(
                tool_call_id=tool_call_id,
                tool_name=name,
                status=status,
                ok=ok,
                model_payload=model_payload,
                result_summary=result_summary,
                started_at=started_at,
                finished_at=finished_at,
                duration_ms=int((time.perf_counter() - started_perf) * 1000),
                error_code=error_code,
                source_id=source_id,
                content=content,
                sources=sources,
            )

        state.consume()
        tool = self.get(name)
        if tool is None:
            logger.warning(
                "tool_not_registered",
                extra={"extra_fields": {"tool_name": name}},
            )
            return finish(
                status=ToolCallStatus.REJECTED,
                ok=False,
                error_code="unknown_tool",
                model_payload={
                    "status": "error",
                    "error_code": "unknown_tool",
                    "message": f"工具 {name} 未注册，无法执行",
                },
                result_summary={"status": "error", "error_code": "unknown_tool"},
            )

        argument_summary = _summarize_arguments(arguments)
        if len(arguments) > _MAX_ARGUMENT_CHARS:
            return finish(
                status=ToolCallStatus.REJECTED,
                ok=False,
                error_code=str(ErrorCode.INVALID_REQUEST),
                model_payload={
                    "status": "error",
                    "error_code": str(ErrorCode.INVALID_REQUEST),
                    "message": "工具参数过长，请减少一次提交的链接数量",
                },
                result_summary={
                    "status": "error",
                    "error_code": str(ErrorCode.INVALID_REQUEST),
                    "arguments": argument_summary,
                },
            )

        try:
            raw = json.loads(arguments or "{}")
        except json.JSONDecodeError:
            raw = None
        if not isinstance(raw, dict):
            return finish(
                status=ToolCallStatus.REJECTED,
                ok=False,
                error_code=str(ErrorCode.INVALID_REQUEST),
                model_payload={
                    "status": "error",
                    "error_code": str(ErrorCode.INVALID_REQUEST),
                    "message": '工具参数必须是 JSON 对象，例如 {"url": "https://..."}',
                },
                result_summary={
                    "status": "error",
                    "error_code": str(ErrorCode.INVALID_REQUEST),
                    "arguments": argument_summary,
                },
            )

        try:
            validated = tool.args_model.model_validate(raw)
        except ValidationError as exc:
            logger.info(
                "tool_arguments_invalid",
                extra={
                    "extra_fields": {
                        "tool_name": name,
                        "error_count": exc.error_count(),
                    }
                },
            )
            return finish(
                status=ToolCallStatus.REJECTED,
                ok=False,
                error_code=str(ErrorCode.INVALID_REQUEST),
                model_payload={
                    "status": "error",
                    "error_code": str(ErrorCode.INVALID_REQUEST),
                    "message": "工具参数不合法，必须提供合法的 url 字段",
                },
                result_summary={
                    "status": "error",
                    "error_code": str(ErrorCode.INVALID_REQUEST),
                    "arguments": argument_summary,
                },
            )

        try:
            outcome = await tool(validated, context)
        except AppError as exc:
            return finish(
                status=ToolCallStatus.FAILED,
                ok=False,
                error_code=str(exc.code),
                model_payload={
                    "status": "error",
                    "error_code": str(exc.code),
                    "message": exc.message,
                },
                result_summary={
                    "status": "error",
                    "error_code": str(exc.code),
                    "arguments": argument_summary,
                },
            )
        except Exception:  # noqa: BLE001 - 未预期异常不暴露给模型
            logger.exception(
                "tool_execution_crashed",
                extra={"extra_fields": {"tool_name": name}},
            )
            return finish(
                status=ToolCallStatus.FAILED,
                ok=False,
                error_code=str(ErrorCode.INTERNAL_ERROR),
                model_payload={
                    "status": "error",
                    "error_code": str(ErrorCode.INTERNAL_ERROR),
                    "message": "工具执行失败，请稍后重试或更换文档地址",
                },
                result_summary={
                    "status": "error",
                    "error_code": str(ErrorCode.INTERNAL_ERROR),
                    "arguments": argument_summary,
                },
            )

        status = ToolCallStatus.SUCCEEDED if outcome.ok else ToolCallStatus.FAILED
        summary = dict(outcome.result_summary)
        summary["arguments"] = argument_summary
        return finish(
            status=status,
            ok=outcome.ok,
            error_code=outcome.error_code,
            model_payload=outcome.model_payload,
            result_summary=summary,
            source_id=outcome.source_id,
            content=outcome.content,
            sources=outcome.sources,
        )


def _summarize_arguments(arguments: str) -> str:
    """生成脱敏、限长的参数摘要（URL 中的敏感查询参数会被遮蔽）。"""

    try:
        raw = json.loads(arguments or "{}")
    except json.JSONDecodeError:
        return arguments[:80]
    if isinstance(raw, dict):
        sanitized = {
            str(key): redact_url(str(value)) if isinstance(value, str) else value
            for key, value in raw.items()
        }
        text = json.dumps(sanitized, ensure_ascii=False)
    else:
        text = arguments
    if len(text) > _MAX_SUMMARY_CHARS:
        return text[:_MAX_SUMMARY_CHARS] + "…"
    return text
