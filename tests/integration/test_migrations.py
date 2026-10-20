"""数据库迁移测试：全新库、阶段 1 旧库与约束保留三条路径。"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, inspect, text

from learning_agent.domain.errors import AppError, ErrorCode
from learning_agent.persistence.database import build_engine, build_session_factory
from learning_agent.persistence.migrations import apply_migrations, schema_state
from learning_agent.persistence.models import Base
from learning_agent.persistence.repositories import Repositories

EXPECTED_TABLES = {
    "conversations",
    "messages",
    "agent_runs",
    "tool_calls",
    "document_snapshots",
    "run_sources",
    "ingestion_watches",
    "learning_notes",
    "learning_note_sources",
    "learning_quizzes",
    "learning_quiz_questions",
    "learning_flashcards",
    "learning_reviews",
    "alembic_version",
}


def make_repositories(url: str) -> Repositories:
    return Repositories(build_session_factory(build_engine(url)))


def test_fresh_database_is_migrated_and_usable(tmp_path) -> None:
    url = f"sqlite:///{(tmp_path / 'fresh.db').as_posix()}"
    assert schema_state(url) == "empty"

    apply_migrations(url)
    apply_migrations(url)  # 幂等：重复执行不应报错

    assert schema_state(url) == "migrated"
    engine = create_engine(url)
    try:
        tables = set(inspect(engine).get_table_names())
        message_columns = {
            column["name"] for column in inspect(engine).get_columns("messages")
        }
        snapshot_columns = {
            column["name"]
            for column in inspect(engine).get_columns("document_snapshots")
        }
    finally:
        engine.dispose()
    assert tables >= EXPECTED_TABLES
    assert "kind" in message_columns
    assert "provider" in snapshot_columns

    repositories = make_repositories(url)
    conversation = repositories.create_conversation(title="迁移后可用")
    repositories.add_message(
        conversation_id=conversation.id, role="user", content="迁移后写入的消息"
    )
    assert repositories.list_messages(conversation.id)[0].content == "迁移后写入的消息"


def test_migration_keeps_single_active_run_constraint(tmp_path) -> None:
    """回归：迁移必须保留「同一对话单一活动运行」的部分唯一索引。"""

    url = f"sqlite:///{(tmp_path / 'constraint.db').as_posix()}"
    apply_migrations(url)
    engine = create_engine(url)
    try:
        indexes = inspect(engine).get_indexes("agent_runs")
    finally:
        engine.dispose()
    matched = [
        item for item in indexes if item["name"] == "uq_agent_runs_active_conversation"
    ]
    assert matched
    assert bool(matched[0]["unique"]) is True
    assert "sqlite_where" in matched[0]["dialect_options"]

    repositories = make_repositories(url)
    conversation = repositories.create_conversation(title="约束")
    repositories.create_run(
        run_id="44444444-4444-4444-4444-444444444444",
        conversation_id=conversation.id,
        model_name="test-model",
        prompt_version="v1",
    )
    with pytest.raises(AppError) as excinfo:
        repositories.create_run(
            run_id="55555555-5555-5555-5555-555555555555",
            conversation_id=conversation.id,
            model_name="test-model",
            prompt_version="v1",
        )
    assert excinfo.value.code is ErrorCode.RUN_IN_PROGRESS


def test_legacy_database_is_stamped_without_data_loss(tmp_path) -> None:
    """阶段 1 由 create_all 建立的库：stamp 后升级，既有数据保留。"""

    url = f"sqlite:///{(tmp_path / 'legacy.db').as_posix()}"
    engine = build_engine(url)
    Base.metadata.create_all(engine)
    repositories = Repositories(build_session_factory(engine))
    conversation = repositories.create_conversation(title="旧库数据")
    repositories.add_message(
        conversation_id=conversation.id, role="user", content="升级前写入的消息"
    )
    engine.dispose()

    assert schema_state(url) == "legacy"
    apply_migrations(url)
    assert schema_state(url) == "migrated"

    upgraded = make_repositories(url)
    messages = upgraded.list_messages(conversation.id)
    assert [message.content for message in messages] == ["升级前写入的消息"]


def test_v1_legacy_database_gets_new_schema(tmp_path) -> None:
    """真实 v1 旧库应补跑 0002/0003，而不是直接 stamp head。"""

    url = f"sqlite:///{(tmp_path / 'legacy-v1.db').as_posix()}"
    engine = build_engine(url)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE conversations (
                    id VARCHAR(36) PRIMARY KEY,
                    title VARCHAR(120) NOT NULL,
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE document_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id VARCHAR(36) NOT NULL,
                    canonical_url VARCHAR(2048) NOT NULL,
                    title VARCHAR(300) NOT NULL,
                    fetched_at DATETIME NOT NULL,
                    content_hash VARCHAR(64) NOT NULL,
                    extracted_text TEXT NOT NULL,
                    sections_json TEXT,
                    truncated BOOLEAN NOT NULL,
                    parse_status VARCHAR(16) NOT NULL,
                    warnings_json TEXT,
                    FOREIGN KEY(conversation_id)
                        REFERENCES conversations(id) ON DELETE CASCADE
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id VARCHAR(36) NOT NULL,
                    run_id VARCHAR(36),
                    role VARCHAR(16) NOT NULL,
                    content TEXT NOT NULL,
                    created_at DATETIME NOT NULL,
                    FOREIGN KEY(conversation_id)
                        REFERENCES conversations(id) ON DELETE CASCADE
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO conversations
                    (id, title, created_at, updated_at)
                VALUES
                    ('conv-1', '旧对话', '2026-10-01 00:00:00',
                     '2026-10-01 00:00:00')
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO messages
                    (conversation_id, role, content, created_at)
                VALUES
                    ('conv-1', 'user', '旧消息', '2026-10-01 00:00:00')
                """
            )
        )
    engine.dispose()

    assert schema_state(url) == "legacy"
    apply_migrations(url)

    engine = create_engine(url)
    try:
        tables = set(inspect(engine).get_table_names())
        columns = {column["name"] for column in inspect(engine).get_columns("messages")}
        snapshot_columns = {
            column["name"]
            for column in inspect(engine).get_columns("document_snapshots")
        }
    finally:
        engine.dispose()
    assert "ingestion_watches" in tables
    assert "kind" in columns
    assert "provider" in snapshot_columns

    repositories = make_repositories(url)
    messages = repositories.list_messages("conv-1")
    assert [message.content for message in messages] == ["旧消息"]
    assert messages[0].kind == "chat"


def test_interrupted_migration_is_recovered(tmp_path) -> None:
    """模拟中断的迁移：版本表存在但没有版本记录，应重新 stamp 并升级。"""

    url = f"sqlite:///{(tmp_path / 'interrupted.db').as_posix()}"
    engine = build_engine(url)
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
        )
    engine.dispose()

    assert schema_state(url) == "legacy"
    apply_migrations(url)
    assert schema_state(url) == "migrated"

    repositories = make_repositories(url)
    conversation = repositories.create_conversation(title="中断恢复")
    assert repositories.get_conversation(conversation.id) is not None
