"""上下文预算单元测试：历史裁剪、文档截断标记与预算超限。"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from learning_agent.agent.context_builder import ContextBuilder, DocumentContext
from learning_agent.domain.errors import AppError, ErrorCode
from learning_agent.models.base import ChatMessage


def make_builder(**overrides: object) -> ContextBuilder:
    options: dict[str, object] = {
        "max_tokens": 1_000,
        "max_history_messages": 10,
        "output_reserve_tokens": 50,
        "tool_schema_reserve_tokens": 50,
    }
    options.update(overrides)
    return ContextBuilder(**options)  # type: ignore[arg-type]


def test_keeps_recent_history_within_budget() -> None:
    history = [
        ChatMessage(role="user", content="第一轮问题"),
        ChatMessage(role="assistant", content="第一轮回答"),
        ChatMessage(role="user", content="第二轮问题"),
    ]
    bundle = make_builder().build(
        system_prompt="系统提示",
        history=history,
        user_message="当前问题",
        documents=[],
    )
    assert bundle.included_history == 3
    assert bundle.messages[-1].content == "当前问题"
    assert bundle.notes == ()


def test_drops_history_when_budget_is_tight() -> None:
    long_history = [
        ChatMessage(role="user", content="很长的历史消息" * 40),
        ChatMessage(role="assistant", content="很长的历史回答" * 40),
    ]
    bundle = make_builder(max_tokens=200, output_reserve_tokens=20).build(
        system_prompt="系统",
        history=long_history,
        user_message="当前问题",
        documents=[],
    )
    assert bundle.included_history < len(long_history)
    assert any("历史" in note for note in bundle.notes)
    assert bundle.messages[-1].content == "当前问题"


def test_document_is_truncated_and_marked() -> None:
    document = DocumentContext(
        source_id="S1",
        title="示例文档",
        url="https://docs.example.com/guide",
        fetched_at=datetime.now(UTC),
        text="正文内容" * 2_000,
    )
    bundle = make_builder().build(
        system_prompt="系统",
        history=[],
        user_message="请解释",
        documents=[document],
    )
    assert bundle.included_documents == 1
    documents_message = next(
        message
        for message in bundle.messages
        if message.content and "[S1]" in message.content
    )
    assert "（内容已截断）" in documents_message.content
    assert len(documents_message.content) < 8_000


def test_oversized_question_is_rejected() -> None:
    builder = make_builder(max_tokens=200, output_reserve_tokens=20)
    with pytest.raises(AppError) as excinfo:
        builder.build(
            system_prompt="系统",
            history=[],
            user_message="非常长的问题" * 200,
            documents=[],
        )
    assert excinfo.value.code is ErrorCode.CONTEXT_BUDGET_EXCEEDED
