"""数据库引擎与会话工厂。"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker


def build_engine(database_url: str, *, echo: bool = False) -> Engine:
    """按 DATABASE_URL 构建引擎；SQLite 文件目录不存在时自动创建。"""

    url = make_url(database_url)
    connect_args: dict[str, object] = {}
    if url.get_backend_name() == "sqlite":
        connect_args["check_same_thread"] = False
        if url.database and url.database != ":memory:":
            Path(url.database).expanduser().parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        database_url, echo=echo, connect_args=connect_args, pool_pre_ping=True
    )
    if url.get_backend_name() == "sqlite":
        event.listen(engine, "connect", _configure_sqlite)
    return engine


def _configure_sqlite(dbapi_connection: object, _record: object) -> None:
    """连接级 PRAGMA：外键约束、WAL 与写锁等待，缓解并发写竞争。"""

    cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
    finally:
        cursor.close()


def build_session_factory(engine: Engine) -> sessionmaker:
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


def ping(engine: Engine) -> bool:
    """就绪检查：确认数据库可查询。"""

    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001 - 就绪检查失败即返回 False
        return False
    return True
