"""上下文组装与 Token 预算管理。

预算为近似估算（中文约 0.9 token/字、英文约 0.3 token/字符），再乘以可配置的安全系数
（``token_safety_factor``）以缓解低估；超预算时优先保留当前问题与最近文档，
所有截断都会显式标注。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from learning_agent.agent.prompts import build_documents_message
from learning_agent.domain.errors import AppError, ErrorCode
from learning_agent.models.base import ChatMessage

_CJK_RANGES = (
    (0x4E00, 0x9FFF),
    (0x3400, 0x4DBF),
    (0x3000, 0x303F),
    (0xFF00, 0xFFEF),
)
_CJK_TOKEN_RATIO = 0.9
_OTHER_TOKEN_RATIO = 0.3
_DOCUMENT_OVERHEAD_TOKENS = 60


def estimate_tokens(text: str, *, safety_factor: float = 1.0) -> int:
    """近似估算 token 数量（按上限估算，可乘以安全系数）。"""

    if not text:
        return 0
    cjk = sum(1 for ch in text if _is_cjk(ch))
    other = len(text) - cjk
    raw = (cjk * _CJK_TOKEN_RATIO + other * _OTHER_TOKEN_RATIO) * safety_factor
    return int(raw) + 1


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
        token_safety_factor: float = 1.0,
    ) -> None:
        self._max_tokens = max_tokens
        self._max_history_messages = max_history_messages
        self._output_reserve_tokens = output_reserve_tokens
        self._tool_schema_reserve_tokens = tool_schema_reserve_tokens
        self._max_document_chars = max_document_chars
        self._token_safety_factor = token_safety_factor

    def _estimate(self, text: str) -> int:
        """按本实例的安全系数估算 token 数。"""

        return estimate_tokens(text, safety_factor=self._token_safety_factor)

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
            - self._estimate(system_prompt)
            - self._estimate(user_message)
        )
        user_tokens = self._estimate(user_message)
        if user_tokens > budget:
            raise AppError(
                ErrorCode.CONTEXT_BUDGET_EXCEEDED,
                "当前问题超出上下文预算，请缩短问题后重试",
            )

        selected_history: list[ChatMessage] = []
        for message in reversed(history[-self._max_history_messages :]):
            cost = self._estimate(message.content or "") + 8
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
                document.text,
                self._max_document_chars,
                budget,
                safety_factor=self._token_safety_factor,
            )
            cost = self._estimate(text) + _DOCUMENT_OVERHEAD_TOKENS
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
            document_messages.append(
                ChatMessage(
                    role="user", content=build_documents_message(included_documents)
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


def _fit_document(
    text: str,
    max_chars: int,
    budget_tokens: int,
    *,
    safety_factor: float = 1.0,
) -> tuple[str, bool]:
    """按预算截断文档。

    字符上限由文本自身的字符构成与安全系数反推，保证截断后的估算 token 数
    不超过预算（此前使用固定 1.6 字符/token，中文比例提高到 0.9 后会把
    本可截断容纳的文档整体丢弃）。
    """

    usable = max(0, budget_tokens - _DOCUMENT_OVERHEAD_TOKENS)
    char_cap = min(max_chars, _chars_within_tokens(text, usable, safety_factor))
    if len(text) <= char_cap:
        return text, False
    return text[:char_cap], True


def _chars_within_tokens(text: str, tokens: int, safety_factor: float) -> int:
    """按文本的中英文构成估算可容纳的字符数（保守取值）。"""

    if tokens <= 0 or not text:
        return 0
    cjk = sum(1 for ch in text if _is_cjk(ch))
    other = len(text) - cjk
    per_char = (
        (cjk * _CJK_TOKEN_RATIO + other * _OTHER_TOKEN_RATIO)
        / len(text)
        * safety_factor
    )
    if per_char <= 0:
        return 0
    return max(1, int((tokens - 1) / per_char))
