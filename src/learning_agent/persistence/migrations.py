"""数据库迁移入口：应用启动与测试统一使用 Alembic 管理 Schema。"""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from learning_agent.observability.logging import get_logger

logger = get_logger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_ALEMBIC_INI = _REPO_ROOT / "alembic.ini"
_SCRIPT_LOCATION = _REPO_ROOT / "migrations"


def build_alembic_config(database_url: str) -> Config:
    """构造 Alembic 配置；由代码调用时不重复配置日志。"""

    config = Config(str(_ALEMBIC_INI))
    config.set_main_option("script_location", str(_SCRIPT_LOCATION))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    config.attributes["configure_logger"] = False
    return config


def schema_state(database_url: str) -> str:
    """返回当前库的状态：``empty``、``legacy``（有表但无版本记录）或 ``migrated``。

    ``alembic_version`` 表存在但没有版本记录，说明上次迁移中断；这种情况按
    ``legacy`` 处理，先 stamp 再升级。
    """

    engine = create_engine(database_url)
    try:
        tables = set(inspect(engine).get_table_names())
        if "alembic_version" in tables:
            with engine.connect() as connection:
                row = connection.execute(
                    text("SELECT version_num FROM alembic_version LIMIT 1")
                ).first()
            if row is not None:
                return "migrated"
        other_tables = tables - {"alembic_version"}
        return "legacy" if other_tables else "empty"
    finally:
        engine.dispose()


def apply_migrations(database_url: str) -> None:
    """把数据库升级到最新版本。

    阶段 1 由 ``create_all`` 建立的库没有版本表：先 stamp 到与现有结构匹配的
    版本，再执行后续迁移。这样既兼容当前模型 create_all 出来的旧库，也不会
    跳过新增的 0002 迁移。
    """

    state = schema_state(database_url)
    config = build_alembic_config(database_url)
    if state == "legacy":
        stamp_revision = _legacy_stamp_revision(database_url)
        logger.warning(
            "database_schema_legacy",
            extra={
                "extra_fields": {
                    "action": "stamp",
                    "revision": stamp_revision,
                }
            },
        )
        command.stamp(config, stamp_revision)
    command.upgrade(config, "head")
    logger.info(
        "database_schema_ready",
        extra={"extra_fields": {"previous_state": state}},
    )


def _legacy_stamp_revision(database_url: str) -> str:
    """判断无版本记录的旧库需要从哪个修订开始补跑。"""

    engine = create_engine(database_url)
    try:
        tables = set(inspect(engine).get_table_names())
        columns: set[str] = set()
        if "messages" in tables:
            columns = {
                column["name"] for column in inspect(engine).get_columns("messages")
            }
        snapshot_columns: set[str] = set()
        if "document_snapshots" in tables:
            snapshot_columns = {
                column["name"]
                for column in inspect(engine).get_columns("document_snapshots")
            }
    finally:
        engine.dispose()
    if "ingestion_watches" in tables and "kind" in columns:
        if "provider" not in snapshot_columns:
            return "0002_ingestion_watch"
        return "head"
    return "0001_initial_schema"
