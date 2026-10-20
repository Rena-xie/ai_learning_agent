"""V3 每日 Digest 的启动触发后台任务。"""

from __future__ import annotations

import asyncio

from learning_agent.application.digest_service import DailyDigestService
from learning_agent.observability.logging import get_logger

logger = get_logger(__name__)


class DigestScheduler:
    """不设固定钟点，启动后立即检查当天 Digest。"""

    def __init__(
        self,
        service: DailyDigestService,
        *,
        enabled: bool,
        recheck_interval_s: int,
    ) -> None:
        self._service = service
        self._enabled = enabled
        self._recheck_interval_s = max(60, recheck_interval_s)
        self._task: asyncio.Task[None] | None = None
        self._stop: asyncio.Event | None = None

    def start(self) -> None:
        if not self._enabled or (self._task is not None and not self._task.done()):
            return
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(
            self._run(self._stop), name="daily-digest-scheduler"
        )

    async def stop(self) -> None:
        if self._stop is not None:
            self._stop.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=10)
            except TimeoutError:
                self._task.cancel()
                await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
        self._stop = None

    async def _run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                result = await self._service.run_today()
                logger.info(
                    "daily_digest_check_finished",
                    extra={
                        "extra_fields": {
                            "date": result.digest.digest_date,
                            "status": result.digest.status,
                            "items": result.digest.item_count,
                        }
                    },
                )
            except Exception:  # noqa: BLE001 - 后台任务必须存活
                logger.exception("daily_digest_check_failed")
            try:
                await asyncio.wait_for(stop.wait(), timeout=self._recheck_interval_s)
            except TimeoutError:
                continue
