"""聊天应用服务：校验输入、协调对话与 Agent Run。"""

from __future__ import annotations

import logging

from learning_agent.agent.runtime import AgentRuntime, RunOutcome
from learning_agent.domain.errors import AppError, ErrorCode

logger = logging.getLogger(__name__)

_TITLE_FALLBACK = "新对话"
_TITLE_MAX_CHARS = 40


class ChatService:
    """用户消息提交用例。"""

    def __init__(
        self,
        *,
        runtime: AgentRuntime,
        repositories: object,
        max_input_chars: int,
    ) -> None:
        self._runtime = runtime
        self._repositories = repositories
        self._max_input_chars = max_input_chars

    async def submit_message(self, *, conversation_id: str, content: str) -> RunOutcome:
        text = (content or "").strip()
        if not text:
            raise AppError(ErrorCode.INVALID_REQUEST, "消息内容不能为空")
        if len(text) > self._max_input_chars:
            raise AppError(
                ErrorCode.INVALID_REQUEST,
                f"消息长度超过上限（{self._max_input_chars} 字符）",
            )
        conversation = await self._repositories.get_conversation(conversation_id)
        if conversation is None:
            raise AppError(ErrorCode.NOT_FOUND, "对话不存在或已被删除")
        await self._repositories.set_title_if_default(
            conversation_id, title=_derive_title(text)
        )
        logger.info(
            "message_submitted",
            extra={"extra_fields": {"chars": len(text)}},
        )
        return await self._runtime.run(
            conversation_id=conversation_id, user_message=text
        )


def _derive_title(text: str) -> str:
    single_line = " ".join(text.split())
    if len(single_line) <= _TITLE_MAX_CHARS:
        return single_line or _TITLE_FALLBACK
    return single_line[: _TITLE_MAX_CHARS - 1] + "…"
