"""Alembic 环境：数据库地址来自应用配置，CLI 与代码调用共用同一套设置。"""

from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

_ROOT = Path(__file__).resolve().parents[1]
for candidate in (_ROOT / "src", _ROOT):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from learning_agent.config import get_settings  # noqa: E402 - 需要先补齐 sys.path
from learning_agent.persistence.models import Base  # noqa: E402

config = context.config
if config.config_file_name is not None and config.attributes.get(
    "configure_logger", True
):
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def database_url() -> str:
    """优先使用调用方注入的地址，其次读取应用配置（环境变量 / .env）。"""

    configured = config.get_main_option("sqlalchemy.url") or ""
    if configured:
        return configured
    return get_settings().database_url


def run_migrations_offline() -> None:
    context.configure(
        url=database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = dict(config.get_section(config.config_ini_section) or {})
    section["sqlalchemy.url"] = database_url()
    connectable = engine_from_config(
        section, prefix="sqlalchemy.", poolclass=pool.NullPool
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
