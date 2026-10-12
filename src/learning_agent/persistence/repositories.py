"""Repository 层：所有数据库访问集中在此，业务模块不写原始 SQL。"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from learning_agent.domain.entities import DocumentContent, Section, UsageRecord
from learning_agent.domain.enums import MessageRole, RunStatus
from learning_agent.domain.errors import AppError, ErrorCode, PersistenceError
from learning_agent.persistence.models import (
    AgentRun,
    Conversation,
    DocumentSnapshot,
    Message,
    RunSource,
    ToolCall,
    utcnow,
)

_SECTION_EXCERPT_CHARS = 300


@dataclass(frozen=True)
class ConversationRecord:
    id: str
    title: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class MessageRecord:
    id: int
    conversation_id: str
    run_id: str | None
    role: str
    content: str
    created_at: datetime


@dataclass(frozen=True)
class SnapshotRecord:
    id: int
    conversation_id: str
    canonical_url: str
    title: str
    fetched_at: datetime
    content_hash: str
    extracted_text: str
    sections: tuple[Section, ...]
    truncated: bool
    parse_status: str
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class ToolCallView:
    id: str
    tool_name: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    duration_ms: int | None
    arguments_summary: str
    result_summary: str
    error_code: str | None


@dataclass(frozen=True)
class RunSourceView:
    source_id: str
    snapshot_id: int
    canonical_url: str
    title: str
    fetched_at: datetime
    truncated: bool


@dataclass(frozen=True)
class RunRecord:
    id: str
    conversation_id: str
    status: str
    model_name: str | None
    prompt_version: str | None
    started_at: datetime
    finished_at: datetime | None
    duration_ms: int | None
    input_tokens: int | None
    output_tokens: int | None
    estimated_cost: float | None
    error_code: str | None
    error_category: str | None
    tool_calls: tuple[ToolCallView, ...] = ()
    sources: tuple[RunSourceView, ...] = ()


class Repositories:
    """统一的数据访问入口。"""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    # ---------- 基础 ----------

    @contextmanager
    def _session(self) -> Iterator[Session]:
        session = self._session_factory()
        try:
            yield session
            session.commit()
        except AppError:
            session.rollback()
            raise
        except SQLAlchemyError as exc:
            session.rollback()
            raise PersistenceError("数据库操作失败", detail=type(exc).__name__) from exc
        finally:
            session.close()

    def ping(self) -> bool:
        try:
            with self._session() as session:
                session.execute(text("SELECT 1"))
        except AppError:
            return False
        return True

    # ---------- 对话 ----------

    def create_conversation(self, *, title: str) -> ConversationRecord:
        with self._session() as session:
            conversation = Conversation(title=title.strip() or "新对话")
            session.add(conversation)
            session.flush()
            return _to_conversation(conversation)

    def list_conversations(self) -> list[ConversationRecord]:
        with self._session() as session:
            rows = session.scalars(
                select(Conversation).order_by(Conversation.updated_at.desc())
            ).all()
            return [_to_conversation(row) for row in rows]

    def get_conversation(self, conversation_id: str) -> ConversationRecord | None:
        with self._session() as session:
            conversation = session.get(Conversation, conversation_id)
            return _to_conversation(conversation) if conversation else None

    def set_title_if_default(self, conversation_id: str, *, title: str) -> None:
        """首条用户消息到达时，把默认标题替换为消息摘要。"""

        with self._session() as session:
            conversation = session.get(Conversation, conversation_id)
            if conversation is None or conversation.title not in {"新对话", ""}:
                return
            conversation.title = title[:120] or "新对话"

    def delete_conversation(self, conversation_id: str) -> bool:
        """按级联策略删除对话及其消息、运行、来源关系与文档快照。"""

        with self._session() as session:
            conversation = session.get(Conversation, conversation_id)
            if conversation is None:
                return False
            session.delete(conversation)
            return True

    # ---------- 消息 ----------

    def add_message(
        self,
        *,
        conversation_id: str,
        role: str,
        content: str,
        run_id: str | None = None,
    ) -> MessageRecord:
        with self._session() as session:
            conversation = session.get(Conversation, conversation_id)
            if conversation is None:
                raise AppError(ErrorCode.NOT_FOUND, "对话不存在或已被删除")
            message = Message(
                conversation_id=conversation_id,
                role=role,
                content=content,
                run_id=run_id,
            )
            conversation.updated_at = utcnow()
            session.add(message)
            session.flush()
            return _to_message(message)

    def list_messages(
        self, conversation_id: str, *, limit: int | None = None
    ) -> list[MessageRecord]:
        with self._session() as session:
            statement = (
                select(Message)
                .where(Message.conversation_id == conversation_id)
                .order_by(Message.created_at, Message.id)
            )
            if limit is not None:
                statement = statement.limit(limit)
            rows = session.scalars(statement).all()
            return [_to_message(row) for row in rows]

    # ---------- 运行 ----------

    def create_run(
        self,
        *,
        run_id: str,
        conversation_id: str,
        model_name: str,
        prompt_version: str,
    ) -> None:
        with self._session() as session:
            conversation = session.get(Conversation, conversation_id)
            if conversation is None:
                raise AppError(ErrorCode.NOT_FOUND, "对话不存在或已被删除")
            run = AgentRun(
                id=run_id,
                conversation_id=conversation_id,
                status=RunStatus.PENDING.value,
                model_name=model_name,
                prompt_version=prompt_version,
                started_at=utcnow(),
            )
            session.add(run)
            try:
                session.flush()
            except IntegrityError as exc:
                session.rollback()
                raise AppError(
                    ErrorCode.RUN_IN_PROGRESS,
                    "该对话已有正在进行的运行，请等待完成后再发送消息",
                ) from exc

    def mark_run_running(self, run_id: str) -> None:
        with self._session() as session:
            run = session.get(AgentRun, run_id)
            if run is None:
                raise AppError(ErrorCode.NOT_FOUND, "运行记录不存在")
            run.status = RunStatus.RUNNING.value

    def finish_run(
        self,
        *,
        run_id: str,
        status: RunStatus,
        usage: UsageRecord,
        model_name: str | None = None,
        duration_ms: int | None = None,
        error_code: str | None = None,
        error_category: str | None = None,
    ) -> None:
        with self._session() as session:
            run = session.get(AgentRun, run_id)
            if run is None:
                raise AppError(ErrorCode.NOT_FOUND, "运行记录不存在")
            run.status = status.value
            run.finished_at = utcnow()
            run.duration_ms = duration_ms
            run.input_tokens = usage.input_tokens
            run.output_tokens = usage.output_tokens
            run.estimated_cost = usage.estimated_cost
            if model_name:
                run.model_name = model_name
            run.error_code = error_code
            run.error_category = error_category

    def get_run(self, run_id: str) -> RunRecord | None:
        with self._session() as session:
            run = session.get(AgentRun, run_id)
            if run is None:
                return None
            tool_calls = session.scalars(
                select(ToolCall)
                .where(ToolCall.run_id == run_id)
                .order_by(ToolCall.started_at)
            ).all()
            sources = session.execute(
                select(RunSource, DocumentSnapshot)
                .join(
                    DocumentSnapshot,
                    RunSource.document_snapshot_id == DocumentSnapshot.id,
                )
                .where(RunSource.run_id == run_id)
                .order_by(RunSource.source_id)
            ).all()
            return _to_run(
                run,
                tool_calls=[_to_tool_call(row) for row in tool_calls],
                sources=[
                    RunSourceView(
                        source_id=source.source_id,
                        snapshot_id=snapshot.id,
                        canonical_url=snapshot.canonical_url,
                        title=snapshot.title,
                        fetched_at=snapshot.fetched_at,
                        truncated=snapshot.truncated,
                    )
                    for source, snapshot in sources
                ],
            )

    def get_active_run(self, conversation_id: str) -> RunRecord | None:
        with self._session() as session:
            run = session.scalars(
                select(AgentRun)
                .where(
                    AgentRun.conversation_id == conversation_id,
                    AgentRun.status.in_(
                        [RunStatus.PENDING.value, RunStatus.RUNNING.value]
                    ),
                )
                .limit(1)
            ).first()
            return _to_run(run) if run else None

    def add_tool_call(
        self,
        *,
        run_id: str,
        tool_call_id: str,
        tool_name: str,
        status: str,
        started_at: datetime,
        finished_at: datetime | None,
        duration_ms: int | None,
        arguments_summary: str,
        result_summary: str,
        error_code: str | None,
    ) -> None:
        with self._session() as session:
            session.add(
                ToolCall(
                    id=tool_call_id,
                    run_id=run_id,
                    tool_name=tool_name,
                    status=status,
                    started_at=started_at,
                    finished_at=finished_at,
                    duration_ms=duration_ms,
                    arguments_summary=arguments_summary,
                    result_summary=result_summary,
                    error_code=error_code,
                )
            )

    # ---------- 文档快照与来源 ----------

    def add_snapshot(
        self,
        *,
        conversation_id: str,
        content: DocumentContent,
        parse_status: str,
    ) -> int:
        sections = [
            {
                "title": section.title,
                "level": section.level,
                "excerpt": section.text[:_SECTION_EXCERPT_CHARS],
            }
            for section in content.sections
        ]
        with self._session() as session:
            snapshot = DocumentSnapshot(
                conversation_id=conversation_id,
                canonical_url=content.canonical_url,
                title=content.title,
                fetched_at=content.fetched_at,
                content_hash=content.content_hash,
                extracted_text=content.content_text,
                sections_json=json.dumps(sections, ensure_ascii=False),
                truncated=content.truncated,
                parse_status=parse_status,
                warnings_json=json.dumps(list(content.warnings), ensure_ascii=False),
            )
            session.add(snapshot)
            session.flush()
            return snapshot.id

    def list_snapshots(
        self, conversation_id: str, *, limit: int = 20
    ) -> list[SnapshotRecord]:
        with self._session() as session:
            rows = session.scalars(
                select(DocumentSnapshot)
                .where(DocumentSnapshot.conversation_id == conversation_id)
                .order_by(
                    DocumentSnapshot.fetched_at.desc(), DocumentSnapshot.id.desc()
                )
                .limit(limit)
            ).all()
            return [_to_snapshot(row) for row in rows]

    def link_run_source(self, *, run_id: str, snapshot_id: int, source_id: str) -> None:
        with self._session() as session:
            existing = session.get(RunSource, (run_id, source_id))
            if existing is not None:
                return
            session.add(
                RunSource(
                    run_id=run_id, source_id=source_id, document_snapshot_id=snapshot_id
                )
            )

    def count_messages(self, conversation_id: str) -> int:
        with self._session() as session:
            return len(
                session.scalars(
                    select(Message.id).where(Message.conversation_id == conversation_id)
                ).all()
            )

    def get_message_by_run(
        self,
        *,
        conversation_id: str,
        run_id: str,
        role: str = MessageRole.ASSISTANT.value,
    ) -> MessageRecord | None:
        with self._session() as session:
            row = session.scalars(
                select(Message)
                .where(
                    Message.conversation_id == conversation_id,
                    Message.run_id == run_id,
                    Message.role == role,
                )
                .order_by(Message.id.desc())
                .limit(1)
            ).first()
            return _to_message(row) if row else None

    def purge_conversation_messages(self, conversation_id: str) -> None:
        """仅用于测试/维护：清空某对话的消息。"""

        with self._session() as session:
            session.execute(
                delete(Message).where(Message.conversation_id == conversation_id)
            )


def _to_conversation(row: Conversation) -> ConversationRecord:
    return ConversationRecord(
        id=row.id,
        title=row.title,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _to_message(row: Message) -> MessageRecord:
    return MessageRecord(
        id=row.id,
        conversation_id=row.conversation_id,
        run_id=row.run_id,
        role=row.role,
        content=row.content,
        created_at=row.created_at,
    )


def _to_snapshot(row: DocumentSnapshot) -> SnapshotRecord:
    sections: list[Section] = []
    if row.sections_json:
        try:
            raw = json.loads(row.sections_json)
        except json.JSONDecodeError:
            raw = []
        for item in raw if isinstance(raw, list) else []:
            if isinstance(item, dict):
                sections.append(
                    Section(
                        title=str(item.get("title", "")),
                        level=int(item.get("level", 0) or 0),
                        text=str(item.get("excerpt", "")),
                    )
                )
    warnings: tuple[str, ...] = ()
    if row.warnings_json:
        try:
            raw_warnings = json.loads(row.warnings_json)
        except json.JSONDecodeError:
            raw_warnings = []
        if isinstance(raw_warnings, list):
            warnings = tuple(str(item) for item in raw_warnings)
    return SnapshotRecord(
        id=row.id,
        conversation_id=row.conversation_id,
        canonical_url=row.canonical_url,
        title=row.title,
        fetched_at=row.fetched_at,
        content_hash=row.content_hash,
        extracted_text=row.extracted_text,
        sections=tuple(sections),
        truncated=row.truncated,
        parse_status=row.parse_status,
        warnings=warnings,
    )


def _to_tool_call(row: ToolCall) -> ToolCallView:
    return ToolCallView(
        id=row.id,
        tool_name=row.tool_name,
        status=row.status,
        started_at=row.started_at,
        finished_at=row.finished_at,
        duration_ms=row.duration_ms,
        arguments_summary=row.arguments_summary,
        result_summary=row.result_summary,
        error_code=row.error_code,
    )


def _to_run(
    row: AgentRun,
    tool_calls: list[ToolCallView] | None = None,
    sources: list[RunSourceView] | None = None,
) -> RunRecord:
    return RunRecord(
        id=row.id,
        conversation_id=row.conversation_id,
        status=row.status,
        model_name=row.model_name,
        prompt_version=row.prompt_version,
        started_at=row.started_at,
        finished_at=row.finished_at,
        duration_ms=row.duration_ms,
        input_tokens=row.input_tokens,
        output_tokens=row.output_tokens,
        estimated_cost=row.estimated_cost,
        error_code=row.error_code,
        error_category=row.error_category,
        tool_calls=tuple(tool_calls or ()),
        sources=tuple(sources or ()),
    )
