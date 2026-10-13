"""备份 SQLite 数据库到指定文件，并做完整性与内容校验。

用法示例::

    uv run python -m scripts.backup_database --output backups/app.db
    uv run python -m scripts.backup_database --database-url sqlite:///./data/app.db
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime
from pathlib import Path

from scripts._sqlite_tools import (
    DatabaseToolError,
    inspect_database,
    open_readonly,
    sqlite_path_from_url,
)


def backup_database(source: Path, target: Path) -> dict[str, object]:
    """使用 SQLite 在线备份 API 复制数据库并校验结果。"""

    if target.exists():
        raise DatabaseToolError(f"备份目标已存在，请换一个文件名：{target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    source_connection = open_readonly(source)
    try:
        target_connection = sqlite3.connect(target)
        try:
            with target_connection:
                source_connection.backup(target_connection)
            report = inspect_database(target_connection)
        finally:
            target_connection.close()
    finally:
        source_connection.close()
    if report["integrity"] != "ok":
        raise DatabaseToolError(f"备份文件完整性校验失败：{report['integrity']}")
    return {
        "source": str(source),
        "target": str(target),
        "size_bytes": target.stat().st_size,
        **report,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="备份 SQLite 数据库")
    parser.add_argument(
        "--database-url",
        default=None,
        help="默认读取应用配置 DATABASE_URL（.env / 环境变量）",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="备份文件路径，默认 backups/app-<时间戳>.db",
    )
    args = parser.parse_args(argv)

    database_url = args.database_url
    if database_url is None:
        from learning_agent.config import get_settings

        database_url = get_settings().database_url
    target = (
        Path(args.output)
        if args.output
        else Path("backups") / f"app-{datetime.now():%Y%m%d-%H%M%S}.db"
    )
    try:
        summary = backup_database(sqlite_path_from_url(database_url), target)
    except DatabaseToolError as exc:
        print(f"备份失败：{exc}")
        return 2
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
