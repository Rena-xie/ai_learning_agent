"""Repository 集成测试：真实 SQLite 文件库上的保存、读取、约束与删除。"""

from __future__ import annotations

import pytest

from learning_agent.domain.entities import DocumentContent, Section, UsageRecord
from learning_agent.domain.enums import RunStatus
from learning_agent.domain.errors import AppError, ErrorCode
from learning_agent.persistence.database import (
    build_engine,
    build_session_factory,
    init_database,
)
from learning_agent.persistence.repositories import Repositories


@pytest.fixture
def repositories(tmp_path) -> Repositories:
    engine = build_engine(f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    init_database(engine)
    return Repositories(build_session_factory(engine))


def make_content(url: str = "https://docs.example.com/guide") -> DocumentContent:
    from datetime import UTC, datetime

    return DocumentContent(
        title="示例指南",
        canonical_url=url,
        fetched_at=datetime.now(UTC),
        content_hash="a" * 64,
        content_text="正文内容",
        sections=(Section(title="核心概念", level=1, text="正文内容"),),
        truncated=False,
        warnings=("示例警告",),
    )


def test_message_roundtrip_and_default_title(tmp_path, repositories) -> None:
    conversation = repositories.create_conversation(title="新对话")
    repositories.add_message(
        conversation_id=conversation.id, role="user", content="请解释这个文档"
    )
    repositories.set_title_if_default(conversation.id, title="请解释这个文档")
    messages = repositories.list_messages(conversation.id)
    assert [message.content for message in messages] == ["请解释这个文档"]
    assert messages[0].role == "user"
    assert repositories.get_conversation(conversation.id).title == "请解释这个文档"


def test_messages_are_isolated_between_conversations(repositories) -> None:
    first = repositories.create_conversation(title="对话一")
    second = repositories.create_conversation(title="对话二")
    repositories.add_message(
        conversation_id=first.id, role="user", content="只属于对话一"
    )
    assert repositories.list_messages(first.id)[0].content == "只属于对话一"
    assert repositories.list_messages(second.id) == []


def test_single_active_run_per_conversation(repositories) -> None:
    conversation = repositories.create_conversation(title="并发约束")
    repositories.create_run(
        run_id="run-1",
        conversation_id=conversation.id,
        model_name="test-model",
        prompt_version="v1",
    )
    with pytest.raises(AppError) as excinfo:
        repositories.create_run(
            run_id="run-2",
            conversation_id=conversation.id,
            model_name="test-model",
            prompt_version="v1",
        )
    assert excinfo.value.code is ErrorCode.RUN_IN_PROGRESS
    assert repositories.get_active_run(conversation.id).id == "run-1"

    repositories.finish_run(
        run_id="run-1",
        status=RunStatus.COMPLETED,
        usage=UsageRecord(input_tokens=10, output_tokens=5),
        duration_ms=123,
    )
    assert repositories.get_active_run(conversation.id) is None
    repositories.create_run(
        run_id="run-3",
        conversation_id=conversation.id,
        model_name="test-model",
        prompt_version="v1",
    )
    run = repositories.get_run("run-1")
    assert run.status == "completed"
    assert run.input_tokens == 10
    assert run.duration_ms == 123


def test_snapshot_and_run_source_link(repositories) -> None:
    conversation = repositories.create_conversation(title="来源关联")
    repositories.create_run(
        run_id="run-1",
        conversation_id=conversation.id,
        model_name="test-model",
        prompt_version="v1",
    )
    snapshot_id = repositories.add_snapshot(
        conversation_id=conversation.id, content=make_content(), parse_status="ok"
    )
    repositories.link_run_source(
        run_id="run-1", snapshot_id=snapshot_id, source_id="S1"
    )
    repositories.link_run_source(
        run_id="run-1", snapshot_id=snapshot_id, source_id="S1"
    )
    snapshots = repositories.list_snapshots(conversation.id)
    assert len(snapshots) == 1
    assert snapshots[0].sections[0].title == "核心概念"
    assert snapshots[0].warnings == ("示例警告",)
    run = repositories.get_run("run-1")
    assert [source.source_id for source in run.sources] == ["S1"]
    assert run.sources[0].canonical_url.endswith("/guide")


def test_delete_conversation_cascades(repositories) -> None:
    keep = repositories.create_conversation(title="保留")
    doomed = repositories.create_conversation(title="删除")
    repositories.add_message(conversation_id=doomed.id, role="user", content="将被删除")
    repositories.create_run(
        run_id="run-x",
        conversation_id=doomed.id,
        model_name="test-model",
        prompt_version="v1",
    )
    snapshot_id = repositories.add_snapshot(
        conversation_id=doomed.id, content=make_content(), parse_status="ok"
    )
    repositories.link_run_source(
        run_id="run-x", snapshot_id=snapshot_id, source_id="S1"
    )

    assert repositories.delete_conversation(doomed.id) is True
    assert repositories.get_conversation(doomed.id) is None
    assert repositories.list_messages(doomed.id) == []
    assert repositories.list_snapshots(doomed.id) == []
    assert repositories.get_run("run-x") is None
    assert repositories.get_conversation(keep.id) is not None


def test_add_message_to_missing_conversation_is_rejected(repositories) -> None:
    with pytest.raises(AppError) as excinfo:
        repositories.add_message(
            conversation_id="00000000-0000-0000-0000-000000000000",
            role="user",
            content="x",
        )
    assert excinfo.value.code is ErrorCode.NOT_FOUND
