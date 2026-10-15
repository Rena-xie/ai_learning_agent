"""聊天应用服务：校验输入、协调对话与 Agent Run。"""

from __future__ import annotations

import logging
import uuid

from learning_agent.agent.runtime import AgentRuntime, RunOutcome
from learning_agent.application.run_scheduler import RunScheduler
from learning_agent.domain.errors import AppError, ErrorCode

logger = logging.getLogger(__name__)

_TITLE_FALLBACK = "新对话"
_TITLE_MAX_CHARS = 40


class ChatService:
    """用户消息提交用例：支持同步执行与后台异步执行两种模式。"""

    def __init__(
        self,
        *,
        runtime: AgentRuntime,
        repositories: object,
        max_input_chars: int,
        scheduler: RunScheduler,
    ) -> None:
        self._runtime = runtime
        self._repositories = repositories
        self._max_input_chars = max_input_chars
        self._scheduler = scheduler

    async def submit_message(self, *, conversation_id: str, content: str) -> RunOutcome:
        """同步执行一次运行并返回最终结果（请求会等待运行结束）。"""

        text = self._validate(content)
        await self._prepare(conversation_id, text)
        logger.info("message_submitted", extra={"extra_fields": {"chars": len(text)}})
        return await self._runtime.run(
            conversation_id=conversation_id, user_message=text
        )

    async def start_run(self, *, conversation_id: str, content: str) -> str:
        """同步占位运行记录后转入后台执行，立即返回 ``run_id``。

        占位阶段若该对话已有活动运行，会抛出 ``RUN_IN_PROGRESS``（HTTP 409），
        保证并发提交被即时拒绝，而不是把冲突推迟到后台运行中。
        """

        text = self._validate(content)
        await self._prepare(conversation_id, text)
        run_id = str(uuid.uuid4())
        await self._runtime.begin(
            run_id=run_id, conversation_id=conversation_id, user_message=text
        )
        logger.info(
            "message_submitted",
            extra={"extra_fields": {"chars": len(text), "mode": "async"}},
        )
        self._scheduler.schedule(
            self._runtime.resume(
                run_id=run_id,
                conversation_id=conversation_id,
                user_message=text,
            )
        )
        return run_id

    def _validate(self, content: str) -> str:
        text = (content or "").strip()
        if not text:
            raise AppError(ErrorCode.INVALID_REQUEST, "消息内容不能为空")
        if len(text) > self._max_input_chars:
            raise AppError(
                ErrorCode.INVALID_REQUEST,
                f"消息长度超过上限（{self._max_input_chars} 字符）",
            )
        return text

    async def _prepare(self, conversation_id: str, text: str) -> None:
        conversation = await self._repositories.get_conversation(conversation_id)
        if conversation is None:
            raise AppError(ErrorCode.NOT_FOUND, "对话不存在或已被删除")
        await self._repositories.set_title_if_default(
            conversation_id, title=_derive_title(text)
        )


def _derive_title(text: str) -> str:
    single_line = " ".join(text.split())
    if len(single_line) <= _TITLE_MAX_CHARS:
        return single_line or _TITLE_FALLBACK
    return single_line[: _TITLE_MAX_CHARS - 1] + "…"
