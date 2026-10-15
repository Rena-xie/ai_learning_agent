"""AsyncRepositories 门面：异步委派与合并事务的正确性。

本测试针对门面本身（同步 Repositories 作为内层），不依赖真实模型与网络。
"""

from __future__ import annotations

import asyncio

import pytest

from learning_agent.domain.entities import UsageRecord
from learning_agent.domain.enums import RunStatus
from learning_agent.domain.errors import AppError, ErrorCode
from learning_agent.persistence.async_repositories import AsyncRepositories
from learning_agent.persistence.database import build_engine, build_session_factory
from learning_agent.persistence.migrations import apply_migrations
from learning_agent.persistence.repositories import Repositories


@pytest.fixture
def repositories(tmp_path) -> AsyncRepositories:
    database_url = f"sqlite:///{(tmp_path / 'async-repos.db').as_posix()}"
    apply_migrations(database_url)
    engine = build_engine(database_url)
    return AsyncRepositories(Repositories(build_session_factory(engine)))


async def test_facade_delegates_conversation_crud(
    repositories: AsyncRepositories,
) -> None:
    created = await repositories.create_conversation(title="异步门面")
    fetched = await repositories.get_conversation(created.id)
    assert fetched is not None
    assert fetched.title == "异步门面"
    listed = await repositories.list_conversations()
    assert [item.id for item in listed] == [created.id]
    assert await repositories.delete_conversation(created.id) is True


async def test_start_and_complete_run_persist_in_single_step(
    repositories: AsyncRepositories,
) -> None:
    conversation = await repositories.create_conversation(title="合并事务")
    await repositories.start_run(
        run_id="run-1",
        conversation_id=conversation.id,
        user_message="请阅读文档",
        model_name="test-model",
        prompt_version="v1",
    )
    run = await repositories.get_run("run-1")
    assert run is not None
    assert run.status == RunStatus.RUNNING.value
    messages = await repositories.list_messages(conversation.id)
    assert [message.role for message in messages] == ["user"]

    await repositories.complete_run(
        run_id="run-1",
        conversation_id=conversation.id,
        content="结论如下 [S1]。",
        usage=UsageRecord(input_tokens=10, output_tokens=5),
        model_name="test-model",
        duration_ms=123,
    )
    run = await repositories.get_run("run-1")
    assert run is not None
    assert run.status == RunStatus.COMPLETED.value
    assert run.duration_ms == 123
    assert run.input_tokens == 10
    messages = await repositories.list_messages(conversation.id)
    assert [message.role for message in messages] == ["user", "assistant"]
    assert await repositories.get_active_run(conversation.id) is None


async def test_second_start_run_is_rejected(repositories: AsyncRepositories) -> None:
    conversation = await repositories.create_conversation(title="冲突")
    await repositories.start_run(
        run_id="run-a",
        conversation_id=conversation.id,
        user_message="第一条",
        model_name="test-model",
        prompt_version="v1",
    )
    with pytest.raises(AppError) as excinfo:
        await repositories.start_run(
            run_id="run-b",
            conversation_id=conversation.id,
            user_message="第二条",
            model_name="test-model",
            prompt_version="v1",
        )
    assert excinfo.value.code is ErrorCode.RUN_IN_PROGRESS
    messages = await repositories.list_messages(conversation.id)
    assert [message.content for message in messages] == ["第一条"]


async def test_concurrent_facade_calls_do_not_fail(
    repositories: AsyncRepositories,
) -> None:
    await repositories.create_conversation(title="并发读")
    results = await asyncio.gather(
        *(repositories.list_conversations() for _ in range(8))
    )
    assert all(len(items) == 1 for items in results)
