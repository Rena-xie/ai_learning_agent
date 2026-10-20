"""V3.2 学习笔记、自测、闪卡和复习记录的持久化访问层。"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import case, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from learning_agent.domain.errors import AppError, ErrorCode
from learning_agent.persistence.models import (
    LearningFlashcard,
    LearningNote,
    LearningNoteSource,
    LearningQuiz,
    LearningQuizQuestion,
    LearningReview,
    LearningTopic,
)


@dataclass(frozen=True)
class NoteSourceRecord:
    id: int
    label: str
    title: str
    publisher: str
    url: str
    provider: str
    excerpt: str


@dataclass(frozen=True)
class LearningNoteRecord:
    id: int
    push_item_id: int
    topic_id: int | None
    digest_date: str
    topic_name: str
    title: str
    content: str
    created_at: datetime
    updated_at: datetime
    sources: tuple[NoteSourceRecord, ...]


@dataclass(frozen=True)
class QuizQuestionRecord:
    id: int
    position: int
    question: str
    answer: str
    explanation: str
    source: NoteSourceRecord | None


@dataclass(frozen=True)
class LearningQuizRecord:
    id: int
    note_id: int
    created_at: datetime
    questions: tuple[QuizQuestionRecord, ...]


@dataclass(frozen=True)
class FlashcardRecord:
    id: int
    note_id: int
    topic_name: str
    question: str
    answer: str
    interval_days: int
    review_count: int
    lapse_count: int
    last_reviewed_at: datetime | None
    next_review_at: datetime
    created_at: datetime
    source: NoteSourceRecord | None


@dataclass(frozen=True)
class LearningProgressRecord:
    notes_total: int
    quizzes_total: int
    flashcards_total: int
    due_total: int
    reviews_total: int
    correct_reviews: int
    topics: tuple[dict[str, object], ...]


class LearningStore:
    """单用户学习闭环的数据访问入口。"""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    @contextmanager
    def _session(self) -> Iterator[Session]:
        session = self._session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    # ---------- 笔记 ----------

    def get_note(self, note_id: int) -> LearningNoteRecord | None:
        with self._session() as session:
            row = session.get(LearningNote, note_id)
            return self._note_view(session, row) if row is not None else None

    def get_note_by_item(self, push_item_id: int) -> LearningNoteRecord | None:
        with self._session() as session:
            row = session.scalar(
                select(LearningNote).where(LearningNote.push_item_id == push_item_id)
            )
            return self._note_view(session, row) if row is not None else None

    def list_notes(self, *, limit: int = 50) -> list[LearningNoteRecord]:
        with self._session() as session:
            rows = session.scalars(
                select(LearningNote)
                .order_by(LearningNote.created_at.desc())
                .limit(limit)
            ).all()
            return [self._note_view(session, row) for row in rows]

    def create_note(
        self,
        *,
        push_item_id: int,
        digest_date: str,
        topic_name: str,
        title: str,
        content: str,
        source: dict[str, str],
    ) -> LearningNoteRecord:
        try:
            with self._session() as session:
                topic = session.scalar(
                    select(LearningTopic).where(LearningTopic.name == topic_name)
                )
                if topic is None:
                    topic = LearningTopic(name=topic_name)
                    session.add(topic)
                    session.flush()
                note = LearningNote(
                    push_item_id=push_item_id,
                    topic_id=topic.id,
                    digest_date=digest_date,
                    topic_name=topic_name,
                    title=title,
                    content=content,
                )
                session.add(note)
                session.flush()
                session.add(
                    LearningNoteSource(
                        note_id=note.id,
                        label="S1",
                        title=source["title"],
                        publisher=source["publisher"],
                        url=source["url"],
                        provider=source.get("provider", "digest"),
                        excerpt=source.get("excerpt", ""),
                    )
                )
                session.flush()
                return self._note_view(session, note)
        except IntegrityError:
            existing = self.get_note_by_item(push_item_id)
            if existing is not None:
                return existing
            raise

    # ---------- 自测题 ----------

    def get_quiz_by_note(self, note_id: int) -> LearningQuizRecord | None:
        with self._session() as session:
            row = session.scalar(
                select(LearningQuiz).where(LearningQuiz.note_id == note_id)
            )
            return self._quiz_view(session, row) if row is not None else None

    def get_quiz(self, quiz_id: int) -> LearningQuizRecord | None:
        with self._session() as session:
            row = session.get(LearningQuiz, quiz_id)
            return self._quiz_view(session, row) if row is not None else None

    def create_quiz(
        self,
        *,
        note_id: int,
        questions: Sequence[dict[str, object]],
    ) -> LearningQuizRecord:
        try:
            with self._session() as session:
                quiz = LearningQuiz(note_id=note_id)
                session.add(quiz)
                session.flush()
                for position, item in enumerate(questions, start=1):
                    session.add(
                        LearningQuizQuestion(
                            quiz_id=quiz.id,
                            source_id=int(item["source_id"]),
                            position=position,
                            question=str(item["question"]),
                            answer=str(item["answer"]),
                            explanation=str(item.get("explanation", "")),
                        )
                    )
                session.flush()
                return self._quiz_view(session, quiz)
        except IntegrityError:
            existing = self.get_quiz_by_note(note_id)
            if existing is not None:
                return existing
            raise

    # ---------- 闪卡与复习 ----------

    def list_flashcards(self, *, note_id: int | None = None) -> list[FlashcardRecord]:
        with self._session() as session:
            statement = (
                select(LearningFlashcard, LearningNote)
                .join(LearningNote, LearningFlashcard.note_id == LearningNote.id)
                .order_by(LearningFlashcard.next_review_at, LearningFlashcard.id)
            )
            if note_id is not None:
                statement = statement.where(LearningFlashcard.note_id == note_id)
            rows = session.execute(statement).all()
            return [self._flashcard_view(session, card, note) for card, note in rows]

    def due_flashcards(
        self, *, now: datetime, limit: int = 30
    ) -> list[FlashcardRecord]:
        with self._session() as session:
            rows = session.execute(
                select(LearningFlashcard, LearningNote)
                .join(LearningNote, LearningFlashcard.note_id == LearningNote.id)
                .where(LearningFlashcard.next_review_at <= now)
                .order_by(LearningFlashcard.next_review_at, LearningFlashcard.id)
                .limit(limit)
            ).all()
            return [self._flashcard_view(session, card, note) for card, note in rows]

    def get_flashcard(self, flashcard_id: int) -> FlashcardRecord | None:
        with self._session() as session:
            row = session.execute(
                select(LearningFlashcard, LearningNote)
                .join(LearningNote, LearningFlashcard.note_id == LearningNote.id)
                .where(LearningFlashcard.id == flashcard_id)
            ).first()
            if row is None:
                return None
            card, note = row
            return self._flashcard_view(session, card, note)

    def create_flashcards(
        self,
        *,
        note_id: int,
        cards: Sequence[dict[str, object]],
        due_at: datetime,
    ) -> list[FlashcardRecord]:
        try:
            with self._session() as session:
                for item in cards:
                    session.add(
                        LearningFlashcard(
                            note_id=note_id,
                            source_id=int(item["source_id"]),
                            card_key=str(item["card_key"]),
                            question=str(item["question"]),
                            answer=str(item["answer"]),
                            next_review_at=due_at,
                        )
                    )
                session.flush()
        except IntegrityError:
            # 已有同一批卡片时保持幂等，返回当前完整集合。
            pass
        cards_view = self.list_flashcards(note_id=note_id)
        if cards_view:
            return cards_view
        raise AppError(ErrorCode.PERSISTENCE_ERROR, "闪卡保存失败")

    def review_flashcard(
        self,
        *,
        flashcard_id: int,
        rating: str,
        new_interval_days: int,
        reviewed_at: datetime,
        next_review_at: datetime,
    ) -> FlashcardRecord:
        with self._session() as session:
            card = session.get(LearningFlashcard, flashcard_id)
            if card is None:
                raise AppError(ErrorCode.NOT_FOUND, "闪卡不存在")
            previous_interval = card.interval_days
            card.interval_days = new_interval_days
            card.review_count += 1
            if rating == "again":
                card.lapse_count += 1
            card.last_reviewed_at = reviewed_at
            card.next_review_at = next_review_at
            card.updated_at = reviewed_at
            session.add(
                LearningReview(
                    flashcard_id=card.id,
                    rating=rating,
                    previous_interval_days=previous_interval,
                    new_interval_days=new_interval_days,
                    reviewed_at=reviewed_at,
                    next_review_at=next_review_at,
                )
            )
            session.flush()
            note = session.get(LearningNote, card.note_id)
            if note is None:
                raise AppError(ErrorCode.NOT_FOUND, "闪卡关联的笔记不存在")
            return self._flashcard_view(session, card, note)

    def progress(self, *, now: datetime) -> LearningProgressRecord:
        with self._session() as session:
            notes_total = int(
                session.scalar(select(func.count()).select_from(LearningNote)) or 0
            )
            quizzes_total = int(
                session.scalar(select(func.count()).select_from(LearningQuiz)) or 0
            )
            flashcards_total = int(
                session.scalar(select(func.count()).select_from(LearningFlashcard)) or 0
            )
            due_total = int(
                session.scalar(
                    select(func.count())
                    .select_from(LearningFlashcard)
                    .where(LearningFlashcard.next_review_at <= now)
                )
                or 0
            )

            note_rows = session.execute(
                select(LearningNote.topic_name, func.count(LearningNote.id)).group_by(
                    LearningNote.topic_name
                )
            ).all()
            card_rows = session.execute(
                select(
                    LearningNote.topic_name,
                    func.count(LearningFlashcard.id),
                    func.sum(
                        case(
                            (LearningFlashcard.next_review_at <= now, 1),
                            else_=0,
                        )
                    ),
                )
                .join(
                    LearningNote,
                    LearningFlashcard.note_id == LearningNote.id,
                )
                .group_by(LearningNote.topic_name)
            ).all()
            review_rows = session.execute(
                select(
                    LearningNote.topic_name,
                    LearningReview.rating,
                    func.count(LearningReview.id),
                )
                .join(
                    LearningFlashcard,
                    LearningReview.flashcard_id == LearningFlashcard.id,
                )
                .join(
                    LearningNote,
                    LearningFlashcard.note_id == LearningNote.id,
                )
                .group_by(LearningNote.topic_name, LearningReview.rating)
            ).all()

        topics: dict[str, dict[str, object]] = defaultdict(
            lambda: {
                "topic_name": "",
                "notes": 0,
                "flashcards": 0,
                "due": 0,
                "reviews": 0,
                "correct": 0,
                "lapses": 0,
            }
        )
        for topic_name, count in note_rows:
            topic = topics[str(topic_name)]
            topic["topic_name"] = str(topic_name)
            topic["notes"] = int(count)
        for topic_name, card_count, due_count in card_rows:
            topic = topics[str(topic_name)]
            topic["topic_name"] = str(topic_name)
            topic["flashcards"] = int(card_count or 0)
            topic["due"] = int(due_count or 0)

        reviews_total = 0
        correct_reviews = 0
        for topic_name, rating, count in review_rows:
            topic = topics[str(topic_name)]
            topic["topic_name"] = str(topic_name)
            count_value = int(count)
            topic["reviews"] = int(topic["reviews"]) + count_value
            reviews_total += count_value
            if rating in {"good", "easy"}:
                topic["correct"] = int(topic["correct"]) + count_value
                correct_reviews += count_value
            elif rating == "again":
                topic["lapses"] = int(topic["lapses"]) + count_value

        for topic in topics.values():
            reviews = int(topic["reviews"])
            correct = int(topic["correct"])
            topic["retention_rate"] = round(correct / reviews, 4) if reviews else None
        ordered_topics = tuple(
            sorted(
                topics.values(),
                key=lambda item: (
                    -int(item["lapses"]),
                    -int(item["due"]),
                    str(item["topic_name"]),
                ),
            )
        )
        return LearningProgressRecord(
            notes_total=notes_total,
            quizzes_total=quizzes_total,
            flashcards_total=flashcards_total,
            due_total=due_total,
            reviews_total=reviews_total,
            correct_reviews=correct_reviews,
            topics=ordered_topics,
        )

    @staticmethod
    def _note_view(session: Session, row: LearningNote) -> LearningNoteRecord:
        sources = session.scalars(
            select(LearningNoteSource)
            .where(LearningNoteSource.note_id == row.id)
            .order_by(LearningNoteSource.id)
        ).all()
        return LearningNoteRecord(
            id=row.id,
            push_item_id=row.push_item_id,
            topic_id=row.topic_id,
            digest_date=row.digest_date,
            topic_name=row.topic_name,
            title=row.title,
            content=row.content,
            created_at=row.created_at,
            updated_at=row.updated_at,
            sources=tuple(_to_source(source) for source in sources),
        )

    @staticmethod
    def _quiz_view(session: Session, row: LearningQuiz) -> LearningQuizRecord:
        questions = session.scalars(
            select(LearningQuizQuestion)
            .where(LearningQuizQuestion.quiz_id == row.id)
            .order_by(LearningQuizQuestion.position)
        ).all()
        source_ids = {
            question.source_id
            for question in questions
            if question.source_id is not None
        }
        sources = {
            source.id: _to_source(source)
            for source in session.scalars(
                select(LearningNoteSource).where(LearningNoteSource.id.in_(source_ids))
            ).all()
        }
        return LearningQuizRecord(
            id=row.id,
            note_id=row.note_id,
            created_at=row.created_at,
            questions=tuple(
                QuizQuestionRecord(
                    id=question.id,
                    position=question.position,
                    question=question.question,
                    answer=question.answer,
                    explanation=question.explanation,
                    source=sources.get(question.source_id or 0),
                )
                for question in questions
            ),
        )

    @staticmethod
    def _flashcard_view(
        session: Session,
        card: LearningFlashcard,
        note: LearningNote,
    ) -> FlashcardRecord:
        source = (
            session.get(LearningNoteSource, card.source_id)
            if card.source_id is not None
            else None
        )
        return FlashcardRecord(
            id=card.id,
            note_id=card.note_id,
            topic_name=note.topic_name,
            question=card.question,
            answer=card.answer,
            interval_days=card.interval_days,
            review_count=card.review_count,
            lapse_count=card.lapse_count,
            last_reviewed_at=card.last_reviewed_at,
            next_review_at=card.next_review_at,
            created_at=card.created_at,
            source=_to_source(source) if source is not None else None,
        )


def _to_source(row: LearningNoteSource) -> NoteSourceRecord:
    return NoteSourceRecord(
        id=row.id,
        label=row.label,
        title=row.title,
        publisher=row.publisher,
        url=row.url,
        provider=row.provider,
        excerpt=row.excerpt,
    )
