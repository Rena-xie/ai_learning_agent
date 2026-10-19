"""模型上下文过滤：入库通知不应进入下一次运行的历史。"""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.support.scripted_model import ScriptedModelClient, final_response

from learning_agent.agent.context_builder import ContextBuilder
from learning_agent.agent.runtime import AgentRuntime
from learning_agent.agent.tool_registry import ToolRegistry
from learning_agent.domain.enums import MessageRole
from learning_agent.persistence.async_repositories import AsyncRepositories
from learning_agent.persistence.database import build_engine, build_session_factory
from learning_agent.persistence.migrations import apply_migrations
from learning_agent.persistence.repositories import Repositories


@pytest.fixture
def repositories(tmp_path: Path):
    url = f"sqlite:///{(tmp_path / 'history.db').as_posix()}"
    apply_migrations(url)
    engine = build_engine(url)
    yield Repositories(build_session_factory(engine))
    engine.dispose()


def build_runtime(
    model: ScriptedModelClient, repositories: Repositories
) -> AgentRuntime:
    return AgentRuntime(
        model_client=model,
        tools=ToolRegistry([]),
        context_builder=ContextBuilder(
            max_tokens=4_000,
            max_history_messages=10,
            output_reserve_tokens=500,
            tool_schema_reserve_tokens=0,
        ),
        repositories=AsyncRepositories(repositories),
        model_timeout_seconds=2.0,
        run_timeout_seconds=5.0,
        max_model_rounds=1,
        max_tool_calls=1,
        max_output_tokens=200,
        max_tool_result_chars=2_000,
    )


async def test_ingestion_notice_is_not_sent_to_model(
    repositories: Repositories,
) -> None:
    conversation = repositories.create_conversation(title="历史过滤")
    repositories.add_message(
        conversation_id=conversation.id,
        role=MessageRole.USER.value,
        content="请解释依赖注入",
    )
    repositories.add_message(
        conversation_id=conversation.id,
        role=MessageRole.ASSISTANT.value,
        content="依赖注入是……",
    )
    repositories.add_ingestion_notice(
        conversation_id=conversation.id,
        content="入库完成：已收藏 1 篇文档",
    )
    model = ScriptedModelClient([final_response("好的")])
    runtime = build_runtime(model, repositories)

    await runtime.run(conversation_id=conversation.id, user_message="继续")

    contents = [message.content for message in model.seen_messages[0]]
    assert "入库完成：已收藏 1 篇文档" not in contents
    assert "请解释依赖注入" in contents
    assert "依赖注入是……" in contents
