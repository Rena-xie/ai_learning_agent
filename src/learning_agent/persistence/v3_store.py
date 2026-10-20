"""V3 Digest 与学习画像的同步持久化访问层。"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from learning_agent.domain.errors import AppError, ErrorCode
from learning_agent.persistence.models import (
    LearningConcept,
    LearningEvent,
    LearningTopic,
    PushDelivery,
    PushDigest,
    PushDigestItem,
    PushItem,
    PushSource,
    utcnow,
)


@dataclass(frozen=True)
class SourceRecord:
    id: str
    name: str
    feed_url: str
    domain: str
    category: str
    trust_level: str
    auto_push: bool
    auto_ingest: bool
    enabled: bool
    last_checked_at: datetime | None
    etag: str | None
    last_modified: str | None
    last_error: str | None


@dataclass(frozen=True)
class DigestRecord:
    id: int
    digest_date: str
    status: str
    item_count: int
    content: str
    error_message: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


@dataclass(frozen=True)
class DigestItemRecord:
    item_id: int
    title: str
    url: str
    summary: str
    topic: str
    source_name: str
    published_at: datetime | None
    rank: int


@dataclass(frozen=True)
class LearningProfileSnapshot:
    topics: tuple[dict[str, object], ...]


class SourceDefault(Protocol):
    id: str
    name: str
    feed_url: str
    domain: str
    category: str
    trust_level: str


class V3Store:
    """单进程、单用户的 V3 数据访问入口。"""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    @contextmanager
    def _session(self) -> Iterator[Session]:
        session = self._session_factory()
        try:
            yield session
            session.commit()
        except IntegrityError:
            session.rollback()
            raise
        finally:
            session.close()

    # ---------- 来源 ----------

    def seed_sources(self, defaults: Iterable[SourceDefault]) -> int:
        """只在来源表为空时写入默认来源，返回新增数量。"""

        with self._session() as session:
            if int(session.scalar(select(func.count()).select_from(PushSource)) or 0):
                return 0
            count = 0
            for item in defaults:
                source = PushSource(
                    id=str(item.id),
                    name=str(item.name),
                    feed_url=str(item.feed_url),
                    domain=str(item.domain),
                    category=str(item.category),
                    trust_level=str(item.trust_level),
                )
                session.add(source)
                count += 1
            return count

    def list_sources(self, *, enabled_only: bool = False) -> list[SourceRecord]:
        with self._session() as session:
            statement = select(PushSource).order_by(PushSource.name)
            if enabled_only:
                statement = statement.where(PushSource.enabled.is_(True))
            rows = session.scalars(statement).all()
            return [_to_source(row) for row in rows]

    def add_source(
        self,
        *,
        source_id: str,
        name: str,
        feed_url: str,
        domain: str,
        category: str,
        trust_level: str = "trusted",
    ) -> SourceRecord:
        with self._session() as session:
            source = PushSource(
                id=source_id,
                name=name.strip(),
                feed_url=feed_url.strip(),
                domain=domain.strip().lower(),
                category=category.strip() or "AI",
                trust_level=trust_level,
            )
            session.add(source)
            session.flush()
            return _to_source(source)

    def mark_source_checked(
        self,
        source_id: str,
        *,
        etag: str | None,
        last_modified: str | None,
        last_error: str | None = None,
    ) -> None:
        with self._session() as session:
            source = session.get(PushSource, source_id)
            if source is None:
                return
            source.last_checked_at = utcnow()
            source.etag = etag
            source.last_modified = last_modified
            source.last_error = last_error[:1000] if last_error else None

    # ---------- Digest ----------

    def get_digest(self, digest_date: str) -> DigestRecord | None:
        with self._session() as session:
            row = session.scalar(
                select(PushDigest).where(PushDigest.digest_date == digest_date)
            )
            return _to_digest(row) if row else None

    def create_digest(self, digest_date: str) -> DigestRecord:
        with self._session() as session:
            existing = session.scalar(
                select(PushDigest).where(PushDigest.digest_date == digest_date)
            )
            if existing is not None:
                return _to_digest(existing)
            row = PushDigest(
                digest_date=digest_date,
                status="queued",
                created_at=utcnow(),
            )
            session.add(row)
            try:
                session.flush()
            except IntegrityError:
                session.rollback()
                existing = session.scalar(
                    select(PushDigest).where(PushDigest.digest_date == digest_date)
                )
                if existing is None:
                    raise
                return _to_digest(existing)
            return _to_digest(row)

    def start_digest(self, digest_id: int) -> None:
        with self._session() as session:
            row = session.get(PushDigest, digest_id)
            if row is None:
                raise AppError(ErrorCode.NOT_FOUND, "Digest 不存在")
            row.status = "running"
            row.started_at = utcnow()
            row.error_message = None

    def complete_digest(
        self,
        digest_id: int,
        *,
        content: str,
        item_count: int,
        items: Sequence[tuple[int, str, str]],
    ) -> None:
        with self._session() as session:
            row = session.get(PushDigest, digest_id)
            if row is None:
                raise AppError(ErrorCode.NOT_FOUND, "Digest 不存在")
            session.execute(
                delete(PushDigestItem).where(PushDigestItem.digest_id == digest_id)
            )
            for rank, (item_id, summary, _title) in enumerate(items, start=1):
                session.add(
                    PushDigestItem(
                        digest_id=digest_id,
                        item_id=item_id,
                        rank=rank,
                        summary=summary,
                    )
                )
            row.status = "completed"
            row.content = content
            row.item_count = item_count
            row.completed_at = utcnow()
            row.error_message = None

    def fail_digest(self, digest_id: int, message: str) -> None:
        with self._session() as session:
            row = session.get(PushDigest, digest_id)
            if row is None:
                return
            row.status = "failed"
            row.error_message = message[:2000]
            row.completed_at = utcnow()

    def upsert_item(
        self,
        *,
        source_id: str,
        title: str,
        url: str,
        summary: str,
        topic: str,
        published_at: datetime | None,
        content_hash: str,
    ) -> tuple[int, bool]:
        with self._session() as session:
            existing = session.scalar(
                select(PushItem).where(PushItem.canonical_url == url)
            )
            if existing is not None:
                return existing.id, False
            row = PushItem(
                source_id=source_id,
                canonical_url=url,
                title=title,
                summary=summary,
                topic=topic,
                published_at=published_at,
                content_hash=content_hash,
            )
            session.add(row)
            session.flush()
            return row.id, True

    def list_digest_items(self, digest_id: int) -> list[DigestItemRecord]:
        with self._session() as session:
            rows = session.execute(
                select(PushDigestItem, PushItem, PushSource)
                .join(PushItem, PushDigestItem.item_id == PushItem.id)
                .join(PushSource, PushItem.source_id == PushSource.id)
                .where(PushDigestItem.digest_id == digest_id)
                .order_by(PushDigestItem.rank)
            ).all()
            return [
                DigestItemRecord(
                    item_id=item.id,
                    title=item.title,
                    url=item.canonical_url,
                    summary=link.summary,
                    topic=item.topic,
                    source_name=source.name,
                    published_at=item.published_at,
                    rank=link.rank,
                )
                for link, item, source in rows
            ]

    def save_delivery(
        self,
        *,
        digest_id: int,
        channel: str,
        status: str,
        attempts: int,
        error: str | None,
        delivered_at: datetime | None,
    ) -> None:
        with self._session() as session:
            row = session.scalar(
                select(PushDelivery).where(
                    PushDelivery.digest_id == digest_id,
                    PushDelivery.channel == channel,
                )
            )
            if row is None:
                row = PushDelivery(digest_id=digest_id, channel=channel)
                session.add(row)
            row.status = status
            row.attempts = attempts
            row.last_error = error[:2000] if error else None
            row.delivered_at = delivered_at

    # ---------- 学习画像 ----------

    def record_learning_event(
        self,
        *,
        topic_name: str,
        concept_name: str | None,
        conversation_id: str,
        run_id: str,
        event_type: str,
        evidence_text: str,
        confidence: float,
    ) -> None:
        with self._session() as session:
            topic = session.scalar(
                select(LearningTopic).where(LearningTopic.name == topic_name)
            )
            now = utcnow()
            if topic is None:
                topic = LearningTopic(name=topic_name, last_seen_at=now)
                session.add(topic)
                session.flush()
            else:
                topic.last_seen_at = now
            concept: LearningConcept | None = None
            if concept_name:
                concept = session.scalar(
                    select(LearningConcept).where(
                        LearningConcept.topic_id == topic.id,
                        LearningConcept.name == concept_name,
                    )
                )
                if concept is None:
                    concept = LearningConcept(
                        topic_id=topic.id,
                        name=concept_name,
                        mastery="weak" if event_type == "weak_signal" else "learning",
                        confidence=confidence,
                        evidence_count=1,
                        last_seen_at=now,
                    )
                    session.add(concept)
                    session.flush()
                else:
                    concept.evidence_count += 1
                    concept.confidence = min(1.0, max(concept.confidence, confidence))
                    concept.last_seen_at = now
                    if event_type == "weak_signal":
                        concept.mastery = "weak"
            session.add(
                LearningEvent(
                    topic_id=topic.id,
                    concept_id=concept.id if concept else None,
                    conversation_id=conversation_id,
                    run_id=run_id,
                    event_type=event_type,
                    evidence_text=evidence_text[:1000],
                    confidence=confidence,
                )
            )

    def learning_profile(self) -> LearningProfileSnapshot:
        with self._session() as session:
            topics = session.scalars(
                select(LearningTopic).order_by(LearningTopic.last_seen_at.desc())
            ).all()
            payload: list[dict[str, object]] = []
            for topic in topics:
                concepts = session.scalars(
                    select(LearningConcept)
                    .where(LearningConcept.topic_id == topic.id)
                    .order_by(LearningConcept.last_seen_at.desc())
                ).all()
                payload.append(
                    {
                        "name": topic.name,
                        "status": topic.status,
                        "last_seen_at": topic.last_seen_at,
                        "concepts": [
                            {
                                "name": concept.name,
                                "mastery": concept.mastery,
                                "confidence": concept.confidence,
                                "evidence_count": concept.evidence_count,
                            }
                            for concept in concepts
                        ],
                    }
                )
            return LearningProfileSnapshot(topics=tuple(payload))

    def clear_profile(self) -> None:
        with self._session() as session:
            session.execute(delete(LearningEvent))
            session.execute(delete(LearningConcept))
            session.execute(delete(LearningTopic))


def _to_source(row: PushSource) -> SourceRecord:
    return SourceRecord(
        id=row.id,
        name=row.name,
        feed_url=row.feed_url,
        domain=row.domain,
        category=row.category,
        trust_level=row.trust_level,
        auto_push=bool(row.auto_push),
        auto_ingest=bool(row.auto_ingest),
        enabled=bool(row.enabled),
        last_checked_at=row.last_checked_at,
        etag=row.etag,
        last_modified=row.last_modified,
        last_error=row.last_error,
    )


def _to_digest(row: PushDigest) -> DigestRecord:
    return DigestRecord(
        id=row.id,
        digest_date=row.digest_date,
        status=row.status,
        item_count=row.item_count,
        content=row.content,
        error_message=row.error_message,
        created_at=row.created_at,
        started_at=row.started_at,
        completed_at=row.completed_at,
    )
