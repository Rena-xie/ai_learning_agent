"""RunScheduler：后台任务调度与优雅关闭。"""

from __future__ import annotations

import asyncio

from learning_agent.agent.runtime import RunOutcome
from learning_agent.application.run_scheduler import RunScheduler
from learning_agent.domain.enums import RunStatus


async def test_shutdown_waits_for_pending_tasks() -> None:
    scheduler = RunScheduler()
    done = asyncio.Event()

    async def job() -> RunOutcome:
        await asyncio.sleep(0.05)
        done.set()
        return RunOutcome(run_id="run-1", status=RunStatus.COMPLETED)

    scheduler.schedule(job())
    assert scheduler.pending == 1

    await scheduler.shutdown()

    assert done.is_set()
    assert scheduler.pending == 0


async def test_shutdown_without_tasks_is_noop() -> None:
    scheduler = RunScheduler()

    await scheduler.shutdown()

    assert scheduler.pending == 0
