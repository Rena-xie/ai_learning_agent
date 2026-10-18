"""异步入库任务的完成/异常通知（v2 M3）。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from learning_agent.domain.errors import AppError, ErrorCode
from learning_agent.persistence.async_repositories import AsyncRepositories
from learning_agent.persistence.repositories import IngestionWatchRecord
from learning_agent.tools.knowledge.client import KnowledgeBasePort

logger = logging.getLogger(__name__)

_TERMINAL_STATUSES = frozenset({"completed", "partial", "failed"})
_MAX_NOTICE_CHARS = 300
_MAX_FAILURES_IN_NOTICE = 2


class IngestionWatcher:
    """轮询 RAG job，在对话中写入确定性的完成/失败通知。

    不调用 LLM：通知只转述任务事实与错误码，避免额外费用与不确定性。
    """

    def __init__(
        self,
        *,
        knowledge_client: KnowledgeBasePort,
        repositories: AsyncRepositories,
        poll_interval_s: float = 2.0,
        initial_delay_s: float = 3.0,
        max_backoff_s: float = 15.0,
        timeout_s: float = 1800.0,
        batch_size: int = 20,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._knowledge_client = knowledge_client
        self._repositories = repositories
        self._poll_interval_s = poll_interval_s
        self._initial_delay_s = initial_delay_s
        self._max_backoff_s = max_backoff_s
        self._timeout_s = timeout_s
        self._batch_size = batch_size
        self._now = now or (lambda: datetime.now(UTC))
        self._task: asyncio.Task[None] | None = None
        self._stop: asyncio.Event | None = None

    async def register(self, *, job_id: str, conversation_id: str) -> None:
        """登记一个 job 的完成通知；同一 job 幂等。"""

        now = self._now()
        await self._repositories.create_ingestion_watch(
            job_id=job_id,
            conversation_id=conversation_id,
            next_check_at=now + timedelta(seconds=self._initial_delay_s),
        )
        logger.info(
            "ingestion_watch_registered",
            extra={
                "extra_fields": {
                    "job_id": job_id,
                    "conversation_id": conversation_id,
                }
            },
        )

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(
            self._run(self._stop), name="ingestion-watcher"
        )

    async def stop(self) -> None:
        if self._stop is not None:
            self._stop.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=5.0)
            except TimeoutError:
                self._task.cancel()
                await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
        self._stop = None

    async def poll_once(self) -> int:
        """检查一批到期 watch；返回本轮检查数量（测试可直接调用）。"""

        now = self._now()
        watches = await self._repositories.list_due_ingestion_watches(
            now=now, limit=self._batch_size
        )
        for watch in watches:
            try:
                await self._check(watch, now=now)
            except Exception:  # noqa: BLE001 - 单个 watch 失败不阻塞其他任务
                logger.exception(
                    "ingestion_watch_check_failed",
                    extra={"extra_fields": {"job_id": watch.job_id}},
                )
        return len(watches)

    async def _run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                await self.poll_once()
            except Exception:  # noqa: BLE001 - 轮询循环必须存活
                logger.exception("ingestion_watcher_poll_failed")
            try:
                await asyncio.wait_for(stop.wait(), timeout=self._poll_interval_s)
            except TimeoutError:
                continue

    async def _check(self, watch: IngestionWatchRecord, *, now: datetime) -> None:
        try:
            payload = await self._knowledge_client.get_ingestion(watch.job_id)
        except AppError as exc:
            await self._handle_error(watch, exc, now=now)
            return

        status = _text(payload.get("status"))
        if status in _TERMINAL_STATUSES:
            await self._notify(watch, build_completion_notice(payload), now=now)
            return
        await self._schedule_next(watch, now=now, last_error=None)

    async def _handle_error(
        self, watch: IngestionWatchRecord, error: AppError, *, now: datetime
    ) -> None:
        if error.code is ErrorCode.INGESTION_NOT_FOUND:
            await self._notify(
                watch,
                "入库失败：没有找到这次任务，可能已被清理。可以重新发送链接。",
                now=now,
            )
            return
        if now - watch.created_at >= timedelta(seconds=self._timeout_s):
            await self._notify(
                watch,
                "暂时无法确认入库状态：知识库服务连接失败。"
                "请稍后检查 RAG 服务，或重新发送链接。",
                now=now,
            )
            return
        await self._schedule_next(
            watch,
            now=now,
            last_error=f"{error.code}: {error.message}"[:200],
        )

    async def _schedule_next(
        self,
        watch: IngestionWatchRecord,
        *,
        now: datetime,
        last_error: str | None,
    ) -> None:
        attempts = watch.attempts + 1
        delay = min(
            self._initial_delay_s * (2 ** (attempts - 1)),
            self._max_backoff_s,
        )
        await self._repositories.save_ingestion_watch(
            job_id=watch.job_id,
            status="watching",
            attempts=attempts,
            last_checked_at=now,
            next_check_at=now + timedelta(seconds=delay),
            notified_at=None,
            last_error=last_error,
        )
        logger.info(
            "ingestion_watch_rescheduled",
            extra={
                "extra_fields": {
                    "job_id": watch.job_id,
                    "attempts": attempts,
                    "delay_s": delay,
                }
            },
        )

    async def _notify(
        self,
        watch: IngestionWatchRecord,
        content: str,
        *,
        now: datetime,
    ) -> None:
        written = await self._repositories.notify_ingestion_watch(
            job_id=watch.job_id, content=content, notified_at=now
        )
        if written:
            logger.info(
                "ingestion_notice_sent",
                extra={"extra_fields": {"job_id": watch.job_id}},
            )
        else:
            logger.info(
                "ingestion_watch_not_notifiable",
                extra={"extra_fields": {"job_id": watch.job_id}},
            )


def build_completion_notice(payload: Mapping[str, object]) -> str:
    """把 RAG job 结果转成不超过 300 字的对话通知。"""

    status = _text(payload.get("status"))
    total = _int(payload.get("total"))
    succeeded = _int(payload.get("succeeded"))
    failed = _int(payload.get("failed"))
    skipped = _int(payload.get("skipped"))
    items = _items(payload.get("items"))
    failures = [item for item in items if _text(item.get("status")) == "failed"]

    if status == "completed":
        if succeeded == 0 and skipped > 0:
            return _shorten(f"入库完成：共 {total} 条，均已存在，无需重复入库。")
        titles = [
            _text(item.get("title"))
            for item in items
            if _text(item.get("status")) == "completed" and _text(item.get("title"))
        ]
        summary = f"已完成入库：共 {total} 条，成功 {succeeded} 条"
        if skipped:
            summary += f"，跳过 {skipped} 条"
        summary += "。"
        if titles:
            summary += "已收录：" + "、".join(titles[:3]) + "。"
        summary += "现在可以基于这些内容提问。"
        return _shorten(summary)

    if status == "partial":
        lines = [f"入库部分完成：成功 {succeeded} 条，失败 {failed} 条。"]
        lines.extend(_failure_lines(failures))
        lines.append("可以修正后重新发送失败链接。")
        return _shorten("\n".join(lines))

    if status == "failed":
        lines = [f"入库失败：共 {total} 条，成功 {succeeded} 条，失败 {failed} 条。"]
        lines.extend(_failure_lines(failures))
        return _shorten("\n".join(lines))

    return f"暂时无法确认入库状态（{status or 'unknown'}）。"


def _failure_lines(items: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for item in items[:_MAX_FAILURES_IN_NOTICE]:
        url = _text(item.get("url")) or _text(item.get("canonical_url"))
        code = _text(item.get("error_code"))
        reason = _text(item.get("error_message")) or code or "未知原因"
        detail = f"{reason}（{code}）" if code and code not in reason else reason
        target = _shorten(url, 120) if url else "未知链接"
        lines.append(f"失败：{target}\n原因：{detail}")
    return lines


def _items(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _text(value: object) -> str:
    return " ".join(value.split()) if isinstance(value, str) else ""


def _int(value: object) -> int:
    return value if isinstance(value, int) else 0


def _shorten(text: str, limit: int = _MAX_NOTICE_CHARS) -> str:
    normalized = "\n".join(
        " ".join(line.split()) for line in text.splitlines() if line.strip()
    )
    return normalized if len(normalized) <= limit else normalized[: limit - 1] + "…"
