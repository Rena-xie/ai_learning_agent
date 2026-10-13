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
    finally:
        engine.dispose()
    assert tables >= EXPECTED_TABLES

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
