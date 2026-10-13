"""备份/恢复脚本共用的 SQLite 工具函数。"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from pathlib import Path

from sqlalchemy.engine import make_url

DEFAULT_TABLES: tuple[str, ...] = (
    "conversations",
    "messages",
    "agent_runs",
    "tool_calls",
    "document_snapshots",
    "run_sources",
)


class DatabaseToolError(RuntimeError):
    """备份/恢复过程中的可预期错误。"""


def sqlite_path_from_url(database_url: str) -> Path:
    """把 ``sqlite:///`` 形式的 URL 解析为文件路径。"""

    url = make_url(database_url)
    if url.get_backend_name() != "sqlite" or not url.database:
        raise DatabaseToolError("仅支持文件形式的 SQLite 数据库（sqlite:///...）")
    if url.database == ":memory:":
        raise DatabaseToolError("内存数据库无法备份，请使用文件数据库")
    return Path(url.database)


def inspect_database(
    connection: sqlite3.Connection, *, tables: Sequence[str] = DEFAULT_TABLES
) -> dict[str, object]:
    """执行完整性检查并统计各表行数（表不存在时记为 None）。"""

    try:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
    except sqlite3.DatabaseError as exc:
        # 文件不是合法的 SQLite 数据库（例如复制损坏）
        return {
            "integrity": f"invalid: {exc}",
            "counts": dict.fromkeys(tables),
        }
    counts: dict[str, int | None] = {}
    for table in tables:
        try:
            counts[table] = connection.execute(
                f"SELECT COUNT(*) FROM {table}"  # noqa: S608 - 表名来自固定常量
            ).fetchone()[0]
        except sqlite3.OperationalError:
            counts[table] = None
    return {"integrity": integrity, "counts": counts}


def open_readonly(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise DatabaseToolError(f"文件不存在：{path}")
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)
