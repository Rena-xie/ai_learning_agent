"""工具注册表单元测试：未注册工具、参数校验与调用上限。"""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from learning_agent.agent.tool_registry import (
    ToolExecutionState,
    ToolRegistry,
    _summarize_arguments,
)
from learning_agent.domain.contracts import ToolContext
from learning_agent.domain.entities import ToolOutcome
from learning_agent.domain.errors import AppError, ErrorCode


class EchoArgs(BaseModel):
    text: str


class EchoTool:
    """测试用工具：回显参数，不产生副作用。"""

    name = "echo"
    description = "回显输入文本"
    args_model = EchoArgs
    parameters_schema = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
        "additionalProperties": False,
    }

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def __call__(self, args: EchoArgs, context: ToolContext) -> ToolOutcome:
        self.calls.append(args.text)
        return ToolOutcome(
            ok=True,
            model_payload={"status": "ok", "text": args.text},
            result_summary={"status": "ok", "chars": len(args.text)},
        )


class NoopAllocator:
    def __init__(self) -> None:
        self.counter = 0

    def next_source_id(self) -> str:
        self.counter += 1
        return f"S{self.counter}"


def make_context() -> ToolContext:
    return ToolContext(
        run_id="run-1", conversation_id="conv-1", allocator=NoopAllocator()
    )


def test_schemas_expose_registered_tools_only() -> None:
    registry = ToolRegistry([EchoTool()])
    schemas = registry.schemas()
    assert [item["function"]["name"] for item in schemas] == ["echo"]


async def test_rejects_unregistered_tool_without_execution() -> None:
    registry = ToolRegistry([EchoTool()])
    result = await registry.execute(
        tool_call_id="tc-1",
        name="fetch_document",
        arguments='{"url": "https://docs.example.com"}',
        context=make_context(),
        state=ToolExecutionState(max_calls=3),
    )
    assert result.ok is False
    assert result.error_code == "unknown_tool"
    assert result.status.value == "rejected"


@pytest.mark.parametrize(
    "arguments",
    ["not-json", '{"text": 123}', "[]", '{"wrong": "field"}'],
)
async def test_rejects_invalid_arguments(arguments: str) -> None:
    tool = EchoTool()
    registry = ToolRegistry([tool])
    result = await registry.execute(
        tool_call_id="tc-2",
        name="echo",
        arguments=arguments,
        context=make_context(),
        state=ToolExecutionState(max_calls=3),
    )
    assert result.ok is False
    assert result.error_code == str(ErrorCode.INVALID_REQUEST)
    assert tool.calls == []


async def test_executes_registered_tool_and_returns_summary() -> None:
    tool = EchoTool()
    registry = ToolRegistry([tool])
    result = await registry.execute(
        tool_call_id="tc-3",
        name="echo",
        arguments='{"text": "你好"}',
        context=make_context(),
        state=ToolExecutionState(max_calls=3),
    )
    assert result.ok is True
    assert result.status.value == "succeeded"
    assert result.model_payload["text"] == "你好"
    assert tool.calls == ["你好"]
    assert result.duration_ms >= 0


async def test_enforces_call_limit() -> None:
    registry = ToolRegistry([EchoTool()])
    state = ToolExecutionState(max_calls=1)
    await registry.execute(
        tool_call_id="tc-4",
        name="echo",
        arguments='{"text": "a"}',
        context=make_context(),
        state=state,
    )
    with pytest.raises(AppError) as excinfo:
        await registry.execute(
            tool_call_id="tc-5",
            name="echo",
            arguments='{"text": "b"}',
            context=make_context(),
            state=state,
        )
    assert excinfo.value.code is ErrorCode.TOOL_LIMIT_EXCEEDED


def test_argument_summary_redacts_nested_urls_and_invalid_json() -> None:
    summary = _summarize_arguments(
        '{"urls": ["https://example.com/doc?token=DEMO_SECRET_123"],'
        ' "nested": {"signature": "DEMO_SIGNATURE_456"}}'
    )

    assert "DEMO_SECRET_123" not in summary
    assert "DEMO_SIGNATURE_456" not in summary
    assert "***" in summary
    assert _summarize_arguments("not-json token=DEMO_SECRET_789") == "<invalid-json>"
