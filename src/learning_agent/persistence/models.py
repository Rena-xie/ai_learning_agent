"""SQLAlchemy ORM 模型（SQLite，v1.0 单用户单实例）。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator


def utcnow() -> datetime:
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator[datetime]):
    """SQLite 不保存时区；统一按 UTC 存取并返回带时区的时间。"""

    impl = DateTime
    cache_ok = True

    def process_bind_param(
        self, value: datetime | None, dialect: object
    ) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("需要带时区的时间戳")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(
        self, value: datetime | None, dialect: object
    ) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC)


class Base(DeclarativeBase):
    pass


class Conversation(Base):
    """对话。"""

    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    title: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, onupdate=utcnow
    )

    messages: Mapped[list[Message]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    runs: Mapped[list[AgentRun]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    snapshots: Mapped[list[DocumentSnapshot]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    ingestion_watches: Mapped[list[IngestionWatch]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class Message(Base):
    """对话消息（只存用户与助手消息，工具内部数据不进此表）。"""

    __tablename__ = "messages"
    __table_args__ = (
        Index("ix_messages_conversation_created", "conversation_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("conversations.id", ondelete="CASCADE")
    )
    run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(
        String(32), default="chat", server_default=text("'chat'")
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class IngestionWatch(Base):
    """异步入库任务的通知订阅（v2 M3）。"""

    __tablename__ = "ingestion_watches"
    __table_args__ = (
        Index("ix_ingestion_watches_status_next", "status", "next_check_at"),
        Index("ix_ingestion_watches_conversation", "conversation_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    conversation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("conversations.id", ondelete="CASCADE")
    )
    status: Mapped[str] = mapped_column(String(16), default="watching")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_checked_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    next_check_at: Mapped[datetime] = mapped_column(UTCDateTime)
    notified_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    conversation: Mapped[Conversation] = relationship(
        back_populates="ingestion_watches"
    )


class AgentRun(Base):
    """一次 Agent 运行记录。"""

    __tablename__ = "agent_runs"
    __table_args__ = (
        Index(
            "uq_agent_runs_active_conversation",
            "conversation_id",
            unique=True,
            sqlite_where=text("status IN ('pending', 'running')"),
        ),
        Index("ix_agent_runs_conversation_created", "conversation_id", "started_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("conversations.id", ondelete="CASCADE")
    )
    status: Mapped[str] = mapped_column(String(16), default="pending")
    model_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    estimated_cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_category: Mapped[str | None] = mapped_column(String(32), nullable=True)

    conversation: Mapped[Conversation] = relationship(back_populates="runs")
    tool_calls: Mapped[list[ToolCall]] = relationship(
        back_populates="run", cascade="all, delete-orphan", passive_deletes=True
    )


class ToolCall(Base):
    """工具调用记录（只存脱敏摘要，不存完整正文）。"""

    __tablename__ = "tool_calls"
    __table_args__ = (Index("ix_tool_calls_run_started", "run_id", "started_at"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("agent_runs.id", ondelete="CASCADE")
    )
    tool_name: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16))
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    arguments_summary: Mapped[str] = mapped_column(Text, default="")
    result_summary: Mapped[str] = mapped_column(Text, default="")
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)

    run: Mapped[AgentRun] = relationship(back_populates="tool_calls")


class DocumentSnapshot(Base):
    """文档快照（只保存清洗后的正文与必要元数据，不保存原始 HTML）。"""

    __tablename__ = "document_snapshots"
    __table_args__ = (
        Index(
            "ix_document_snapshots_conversation_fetched",
            "conversation_id",
            "fetched_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("conversations.id", ondelete="CASCADE")
    )
    canonical_url: Mapped[str] = mapped_column(String(2048))
    title: Mapped[str] = mapped_column(String(300))
    fetched_at: Mapped[datetime] = mapped_column(UTCDateTime)
    content_hash: Mapped[str] = mapped_column(String(64))
    extracted_text: Mapped[str] = mapped_column(Text)
    sections_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    truncated: Mapped[bool] = mapped_column(Boolean, default=False)
    parse_status: Mapped[str] = mapped_column(String(16), default="ok")
    provider: Mapped[str] = mapped_column(
        String(32), default="fetch", server_default=text("'fetch'")
    )
    warnings_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    conversation: Mapped[Conversation] = relationship(back_populates="snapshots")


class RunSource(Base):
    """运行与文档快照的来源关联。"""

    __tablename__ = "run_sources"

    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("agent_runs.id", ondelete="CASCADE"), primary_key=True
    )
    source_id: Mapped[str] = mapped_column(String(8), primary_key=True)
    document_snapshot_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("document_snapshots.id", ondelete="CASCADE")
    )


class PushSource(Base):
    """每日资讯来源注册表。"""

    __tablename__ = "push_sources"
    __table_args__ = (
        UniqueConstraint("feed_url", name="uq_push_sources_feed_url"),
        Index("ix_push_sources_enabled", "enabled"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    feed_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    domain: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str] = mapped_column(String(64), default="AI")
    trust_level: Mapped[str] = mapped_column(String(16), default="trusted")
    auto_push: Mapped[bool] = mapped_column(Boolean, default=True)
    auto_ingest: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    etag: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_modified: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, onupdate=utcnow
    )


class PushItem(Base):
    """从 RSS/Atom 抓取到的候选资讯；不自动进入 RAG。"""

    __tablename__ = "push_items"
    __table_args__ = (
        UniqueConstraint("canonical_url", name="uq_push_items_canonical_url"),
        Index("ix_push_items_published", "published_at"),
        Index("ix_push_items_source", "source_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("push_sources.id", ondelete="CASCADE")
    )
    canonical_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    summary: Mapped[str] = mapped_column(Text, default="")
    topic: Mapped[str] = mapped_column(String(64), default="AI")
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="new")


class PushDigest(Base):
    """某个日期的 Daily Digest。"""

    __tablename__ = "push_digests"
    __table_args__ = (
        UniqueConstraint("digest_date", name="uq_push_digests_date"),
        Index("ix_push_digests_status", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    digest_date: Mapped[str] = mapped_column(String(10), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="queued")
    item_count: Mapped[int] = mapped_column(Integer, default=0)
    content: Mapped[str] = mapped_column(Text, default="")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class PushDigestItem(Base):
    """Digest 与候选资讯的排序关联。"""

    __tablename__ = "push_digest_items"

    digest_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("push_digests.id", ondelete="CASCADE"), primary_key=True
    )
    item_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("push_items.id", ondelete="CASCADE"), primary_key=True
    )
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    summary: Mapped[str] = mapped_column(Text, default="")


class PushDelivery(Base):
    """Digest 的渠道投递记录。"""

    __tablename__ = "push_deliveries"
    __table_args__ = (
        UniqueConstraint("digest_id", "channel", name="uq_push_delivery_channel"),
        Index("ix_push_deliveries_status", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    digest_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("push_digests.id", ondelete="CASCADE")
    )
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class PushPreference(Base):
    """单用户资讯偏好；列表以 JSON 文本保存，便于后续扩展字段。"""

    __tablename__ = "push_preferences"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    topics_json: Mapped[str] = mapped_column(Text, default="[]")
    source_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    top_n: Mapped[int] = mapped_column(Integer, default=8)
    catch_up_days: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, onupdate=utcnow
    )


class PushFeedback(Base):
    """用户对单条资讯的反馈。"""

    __tablename__ = "push_feedback"
    __table_args__ = (
        UniqueConstraint("item_id", "action", name="uq_push_feedback_item_action"),
        Index("ix_push_feedback_item", "item_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    item_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("push_items.id", ondelete="CASCADE"), nullable=False
    )
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class PushItemIngestion(Base):
    """Digest 条目收藏到 RAG 的异步任务记录。"""

    __tablename__ = "push_item_ingestions"

    item_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("push_items.id", ondelete="CASCADE"),
        primary_key=True,
    )
    job_id: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="queued")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, onupdate=utcnow
    )


class LearningProfileSetting(Base):
    """学习画像的用户开关。"""

    __tablename__ = "learning_profile_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, onupdate=utcnow
    )


class LearningTopic(Base):
    """跨对话学习主题。"""

    __tablename__ = "learning_topics"
    __table_args__ = (UniqueConstraint("name", name="uq_learning_topics_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="learning")
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, onupdate=utcnow
    )


class LearningConcept(Base):
    """主题下的概念与掌握状态。"""

    __tablename__ = "learning_concepts"
    __table_args__ = (
        UniqueConstraint("topic_id", "name", name="uq_learning_concept_topic_name"),
        Index("ix_learning_concepts_topic", "topic_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    topic_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("learning_topics.id", ondelete="CASCADE")
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    mastery: Mapped[str] = mapped_column(String(16), default="learning")
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    evidence_count: Mapped[int] = mapped_column(Integer, default=0)
    user_corrected: Mapped[bool] = mapped_column(Boolean, default=False)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, onupdate=utcnow
    )


class LearningEvent(Base):
    """画像结论的证据事件。"""

    __tablename__ = "learning_events"
    __table_args__ = (Index("ix_learning_events_created", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    topic_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("learning_topics.id", ondelete="CASCADE")
    )
    concept_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("learning_concepts.id", ondelete="SET NULL")
    )
    conversation_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    evidence_text: Mapped[str] = mapped_column(Text, default="")
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class LearningNote(Base):
    """从 Digest 条目生成的学习笔记；当前单条资讯只生成一篇笔记。"""

    __tablename__ = "learning_notes"
    __table_args__ = (
        UniqueConstraint("push_item_id", name="uq_learning_notes_push_item"),
        Index("ix_learning_notes_topic_created", "topic_name", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    push_item_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("push_items.id", ondelete="CASCADE"), nullable=False
    )
    topic_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("learning_topics.id", ondelete="SET NULL"), nullable=True
    )
    digest_date: Mapped[str] = mapped_column(String(10), nullable=False)
    topic_name: Mapped[str] = mapped_column(String(120), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, onupdate=utcnow
    )


class LearningNoteSource(Base):
    """笔记中的可追溯来源，供测验和闪卡复用。"""

    __tablename__ = "learning_note_sources"
    __table_args__ = (
        UniqueConstraint("note_id", "label", name="uq_learning_note_source_label"),
        UniqueConstraint("note_id", "url", name="uq_learning_note_source_url"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    note_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("learning_notes.id", ondelete="CASCADE"), nullable=False
    )
    label: Mapped[str] = mapped_column(String(16), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    publisher: Mapped[str] = mapped_column(String(160), nullable=False)
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), default="digest")
    excerpt: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class LearningQuiz(Base):
    """一篇笔记对应一套可重复查看的自测题。"""

    __tablename__ = "learning_quizzes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    note_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("learning_notes.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class LearningQuizQuestion(Base):
    """自测题答案与来源关联。"""

    __tablename__ = "learning_quiz_questions"
    __table_args__ = (
        UniqueConstraint("quiz_id", "position", name="uq_learning_quiz_position"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    quiz_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("learning_quizzes.id", ondelete="CASCADE"), nullable=False
    )
    source_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("learning_note_sources.id", ondelete="SET NULL"),
        nullable=True,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    explanation: Mapped[str] = mapped_column(Text, default="")


class LearningFlashcard(Base):
    """带简单间隔复习状态的学习闪卡。"""

    __tablename__ = "learning_flashcards"
    __table_args__ = (
        UniqueConstraint("note_id", "card_key", name="uq_learning_flashcard_key"),
        Index("ix_learning_flashcards_due", "next_review_at"),
        Index("ix_learning_flashcards_note", "note_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    note_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("learning_notes.id", ondelete="CASCADE"), nullable=False
    )
    source_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("learning_note_sources.id", ondelete="SET NULL"),
        nullable=True,
    )
    card_key: Mapped[str] = mapped_column(String(64), nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    interval_days: Mapped[int] = mapped_column(Integer, default=1)
    review_count: Mapped[int] = mapped_column(Integer, default=0)
    lapse_count: Mapped[int] = mapped_column(Integer, default=0)
    last_reviewed_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime, nullable=True
    )
    next_review_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, onupdate=utcnow
    )


class LearningReview(Base):
    """闪卡复习历史，用于恢复复习进度和薄弱主题。"""

    __tablename__ = "learning_reviews"
    __table_args__ = (
        Index("ix_learning_reviews_card_time", "flashcard_id", "reviewed_at"),
        Index("ix_learning_reviews_time", "reviewed_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    flashcard_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("learning_flashcards.id", ondelete="CASCADE"),
        nullable=False,
    )
    rating: Mapped[str] = mapped_column(String(16), nullable=False)
    previous_interval_days: Mapped[int] = mapped_column(Integer, nullable=False)
    new_interval_days: Mapped[int] = mapped_column(Integer, nullable=False)
    reviewed_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    next_review_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
