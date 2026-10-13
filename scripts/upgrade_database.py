"""把数据库升级到最新版本（与应用启动时执行的迁移逻辑一致）。

用法::

    uv run python -m scripts.upgrade_database
    uv run python -m scripts.upgrade_database --database-url sqlite:///./data/app.db

说明：阶段 1 由 ``create_all`` 建立的旧库没有版本表，本脚本会按
``apply_migrations`` 的规则先 stamp 再升级；直接用 ``alembic upgrade head``
只会用于全新空库。
"""

from __future__ import annotations

import argparse
import json

from learning_agent.config import get_settings
from learning_agent.persistence.migrations import apply_migrations, schema_state


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="升级数据库 Schema")
    parser.add_argument(
        "--database-url",
        default=None,
        help="默认读取应用配置 DATABASE_URL（.env / 环境变量）",
    )
    args = parser.parse_args(argv)
    database_url = args.database_url or get_settings().database_url

    state_before = schema_state(database_url)
    apply_migrations(database_url)
    print(
        json.dumps(
            {
                "database_url": database_url,
                "previous_state": state_before,
                "current_state": schema_state(database_url),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
