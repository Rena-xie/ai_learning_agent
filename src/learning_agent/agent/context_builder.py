"""上下文组装与 Token 预算管理。

预算为近似估算（DeepSeek 官方量级：中文约 0.6 token/字、英文约 0.3 token/字符），
按上限估算并在超预算时优先保留当前问题与最近文档，所有截断都会显式标注。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from learning_agent.domain.errors import AppError, ErrorCode
from learning_agent.models.base import ChatMessage

_CJK_RANGES = (
    (0x4E00, 0x9FFF),
    (0x3400, 0x4DBF),
    (0x3000, 0x303F),
    (0xFF00, 0xFFEF),
)
_CJK_TOKEN_RATIO = 0.6
_OTHER_TOKEN_RATIO = 0.3
_TOKENS_TO_CHARS = 1.6
_DOCUMENT_OVERHEAD_TOKENS = 60


def estimate_tokens(text: str) -> int:
    """近似估算 token 数量（保守上限）。"""

    if not text:
        return 0
    cjk = sum(1 for ch in text if _is_cjk(ch))
    other = len(text) - cjk
    return int(cjk * _CJK_TOKEN_RATIO + other * _OTHER_TOKEN_RATIO) + 1


def _is_cjk(ch: str) -> bool:
    code = ord(ch)
    return any(start <= code <= end for start, end in _CJK_RANGES)


@dataclass(frozen=True)
class DocumentContext:
    """送入上下文的文档片段。"""

    source_id: str
    title: str
    url: str
    fetched_at: datetime
    text: str
    truncated: bool = False


@dataclass(frozen=True)
class ContextBundle:
    """构建好的上下文。"""

    messages: tuple[ChatMessage, ...]
    included_history: int
    included_documents: int
    notes: tuple[str, ...] = ()


class ContextBuilder:
    """按预算裁剪历史消息与文档内容。"""

    def __init__(
        self,
        *,
        max_tokens: int,
        max_history_messages: int,
        output_reserve_tokens: int,
        tool_schema_reserve_tokens: int = 1200,
        max_document_chars: int = 24_000,
    ) -> None:
        self._max_tokens = max_tokens
        self._max_history_messages = max_history_messages
        self._output_reserve_tokens = output_reserve_tokens
        self._tool_schema_reserve_tokens = tool_schema_reserve_tokens
        self._max_document_chars = max_document_chars

    def build(
        self,
        *,
        system_prompt: str,
        history: Sequence[ChatMessage],
        user_message: str,
        documents: Sequence[DocumentContext],
    ) -> ContextBundle:
        notes: list[str] = []
        budget = (
            self._max_tokens
            - self._output_reserve_tokens
            - self._tool_schema_reserve_tokens
            - estimate_tokens(system_prompt)
            - estimate_tokens(user_message)
        )
        user_tokens = estimate_tokens(user_message)
        if user_tokens > budget:
            raise AppError(
                ErrorCode.CONTEXT_BUDGET_EXCEEDED,
                "当前问题超出上下文预算，请缩短问题后重试",
            )

        selected_history: list[ChatMessage] = []
        for message in reversed(history[-self._max_history_messages :]):
            cost = estimate_tokens(message.content or "") + 8
            if cost > budget:
                notes.append("较早的对话历史因上下文预算被省略")
                break
            budget -= cost
            selected_history.append(message)
        selected_history.reverse()

        document_messages: list[ChatMessage] = []
        included_documents: list[DocumentContext] = []
        for document in documents:
            text, truncated = _fit_document(
                document.text, self._max_document_chars, budget
            )
            cost = estimate_tokens(text) + _DOCUMENT_OVERHEAD_TOKENS
            if cost > budget:
                notes.append("部分已获取文档因上下文预算未送入模型，回答可能不完整")
                break
            budget -= cost
            included_documents.append(
                DocumentContext(
                    source_id=document.source_id,
                    title=document.title,
                    url=document.url,
                    fetched_at=document.fetched_at,
                    text=text,
                    truncated=document.truncated or truncated,
                )
            )
        if included_documents:
            from learning_agent.agent.prompts import build_documents_message

            document_messages.append(
                ChatMessage(
                    role="system", content=build_documents_message(included_documents)
                )
            )

        messages = [
            ChatMessage(role="system", content=system_prompt),
            *selected_history,
            *document_messages,
            ChatMessage(role="user", content=user_message),
        ]
        return ContextBundle(
            messages=tuple(messages),
            included_history=len(selected_history),
            included_documents=len(included_documents),
            notes=tuple(notes),
        )


def _fit_document(text: str, max_chars: int, budget_tokens: int) -> tuple[str, bool]:
    usable = max(0, budget_tokens - _DOCUMENT_OVERHEAD_TOKENS)
    char_cap = min(max_chars, max(200, int(usable * _TOKENS_TO_CHARS)))
    if len(text) <= char_cap:
        return text, False
    return text[:char_cap], True
