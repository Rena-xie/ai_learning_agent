"""运行最终结果落库失败时，必须把活动 Run 结束为失败。"""

from __future__ import annotations

from typing import Any

from tests.support.scripted_model import ScriptedModelClient, final_response

from learning_agent.agent.context_builder import ContextBuilder
from learning_agent.agent.runtime import AgentRuntime
from learning_agent.agent.tool_registry import ToolRegistry
from learning_agent.domain.enums import RunStatus
from learning_agent.domain.errors import ErrorCode, PersistenceError


class FailingCompleteRepository:
    def __init__(self) -> None:
        self.finish_args: dict[str, Any] | None = None

    async def list_messages(self, conversation_id: str) -> list[object]:
        return []

    async def list_snapshots(
        self, conversation_id: str, *, provider: str | None = None
    ) -> list[object]:
        return []

    async def complete_run(self, **kwargs: object) -> None:
        raise PersistenceError("最终结果保存失败")

    async def finish_run(self, **kwargs: object) -> None:
        self.finish_args = dict(kwargs)


async def test_completion_persistence_failure_finishes_run() -> None:
    repositories = FailingCompleteRepository()
    runtime = AgentRuntime(
        model_client=ScriptedModelClient([final_response("回答")]),
        tools=ToolRegistry([]),
        context_builder=ContextBuilder(
            max_tokens=1_000,
            max_history_messages=5,
            output_reserve_tokens=100,
            tool_schema_reserve_tokens=0,
        ),
        repositories=repositories,
        model_timeout_seconds=1.0,
        run_timeout_seconds=2.0,
        max_model_rounds=1,
        max_tool_calls=1,
        max_output_tokens=100,
        max_tool_result_chars=1_000,
    )

    outcome = await runtime.resume(
        run_id="run-1",
        conversation_id="conversation-1",
        user_message="问题",
    )

    assert outcome.status is RunStatus.FAILED
    assert outcome.error_code == ErrorCode.PERSISTENCE_ERROR.value
    assert repositories.finish_args is not None
    assert repositories.finish_args["status"] is RunStatus.FAILED
