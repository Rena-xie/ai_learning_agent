"""备份与恢复集成测试：备份可校验、恢复后数据可读、异常路径被拒绝。"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from scripts._sqlite_tools import DatabaseToolError, sqlite_path_from_url
from scripts.backup_database import backup_database
from scripts.restore_database import restore_database

from learning_agent.domain.entities import DocumentContent, Section
from learning_agent.persistence.database import build_engine, build_session_factory
from learning_agent.persistence.migrations import apply_migrations
from learning_agent.persistence.repositories import Repositories

RUN_ID = "66666666-6666-6666-6666-666666666666"


def seed_database(url: str) -> str:
    """建库并写入一条完整链路数据（不调用模型）。"""

    apply_migrations(url)
    repositories = Repositories(build_session_factory(build_engine(url)))
    conversation = repositories.create_conversation(title="备份演练")
    repositories.add_message(
        conversation_id=conversation.id, role="user", content="备份前的消息"
    )
    repositories.create_run(
        run_id=RUN_ID,
        conversation_id=conversation.id,
        model_name="test-model",
        prompt_version="v1",
    )
    snapshot_id = repositories.add_snapshot(
        conversation_id=conversation.id,
        content=DocumentContent(
            title="示例指南",
            canonical_url="https://docs.example.com/guide",
            fetched_at=datetime.now(UTC),
            content_hash="c" * 64,
            content_text="正文内容",
            sections=(Section(title="核心概念", level=1, text="正文内容"),),
        ),
        parse_status="ok",
    )
    repositories.link_run_source(run_id=RUN_ID, snapshot_id=snapshot_id, source_id="S1")
    return conversation.id


def test_backup_and_restore_roundtrip(tmp_path) -> None:
    source_url = f"sqlite:///{(tmp_path / 'source.db').as_posix()}"
    conversation_id = seed_database(source_url)

    backup_path = tmp_path / "backups" / "app-test.db"
    summary = backup_database(sqlite_path_from_url(source_url), backup_path)
    assert summary["integrity"] == "ok"
    assert summary["counts"] == {
        "conversations": 1,
        "messages": 1,
        "agent_runs": 1,
        "tool_calls": 0,
        "document_snapshots": 1,
        "run_sources": 1,
    }

    restored_url = f"sqlite:///{(tmp_path / 'restored' / 'app.db').as_posix()}"
    restore_summary = restore_database(backup_path, sqlite_path_from_url(restored_url))
    assert restore_summary["counts"] == summary["counts"]

    repositories = Repositories(build_session_factory(build_engine(restored_url)))
    assert [
        message.content for message in repositories.list_messages(conversation_id)
    ] == ["备份前的消息"]
    run = repositories.get_run(RUN_ID)
    assert [source.source_id for source in run.sources] == ["S1"]


def test_backup_refuses_existing_target(tmp_path) -> None:
    source_url = f"sqlite:///{(tmp_path / 'source.db').as_posix()}"
    seed_database(source_url)
    target = tmp_path / "exists.db"
    target.write_bytes(b"placeholder")
    with pytest.raises(DatabaseToolError):
        backup_database(sqlite_path_from_url(source_url), target)


def test_restore_refuses_overwrite_without_force(tmp_path) -> None:
    source_url = f"sqlite:///{(tmp_path / 'source.db').as_posix()}"
    seed_database(source_url)
    backup_path = tmp_path / "app.db"
    backup_database(sqlite_path_from_url(source_url), backup_path)
    target = tmp_path / "target.db"
    restore_database(backup_path, target)
    with pytest.raises(DatabaseToolError):
        restore_database(backup_path, target)
    restore_database(backup_path, target, force=True)


def test_restore_rejects_corrupted_backup(tmp_path) -> None:
    broken = tmp_path / "broken.db"
    broken.write_bytes(b"this is not a sqlite database")
    with pytest.raises(DatabaseToolError):
        restore_database(broken, tmp_path / "out.db")
