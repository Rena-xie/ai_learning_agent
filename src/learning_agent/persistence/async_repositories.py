"""异步数据库访问门面：把同步 ``Repositories`` 的阻塞调用移入线程池。

SQLAlchemy 同步 Session 不能在事件循环里直接调用，否则会阻塞整个进程。
本门面只包装异步调用方（Agent Runtime 与 API 路由）实际用到的方法，
内部统一用 ``run_in_threadpool`` 调度；同步 ``Repositories`` 保持不变，
供脚本、Alembic 迁移与测试继续使用。
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import TypeVar

from starlette.concurrency import run_in_threadpool

from learning_agent.domain.entities import DocumentContent, UsageRecord
from learning_agent.domain.enums import RunStatus
from learning_agent.persistence.repositories import (
    ConversationRecord,
    IngestionWatchRecord,
    MessageRecord,
    Repositories,
    RunRecord,
    RunSourceView,
    SnapshotRecord,
)

_T = TypeVar("_T")


class AsyncRepositories:
    """``Repositories`` 的异步包装：把所有阻塞调用移入线程池。"""

    def __init__(self, inner: Repositories) -> None:
        self._inner = inner

    @property
    def sync(self) -> Repositories:
        """底层同步实例，供非热路径（启动清理、健康检查）使用。"""

        return self._inner

    async def _call(
        self, func: Callable[..., _T], *args: object, **kwargs: object
    ) -> _T:
        return await run_in_threadpool(func, *args, **kwargs)

    # ---------- 对话 ----------

    async def create_conversation(self, *, title: str) -> ConversationRecord:
        return await self._call(self._inner.create_conversation, title=title)

    async def list_conversations(self) -> list[ConversationRecord]:
        return await self._call(self._inner.list_conversations)

    async def get_conversation(self, conversation_id: str) -> ConversationRecord | None:
        return await self._call(self._inner.get_conversation, conversation_id)

    async def set_title_if_default(self, conversation_id: str, *, title: str) -> None:
        await self._call(self._inner.set_title_if_default, conversation_id, title=title)

    async def delete_conversation(self, conversation_id: str) -> bool:
        return await self._call(self._inner.delete_conversation, conversation_id)

    # ---------- 运行与消息 ----------

    async def start_run(
        self,
        *,
        run_id: str,
        conversation_id: str,
        user_message: str,
        model_name: str,
        prompt_version: str,
    ) -> None:
        await self._call(
            self._inner.start_run,
            run_id=run_id,
            conversation_id=conversation_id,
            user_message=user_message,
            model_name=model_name,
            prompt_version=prompt_version,
        )

    async def complete_run(
        self,
        *,
        run_id: str,
        conversation_id: str,
        content: str,
        usage: UsageRecord,
        model_name: str | None,
        duration_ms: int,
    ) -> None:
        await self._call(
            self._inner.complete_run,
            run_id=run_id,
            conversation_id=conversation_id,
            content=content,
            usage=usage,
            model_name=model_name,
            duration_ms=duration_ms,
        )

    async def finish_run(
        self,
        *,
        run_id: str,
        status: RunStatus,
        usage: UsageRecord,
        model_name: str | None = None,
        duration_ms: int | None = None,
        error_code: str | None = None,
        error_category: str | None = None,
    ) -> None:
        await self._call(
            self._inner.finish_run,
            run_id=run_id,
            status=status,
            usage=usage,
            model_name=model_name,
            duration_ms=duration_ms,
            error_code=error_code,
            error_category=error_category,
        )

    async def get_run(self, run_id: str) -> RunRecord | None:
        return await self._call(self._inner.get_run, run_id)

    async def get_active_run(self, conversation_id: str) -> RunRecord | None:
        return await self._call(self._inner.get_active_run, conversation_id)

    async def list_messages(
        self, conversation_id: str, *, limit: int | None = None
    ) -> list[MessageRecord]:
        return await self._call(self._inner.list_messages, conversation_id, limit=limit)

    async def create_ingestion_watch(
        self,
        *,
        job_id: str,
        conversation_id: str,
        next_check_at: datetime,
    ) -> IngestionWatchRecord:
        return await self._call(
            self._inner.create_ingestion_watch,
            job_id=job_id,
            conversation_id=conversation_id,
            next_check_at=next_check_at,
        )

    async def list_due_ingestion_watches(
        self, *, now: datetime, limit: int = 20
    ) -> list[IngestionWatchRecord]:
        return await self._call(
            self._inner.list_due_ingestion_watches, now=now, limit=limit
        )

    async def save_ingestion_watch(
        self,
        *,
        job_id: str,
        status: str,
        attempts: int,
        last_checked_at: datetime | None,
        next_check_at: datetime,
        notified_at: datetime | None,
        last_error: str | None,
    ) -> None:
        await self._call(
            self._inner.save_ingestion_watch,
            job_id=job_id,
            status=status,
            attempts=attempts,
            last_checked_at=last_checked_at,
            next_check_at=next_check_at,
            notified_at=notified_at,
            last_error=last_error,
        )

    async def add_ingestion_notice(
        self, *, conversation_id: str, content: str
    ) -> MessageRecord:
        return await self._call(
            self._inner.add_ingestion_notice,
            conversation_id=conversation_id,
            content=content,
        )

    async def notify_ingestion_watch(
        self, *, job_id: str, content: str, notified_at: datetime
    ) -> bool:
        return await self._call(
            self._inner.notify_ingestion_watch,
            job_id=job_id,
            content=content,
            notified_at=notified_at,
        )

    async def get_message_by_run(
        self,
        *,
        conversation_id: str,
        run_id: str,
        role: str = "assistant",
    ) -> MessageRecord | None:
        return await self._call(
            self._inner.get_message_by_run,
            conversation_id=conversation_id,
            run_id=run_id,
            role=role,
        )

    async def list_run_sources(
        self, conversation_id: str
    ) -> dict[str, tuple[RunSourceView, ...]]:
        return await self._call(self._inner.list_run_sources, conversation_id)

    # ---------- 文档快照与来源 ----------

    async def add_snapshot(
        self,
        *,
        conversation_id: str,
        content: DocumentContent,
        parse_status: str,
    ) -> int:
        return await self._call(
            self._inner.add_snapshot,
            conversation_id=conversation_id,
            content=content,
            parse_status=parse_status,
        )

    async def find_snapshot_id(
        self, *, conversation_id: str, canonical_url: str, content_hash: str
    ) -> int | None:
        return await self._call(
            self._inner.find_snapshot_id,
            conversation_id=conversation_id,
            canonical_url=canonical_url,
            content_hash=content_hash,
        )

    async def list_snapshots(
        self, conversation_id: str, *, limit: int = 20
    ) -> list[SnapshotRecord]:
        return await self._call(
            self._inner.list_snapshots, conversation_id, limit=limit
        )

    async def link_run_source(
        self, *, run_id: str, snapshot_id: int, source_id: str
    ) -> None:
        await self._call(
            self._inner.link_run_source,
            run_id=run_id,
            snapshot_id=snapshot_id,
            source_id=source_id,
        )

    async def add_tool_call(
        self,
        *,
        run_id: str,
        tool_call_id: str,
        tool_name: str,
        status: str,
        started_at: datetime,
        finished_at: datetime | None,
        duration_ms: int | None,
        arguments_summary: str,
        result_summary: str,
        error_code: str | None,
    ) -> None:
        await self._call(
            self._inner.add_tool_call,
            run_id=run_id,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            status=status,
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=duration_ms,
            arguments_summary=arguments_summary,
            result_summary=result_summary,
            error_code=error_code,
        )

    # ---------- 统计 ----------

    async def run_statistics(self) -> dict[str, object]:
        return await self._call(self._inner.run_statistics)
