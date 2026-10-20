"""V3 Digest 与学习画像的同步持久化访问层。"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from sqlalchemy import and_, delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from learning_agent.domain.errors import AppError, ErrorCode
from learning_agent.domain.urls import normalize_external_url
from learning_agent.persistence.models import (
    LearningConcept,
    LearningEvent,
    LearningProfileSetting,
    LearningTopic,
    PushDelivery,
    PushDigest,
    PushDigestItem,
    PushFeedback,
    PushItem,
    PushItemIngestion,
    PushPreference,
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
    feedback_action: str | None = None
    ingestion_job_id: str | None = None
    ingestion_status: str | None = None


@dataclass(frozen=True)
class DeliveryRecord:
    digest_id: int
    channel: str
    status: str
    attempts: int
    last_error: str | None
    delivered_at: datetime | None


@dataclass(frozen=True)
class PushPreferenceRecord:
    topics: tuple[str, ...]
    source_ids: tuple[str, ...]
    top_n: int
    catch_up_days: int


@dataclass(frozen=True)
class IngestionLinkRecord:
    item_id: int
    job_id: str
    status: str
    error_message: str | None


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

    def set_source_enabled(self, source_id: str, *, enabled: bool) -> SourceRecord:
        with self._session() as session:
            source = session.get(PushSource, source_id)
            if source is None:
                raise AppError(ErrorCode.NOT_FOUND, "资讯来源不存在")
            source.enabled = enabled
            session.flush()
            return _to_source(source)

    def quarantine_invalid_push_items(self) -> int:
        """将历史非法文章 URL 隔离，避免继续出现在 Digest 或 RAG 操作中。"""

        with self._session() as session:
            rows = session.scalars(
                select(PushItem).where(PushItem.status != "quarantined")
            ).all()
            count = 0
            for row in rows:
                try:
                    normalize_external_url(row.canonical_url)
                except ValueError:
                    row.status = "quarantined"
                    count += 1
            return count

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

    # ---------- 用户偏好 ----------

    def get_push_preferences(
        self, *, default_top_n: int, default_catch_up_days: int
    ) -> PushPreferenceRecord:
        with self._session() as session:
            row = session.get(PushPreference, 1)
            if row is None:
                return PushPreferenceRecord(
                    topics=(),
                    source_ids=(),
                    top_n=default_top_n,
                    catch_up_days=default_catch_up_days,
                )
            return PushPreferenceRecord(
                topics=tuple(_load_string_list(row.topics_json)),
                source_ids=tuple(_load_string_list(row.source_ids_json)),
                top_n=row.top_n,
                catch_up_days=row.catch_up_days,
            )

    def save_push_preferences(
        self,
        *,
        topics: Sequence[str],
        source_ids: Sequence[str],
        top_n: int,
        catch_up_days: int,
    ) -> PushPreferenceRecord:
        with self._session() as session:
            row = session.get(PushPreference, 1)
            if row is None:
                row = PushPreference(id=1)
                session.add(row)
            row.topics_json = json.dumps(
                list(dict.fromkeys(topics)), ensure_ascii=False
            )
            row.source_ids_json = json.dumps(
                list(dict.fromkeys(source_ids)), ensure_ascii=False
            )
            row.top_n = top_n
            row.catch_up_days = catch_up_days
            session.flush()
            return PushPreferenceRecord(
                topics=tuple(_load_string_list(row.topics_json)),
                source_ids=tuple(_load_string_list(row.source_ids_json)),
                top_n=row.top_n,
                catch_up_days=row.catch_up_days,
            )

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
        url = normalize_external_url(url)
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
                select(
                    PushDigestItem,
                    PushItem,
                    PushSource,
                    PushFeedback,
                    PushItemIngestion,
                )
                .join(PushItem, PushDigestItem.item_id == PushItem.id)
                .join(PushSource, PushItem.source_id == PushSource.id)
                .outerjoin(
                    PushFeedback,
                    and_(
                        PushFeedback.item_id == PushItem.id,
                        PushFeedback.action.in_(("useful", "not_useful")),
                    ),
                )
                .outerjoin(
                    PushItemIngestion,
                    PushItemIngestion.item_id == PushItem.id,
                )
                .where(
                    PushDigestItem.digest_id == digest_id,
                    PushItem.status != "quarantined",
                )
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
                    feedback_action=feedback.action if feedback else None,
                    ingestion_job_id=ingestion.job_id if ingestion else None,
                    ingestion_status=ingestion.status if ingestion else None,
                )
                for link, item, source, feedback, ingestion in rows
            ]

    def get_digest_item(
        self, *, digest_date: str, item_id: int
    ) -> DigestItemRecord | None:
        with self._session() as session:
            digest = session.scalar(
                select(PushDigest).where(PushDigest.digest_date == digest_date)
            )
            if digest is None:
                return None
            row = session.execute(
                select(PushDigestItem, PushItem, PushSource)
                .join(PushItem, PushDigestItem.item_id == PushItem.id)
                .join(PushSource, PushItem.source_id == PushSource.id)
                .where(
                    PushDigestItem.digest_id == digest.id,
                    PushDigestItem.item_id == item_id,
                    PushItem.status != "quarantined",
                )
            ).first()
            if row is None:
                return None
            link, item, source = row
            return DigestItemRecord(
                item_id=item.id,
                title=item.title,
                url=item.canonical_url,
                summary=link.summary,
                topic=item.topic,
                source_name=source.name,
                published_at=item.published_at,
                rank=link.rank,
            )

    def save_feedback(self, *, item_id: int, action: str) -> None:
        with self._session() as session:
            session.execute(
                delete(PushFeedback).where(
                    PushFeedback.item_id == item_id,
                    PushFeedback.action.in_(("useful", "not_useful")),
                )
            )
            session.add(PushFeedback(item_id=item_id, action=action))

    def get_delivery(
        self, digest_id: int, *, channel: str = "feishu"
    ) -> DeliveryRecord | None:
        with self._session() as session:
            row = session.scalar(
                select(PushDelivery).where(
                    PushDelivery.digest_id == digest_id,
                    PushDelivery.channel == channel,
                )
            )
            if row is None:
                return None
            return DeliveryRecord(
                digest_id=row.digest_id,
                channel=row.channel,
                status=row.status,
                attempts=row.attempts,
                last_error=row.last_error,
                delivered_at=row.delivered_at,
            )

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

    def get_ingestion_link(self, item_id: int) -> IngestionLinkRecord | None:
        with self._session() as session:
            row = session.get(PushItemIngestion, item_id)
            if row is None:
                return None
            return IngestionLinkRecord(
                item_id=row.item_id,
                job_id=row.job_id,
                status=row.status,
                error_message=row.error_message,
            )

    def save_ingestion_link(
        self,
        *,
        item_id: int,
        job_id: str,
        status: str,
        error_message: str | None = None,
    ) -> IngestionLinkRecord:
        with self._session() as session:
            row = session.get(PushItemIngestion, item_id)
            if row is None:
                row = PushItemIngestion(item_id=item_id)
                session.add(row)
            row.job_id = job_id
            row.status = status
            row.error_message = error_message[:2000] if error_message else None
            session.flush()
            return IngestionLinkRecord(
                item_id=row.item_id,
                job_id=row.job_id,
                status=row.status,
                error_message=row.error_message,
            )

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
                    if event_type == "weak_signal" and not concept.user_corrected:
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
                        "id": topic.id,
                        "name": topic.name,
                        "status": topic.status,
                        "last_seen_at": topic.last_seen_at,
                        "concepts": [
                            {
                                "id": concept.id,
                                "name": concept.name,
                                "mastery": concept.mastery,
                                "confidence": concept.confidence,
                                "evidence_count": concept.evidence_count,
                                "user_corrected": concept.user_corrected,
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

    def learning_profile_enabled(self, *, default: bool = True) -> bool:
        with self._session() as session:
            row = session.get(LearningProfileSetting, 1)
            return default if row is None else bool(row.enabled)

    def set_learning_profile_enabled(self, *, enabled: bool) -> bool:
        with self._session() as session:
            row = session.get(LearningProfileSetting, 1)
            if row is None:
                row = LearningProfileSetting(id=1, enabled=enabled)
                session.add(row)
            else:
                row.enabled = enabled
            session.flush()
            return bool(row.enabled)

    def update_learning_topic_status(self, topic_id: int, *, status: str) -> None:
        with self._session() as session:
            row = session.get(LearningTopic, topic_id)
            if row is None:
                raise AppError(ErrorCode.NOT_FOUND, "学习主题不存在")
            row.status = status

    def delete_learning_topic(self, topic_id: int) -> None:
        with self._session() as session:
            row = session.get(LearningTopic, topic_id)
            if row is None:
                raise AppError(ErrorCode.NOT_FOUND, "学习主题不存在")
            session.delete(row)

    def update_learning_concept_mastery(self, concept_id: int, *, mastery: str) -> None:
        with self._session() as session:
            row = session.get(LearningConcept, concept_id)
            if row is None:
                raise AppError(ErrorCode.NOT_FOUND, "学习概念不存在")
            row.mastery = mastery
            row.user_corrected = True


def _to_source(row: PushSource) -> SourceRecord:
    return SourceRecord(
        id=row.id,
        name=row.name,
        feed_url=row.feed_url,
        domain=row.domain,
        category=row.category,
        trust_level=row.trust_level,
        auto_push=bool(row.auto_push),
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


def _load_string_list(value: str) -> list[str]:
    try:
        payload = json.loads(value)
    except (TypeError, ValueError):
        return []
    if not isinstance(payload, list):
        return []
    return [item.strip() for item in payload if isinstance(item, str) and item.strip()]
