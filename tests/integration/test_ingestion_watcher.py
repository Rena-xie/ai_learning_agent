"""入库通知 watcher（v2 M3）。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select, update

from learning_agent.application.ingestion_watcher import IngestionWatcher
from learning_agent.domain.errors import AppError, ErrorCode
from learning_agent.persistence.async_repositories import AsyncRepositories
from learning_agent.persistence.database import build_engine, build_session_factory
from learning_agent.persistence.migrations import apply_migrations
from learning_agent.persistence.models import IngestionWatch
from learning_agent.persistence.repositories import Repositories


class FakeKnowledgeClient:
    def __init__(self, responses: list[object]) -> None:
        self._responses = list(responses)
        self.calls = 0

    async def get_ingestion(self, job_id: str) -> dict[str, object]:
        self.calls += 1
        value = self._responses.pop(0)
        if isinstance(value, Exception):
            raise value
        assert isinstance(value, dict)
        return value

    async def create_ingestion(self, urls, *, client_run_id=None, idempotency_key=None):
        raise AssertionError("watcher 不应调用 create_ingestion")

    async def list_sources(self, limit: int = 10) -> dict[str, object]:
        raise AssertionError("watcher 不应调用 list_sources")

    async def aclose(self) -> None:
        return None


@pytest.fixture
def repo_env(tmp_path: Path):
    url = f"sqlite:///{(tmp_path / 'agent.db').as_posix()}"
    apply_migrations(url)
    engine = build_engine(url)
    sync = Repositories(build_session_factory(engine))
    yield sync, AsyncRepositories(sync)
    engine.dispose()


def completed_payload() -> dict[str, object]:
    return {
        "job_id": "job_1",
        "status": "completed",
        "total": 2,
        "succeeded": 2,
        "failed": 0,
        "skipped": 0,
        "items": [
            {
                "url": "https://example.com/a",
                "status": "completed",
                "title": "文章 A",
            },
            {
                "url": "https://example.com/b",
                "status": "completed",
                "title": "文章 B",
            },
        ],
    }


def make_watcher(
    repositories: AsyncRepositories,
    client: FakeKnowledgeClient,
    current: list[datetime],
    *,
    timeout_s: float = 1800,
) -> IngestionWatcher:
    return IngestionWatcher(
        knowledge_client=client,
        repositories=repositories,
        poll_interval_s=0.01,
        initial_delay_s=3,
        max_backoff_s=15,
        timeout_s=timeout_s,
        now=lambda: current[0],
    )


async def test_completed_job_writes_single_notice(repo_env) -> None:
    sync, repositories = repo_env
    conversation = sync.create_conversation(title="通知")
    base = datetime.now(UTC)
    current = [base]
    client = FakeKnowledgeClient(
        [
            {
                "job_id": "job_1",
                "status": "running",
                "total": 2,
                "succeeded": 1,
                "failed": 0,
                "skipped": 0,
                "items": [],
            },
            completed_payload(),
        ]
    )
    watcher = make_watcher(repositories, client, current)
    await watcher.register(job_id="job_1", conversation_id=conversation.id)

    current[0] = base + timedelta(seconds=4)
    assert await watcher.poll_once() == 1
    assert sync.list_messages(conversation.id) == []

    current[0] = base + timedelta(seconds=8)
    assert await watcher.poll_once() == 1
    messages = sync.list_messages(conversation.id)
    notices = [message for message in messages if message.kind == "ingestion_notice"]
    assert len(notices) == 1
    assert notices[0].run_id is None
    assert "已完成入库" in notices[0].content

    assert await watcher.poll_once() == 0
    assert (
        len(
            [
                message
                for message in sync.list_messages(conversation.id)
                if message.kind == "ingestion_notice"
            ]
        )
        == 1
    )


async def test_partial_job_notice_contains_failure(repo_env) -> None:
    sync, repositories = repo_env
    conversation = sync.create_conversation(title="部分失败")
    base = datetime.now(UTC)
    current = [base]
    client = FakeKnowledgeClient(
        [
            {
                "job_id": "job_2",
                "status": "partial",
                "total": 2,
                "succeeded": 1,
                "failed": 1,
                "skipped": 0,
                "items": [
                    {
                        "url": "https://example.com/bad",
                        "status": "failed",
                        "error_code": "FETCH_TIMEOUT",
                        "error_message": "抓取超时",
                    }
                ],
            }
        ]
    )
    watcher = make_watcher(repositories, client, current)
    await watcher.register(job_id="job_2", conversation_id=conversation.id)

    current[0] = base + timedelta(seconds=4)
    await watcher.poll_once()

    notices = [
        message
        for message in sync.list_messages(conversation.id)
        if message.kind == "ingestion_notice"
    ]
    assert len(notices) == 1
    assert "入库部分完成" in notices[0].content
    assert "https://example.com/bad" in notices[0].content
    assert "FETCH_TIMEOUT" in notices[0].content


async def test_unavailable_then_timeout_writes_unknown_notice(repo_env) -> None:
    sync, repositories = repo_env
    conversation = sync.create_conversation(title="不可达")
    base = datetime.now(UTC)
    current = [base]
    client = FakeKnowledgeClient(
        [
            AppError(ErrorCode.KNOWLEDGE_BASE_UNAVAILABLE, "连接失败"),
            AppError(ErrorCode.KNOWLEDGE_BASE_UNAVAILABLE, "连接失败"),
        ]
    )
    watcher = make_watcher(repositories, client, current, timeout_s=1800)
    await watcher.register(job_id="job_3", conversation_id=conversation.id)

    current[0] = base + timedelta(seconds=4)
    await watcher.poll_once()
    assert sync.list_messages(conversation.id) == []

    current[0] = base + timedelta(seconds=2_000)
    await watcher.poll_once()
    notices = [
        message
        for message in sync.list_messages(conversation.id)
        if message.kind == "ingestion_notice"
    ]
    assert len(notices) == 1
    assert "无法确认入库状态" in notices[0].content


async def test_persisted_watch_is_resumed_by_new_watcher(repo_env) -> None:
    sync, repositories = repo_env
    conversation = sync.create_conversation(title="重启恢复")
    base = datetime.now(UTC)
    current = [base]
    first = make_watcher(repositories, FakeKnowledgeClient([]), current)
    await first.register(job_id="job_4", conversation_id=conversation.id)

    resumed = make_watcher(
        repositories, FakeKnowledgeClient([completed_payload()]), current
    )
    current[0] = base + timedelta(seconds=4)
    await resumed.poll_once()

    notices = [
        message
        for message in sync.list_messages(conversation.id)
        if message.kind == "ingestion_notice"
    ]
    assert len(notices) == 1
    assert "已完成入库" in notices[0].content


async def test_finished_watches_are_cleaned_up_after_retention(repo_env) -> None:
    sync, repositories = repo_env
    conversation = sync.create_conversation(title="清理旧 watch")
    base = datetime.now(UTC)

    for job_id, content in (
        ("job_old", "旧通知"),
        ("job_new", "新通知"),
    ):
        sync.create_ingestion_watch(
            job_id=job_id,
            conversation_id=conversation.id,
            next_check_at=base,
        )
        sync.notify_ingestion_watch(job_id=job_id, content=content, notified_at=base)

    with sync._session() as session:
        session.execute(
            update(IngestionWatch)
            .where(IngestionWatch.job_id == "job_old")
            .values(created_at=base - timedelta(days=40))
        )
        session.commit()

    current = [base]
    watcher = IngestionWatcher(
        knowledge_client=FakeKnowledgeClient([]),
        repositories=repositories,
        retention_days=30,
        now=lambda: current[0],
    )

    assert await watcher.poll_once() == 0

    with sync._session() as session:
        remaining = {row.job_id for row in session.scalars(select(IngestionWatch))}
    assert remaining == {"job_new"}
