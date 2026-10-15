"""后台运行调度器：持有执行中的 Agent Run 任务，支持优雅关闭。

异步提交模式下，HTTP 请求只负责创建运行记录并返回 ``run_id``，
真正的模型与工具循环在后台任务中执行；本调度器持有这些任务引用，
以便应用关闭时等待其结束，避免「Task was destroyed but it is pending」。
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any

from learning_agent.agent.runtime import RunOutcome


class RunScheduler:
    """把 Agent Run 放到后台任务执行，并持有引用以便关闭时等待。"""

    def __init__(self) -> None:
        self._tasks: set[asyncio.Task[RunOutcome]] = set()

    @property
    def pending(self) -> int:
        """仍在执行的运行数量。"""

        return len(self._tasks)

    def schedule(self, coro: Coroutine[Any, Any, RunOutcome]) -> None:
        """调度一个后台运行。

        运行级失败由 :meth:`AgentRuntime.resume` 自行转化为失败结果并落库，
        此处不额外处理异常；未知异常也会被 Runtime 捕获。
        """

        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def shutdown(self) -> None:
        """等待所有在途运行结束（应用关闭时调用）。"""

        if self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)
