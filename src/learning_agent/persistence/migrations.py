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

    阶段 1 由 ``create_all`` 建立的库没有版本表：先 ``stamp head`` 再升级。
    该假设仅在旧库结构与当前初始迁移一致时成立（v1.0 阶段 2 时成立）；
    后续若初始迁移有结构变更，需要人工确认旧库后再 stamp。
    """

    state = schema_state(database_url)
    config = build_alembic_config(database_url)
    if state == "legacy":
        logger.warning(
            "database_schema_legacy",
            extra={"extra_fields": {"action": "stamp_head"}},
        )
        command.stamp(config, "head")
    command.upgrade(config, "head")
    logger.info(
        "database_schema_ready",
        extra={"extra_fields": {"previous_state": state}},
    )
