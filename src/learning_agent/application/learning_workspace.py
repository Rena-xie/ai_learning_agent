"""V3.2 学习笔记、自测、闪卡与复习闭环。"""

from __future__ import annotations

import html
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from learning_agent.application.learning_profile import LearningProfileService
from learning_agent.domain.errors import AppError, ErrorCode
from learning_agent.persistence.learning_store import (
    FlashcardRecord,
    LearningNoteRecord,
    LearningQuizRecord,
    LearningStore,
    NoteSourceRecord,
)
from learning_agent.persistence.v3_store import V3Store
from learning_agent.tools.knowledge.client import KnowledgeBasePort

_HTML_TAG = re.compile(r"<[^>]+>")
_WHITESPACE = re.compile(r"\s+")


class LearningWorkspaceService:
    """优先交付可用闭环；内容生成器未来可替换为模型实现。"""

    def __init__(
        self,
        *,
        store: LearningStore,
        digest_store: V3Store,
        profile_service: LearningProfileService,
        knowledge_client: KnowledgeBasePort | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._digest_store = digest_store
        self._profile_service = profile_service
        self._knowledge_client = knowledge_client
        self._now = now or (lambda: datetime.now(UTC))

    def create_note_from_digest(
        self, *, digest_date: str, item_id: int
    ) -> LearningNoteRecord:
        existing = self._store.get_note_by_item(item_id)
        if existing is not None:
            return existing
        item = self._digest_store.get_digest_item(
            digest_date=digest_date, item_id=item_id
        )
        if item is None:
            raise AppError(ErrorCode.NOT_FOUND, "该资讯不在指定日期的 Digest 中")
        title = _plain_text(item.title)[:500] or "未命名学习资料"
        summary = _plain_text(item.summary)[:4000]
        excerpt = summary or title
        topic_name = _plain_text(item.topic)[:120] or "AI"
        source = {
            "title": title,
            "publisher": _plain_text(item.source_name)[:160] or "未知来源",
            "url": item.url,
            "provider": "digest",
            "excerpt": excerpt,
        }
        content = _render_note(
            title=title,
            summary=excerpt,
            topic=topic_name,
            publisher=source["publisher"],
            url=item.url,
        )
        return self._store.create_note(
            push_item_id=item_id,
            digest_date=digest_date,
            topic_name=topic_name,
            title=title,
            content=content,
            source=source,
        )

    def list_notes(self, *, limit: int = 50) -> list[LearningNoteRecord]:
        return self._store.list_notes(limit=limit)

    def get_note(self, note_id: int) -> LearningNoteRecord:
        note = self._store.get_note(note_id)
        if note is None:
            raise AppError(ErrorCode.NOT_FOUND, "学习笔记不存在")
        return note

    def create_quiz(
        self, *, note_id: int, question_count: int = 4
    ) -> LearningQuizRecord:
        existing = self._store.get_quiz_by_note(note_id)
        if existing is not None:
            return existing
        note = self.get_note(note_id)
        source = note.sources[0]
        question_bank = _quiz_question_bank(note, source)
        selected = question_bank[: max(1, min(question_count, len(question_bank)))]
        return self._store.create_quiz(note_id=note_id, questions=selected)

    def get_quiz(self, quiz_id: int) -> LearningQuizRecord:
        quiz = self._store.get_quiz(quiz_id)
        if quiz is None:
            raise AppError(ErrorCode.NOT_FOUND, "自测题不存在")
        return quiz

    def create_flashcards(
        self, *, note_id: int, card_count: int = 4
    ) -> list[FlashcardRecord]:
        existing = self._store.list_flashcards(note_id=note_id)
        if existing:
            return existing
        note = self.get_note(note_id)
        source = note.sources[0]
        card_bank = _flashcard_bank(note, source)
        selected = card_bank[: max(1, min(card_count, len(card_bank)))]
        return self._store.create_flashcards(
            note_id=note_id,
            cards=selected,
            due_at=self._now(),
        )

    def list_flashcards(self, *, note_id: int | None = None) -> list[FlashcardRecord]:
        return self._store.list_flashcards(note_id=note_id)

    def due_reviews(self, *, limit: int = 30) -> list[FlashcardRecord]:
        return self._store.due_flashcards(now=self._now(), limit=limit)

    def submit_review(self, *, flashcard_id: int, rating: str) -> FlashcardRecord:
        card = self._store.get_flashcard(flashcard_id)
        if card is None:
            raise AppError(ErrorCode.NOT_FOUND, "闪卡不存在")
        now = self._now()
        interval = _next_interval(
            previous_days=card.interval_days,
            review_count=card.review_count,
            rating=rating,
        )
        next_review_at = now + timedelta(days=interval)
        return self._store.review_flashcard(
            flashcard_id=flashcard_id,
            rating=rating,
            new_interval_days=interval,
            reviewed_at=now,
            next_review_at=next_review_at,
        )

    def progress(self) -> dict[str, object]:
        progress = self._store.progress(now=self._now())
        profile = self._profile_service.snapshot()
        weak_concepts: dict[str, list[str]] = {}
        for topic in profile.topics:
            weak = [
                str(concept["name"])
                for concept in topic["concepts"]  # type: ignore[union-attr]
                if str(concept["mastery"]) == "weak"
            ]
            if weak:
                weak_concepts[str(topic["name"])] = weak

        topics: list[dict[str, object]] = []
        for topic in progress.topics:
            item = dict(topic)
            name = str(item["topic_name"])
            item["weak_concepts"] = weak_concepts.get(name, [])
            item["needs_attention"] = bool(
                int(item["lapses"]) > 0 or item["weak_concepts"]
            )
            topics.append(item)
        retention_rate = (
            round(progress.correct_reviews / progress.reviews_total, 4)
            if progress.reviews_total
            else None
        )
        return {
            "notes_total": progress.notes_total,
            "quizzes_total": progress.quizzes_total,
            "flashcards_total": progress.flashcards_total,
            "due_total": progress.due_total,
            "reviews_total": progress.reviews_total,
            "correct_reviews": progress.correct_reviews,
            "retention_rate": retention_rate,
            "topics": topics,
        }

    async def search_collection(
        self, *, collection_id: str, question: str, top_k: int
    ) -> dict[str, object]:
        """当前只有总库；为后续 collection 路由保留稳定接口。"""

        if collection_id not in {"default", "knowledge_base"}:
            raise AppError(ErrorCode.NOT_FOUND, "知识库 collection 不存在")
        if self._knowledge_client is None:
            raise AppError(
                ErrorCode.KNOWLEDGE_BASE_UNAVAILABLE,
                "知识库服务未启用，暂时无法检索",
            )
        payload = await self._knowledge_client.search(question, top_k=top_k)
        return {
            "collection_id": "default",
            "question": str(payload.get("question") or question),
            "top_k": int(payload.get("top_k") or top_k),
            "evidences": payload.get("evidences") or [],
        }


def _plain_text(value: str | None) -> str:
    text = html.unescape(value or "")
    text = _HTML_TAG.sub(" ", text)
    return _WHITESPACE.sub(" ", text).strip()


def _render_note(
    *,
    title: str,
    summary: str,
    topic: str,
    publisher: str,
    url: str,
) -> str:
    return "\n".join(
        [
            f"# {title}",
            "",
            "## 来源摘要",
            "",
            f"{summary} [S1]",
            "",
            "## 学习上下文",
            "",
            f"- 学习主题：{topic} [S1]",
            f"- 发布来源：{publisher} [S1]",
            f"- 原始链接：{url} [S1]",
            "",
            "## 下一步",
            "",
            "- 对照原文核对摘要中的技术名词、版本和适用边界。",
            "- 用自己的话复述核心内容，并检查每项结论是否能由 [S1] 支持。",
        ]
    )


def _quiz_question_bank(
    note: LearningNoteRecord, source: NoteSourceRecord
) -> list[dict[str, object]]:
    return [
        {
            "source_id": source.id,
            "question": "这篇资料的核心主题是什么？",
            "answer": note.title,
            "explanation": f"答案来自 {source.label} 的来源标题。",
        },
        {
            "source_id": source.id,
            "question": "来源摘要如何概括这篇资料？",
            "answer": source.excerpt,
            "explanation": f"答案直接取自 {source.label} 的来源摘要。",
        },
        {
            "source_id": source.id,
            "question": "这篇资料属于哪个学习主题？",
            "answer": note.topic_name,
            "explanation": f"主题元数据关联到 {source.label}。",
        },
        {
            "source_id": source.id,
            "question": "这篇资料的原始发布来源和链接是什么？",
            "answer": f"{source.publisher}，{source.url}",
            "explanation": f"来源链接见 {source.label}。",
        },
    ]


def _flashcard_bank(
    note: LearningNoteRecord, source: NoteSourceRecord
) -> list[dict[str, object]]:
    cards: list[dict[str, object]] = [
        {
            "source_id": source.id,
            "card_key": "title",
            "question": "用一句话回忆这篇资料的主题。",
            "answer": note.title,
        },
        {
            "source_id": source.id,
            "card_key": "summary",
            "question": "来源摘要的核心内容是什么？",
            "answer": source.excerpt,
        },
        {
            "source_id": source.id,
            "card_key": "topic",
            "question": "这篇资料属于哪个学习主题？",
            "answer": note.topic_name,
        },
        {
            "source_id": source.id,
            "card_key": "source",
            "question": "这篇资料来自哪里？",
            "answer": f"{source.publisher}：{source.url}",
        },
    ]
    return cards


def _next_interval(*, previous_days: int, review_count: int, rating: str) -> int:
    previous = max(1, previous_days)
    if rating == "again":
        return 1
    if rating == "hard":
        return min(30, max(1, round(previous * 1.2)))
    if rating == "easy":
        return 7 if review_count == 0 else min(120, max(7, round(previous * 3)))
    return 3 if review_count == 0 else min(60, max(3, round(previous * 2.2)))
