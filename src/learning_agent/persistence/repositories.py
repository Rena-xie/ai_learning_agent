"""Repository 层：所有数据库访问集中在此，业务模块不写原始 SQL。"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from learning_agent.domain.entities import DocumentContent, Section, UsageRecord
from learning_agent.domain.enums import MessageRole, RunStatus, ToolCallStatus
from learning_agent.domain.errors import (
    AppError,
    ErrorCategory,
    ErrorCode,
    PersistenceError,
)
from learning_agent.persistence.models import (
    AgentRun,
    Conversation,
    DocumentSnapshot,
    IngestionWatch,
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
    kind: str
    created_at: datetime


@dataclass(frozen=True)
class IngestionWatchRecord:
    id: int
    job_id: str
    conversation_id: str
    status: str
    attempts: int
    last_checked_at: datetime | None
    next_check_at: datetime
    notified_at: datetime | None
    last_error: str | None
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
    provider: str
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
    provider: str


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
        kind: str = "chat",
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
                kind=kind,
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

    # ---------- 入库通知 ----------

    def create_ingestion_watch(
        self,
        *,
        job_id: str,
        conversation_id: str,
        next_check_at: datetime,
    ) -> IngestionWatchRecord:
        """登记一个任务通知订阅；同一 job 幂等。"""

        with self._session() as session:
            existing = session.scalar(
                select(IngestionWatch).where(IngestionWatch.job_id == job_id)
            )
            if existing is not None:
                return _to_watch(existing)
            if session.get(Conversation, conversation_id) is None:
                raise AppError(ErrorCode.NOT_FOUND, "对话不存在或已被删除")
            watch = IngestionWatch(
                job_id=job_id,
                conversation_id=conversation_id,
                status="watching",
                attempts=0,
                next_check_at=next_check_at,
                created_at=utcnow(),
            )
            session.add(watch)
            session.flush()
            return _to_watch(watch)

    def list_due_ingestion_watches(
        self, *, now: datetime, limit: int = 20
    ) -> list[IngestionWatchRecord]:
        with self._session() as session:
            rows = session.scalars(
                select(IngestionWatch)
                .where(
                    IngestionWatch.status == "watching",
                    IngestionWatch.next_check_at <= now,
                )
                .order_by(IngestionWatch.next_check_at, IngestionWatch.id)
                .limit(limit)
            ).all()
            return [_to_watch(row) for row in rows]

    def delete_finished_ingestion_watches(self, *, before: datetime) -> int:
        """清理已通知/失败的旧 watch，避免表无限增长。"""

        with self._session() as session:
            result = session.execute(
                delete(IngestionWatch).where(
                    IngestionWatch.status.in_(("notified", "failed")),
                    IngestionWatch.created_at < before,
                )
            )
            session.commit()
            return int(result.rowcount or 0)

    def save_ingestion_watch(
        self,
        *,
        job_id: str,
        status: str,
        attempts: int,
        last_checked_at: datetime | None,
        next_check_at: datetime,
        notified_at: datetime | None,
        last_error: str | None,
    ) -> None:
        """保存 watcher 状态；对话已删除时静默跳过。"""

        with self._session() as session:
            watch = session.scalar(
                select(IngestionWatch).where(IngestionWatch.job_id == job_id)
            )
            if watch is None:
                return
            watch.status = status
            watch.attempts = attempts
            watch.last_checked_at = last_checked_at
            watch.next_check_at = next_check_at
            watch.notified_at = notified_at
            watch.last_error = last_error

    def add_ingestion_notice(
        self, *, conversation_id: str, content: str
    ) -> MessageRecord:
        """写入确定性的入库通知消息（不创建 Run）。"""

        return self.add_message(
            conversation_id=conversation_id,
            role=MessageRole.ASSISTANT.value,
            content=content,
            kind="ingestion_notice",
        )

    def notify_ingestion_watch(
        self, *, job_id: str, content: str, notified_at: datetime
    ) -> bool:
        """原子地写入通知并标记 watcher，避免重复通知。"""

        with self._session() as session:
            watch = session.scalar(
                select(IngestionWatch).where(IngestionWatch.job_id == job_id)
            )
            if (
                watch is None
                or watch.status != "watching"
                or watch.notified_at is not None
            ):
                return False
            conversation = session.get(Conversation, watch.conversation_id)
            if conversation is None:
                return False
            conversation.updated_at = utcnow()
            session.add(
                Message(
                    conversation_id=watch.conversation_id,
                    role=MessageRole.ASSISTANT.value,
                    content=content,
                    run_id=None,
                    kind="ingestion_notice",
                )
            )
            watch.status = "notified"
            watch.notified_at = notified_at
            watch.last_checked_at = notified_at
            watch.next_check_at = notified_at
            watch.last_error = None
            session.flush()
            return True

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

    def start_run(
        self,
        *,
        run_id: str,
        conversation_id: str,
        user_message: str,
        model_name: str,
        prompt_version: str,
    ) -> None:
        """一次事务完成：创建运行、写入用户消息并置为 running。

        替代 create_run + add_message + mark_run_running 的三次提交，
        减少中间态与数据库往返；冲突时同样以「单对话单活动运行」约束拒绝。
        """

        with self._session() as session:
            conversation = session.get(Conversation, conversation_id)
            if conversation is None:
                raise AppError(ErrorCode.NOT_FOUND, "对话不存在或已被删除")
            session.add(
                AgentRun(
                    id=run_id,
                    conversation_id=conversation_id,
                    status=RunStatus.RUNNING.value,
                    model_name=model_name,
                    prompt_version=prompt_version,
                    started_at=utcnow(),
                )
            )
            session.add(
                Message(
                    conversation_id=conversation_id,
                    role=MessageRole.USER.value,
                    content=user_message,
                    run_id=run_id,
                )
            )
            conversation.updated_at = utcnow()
            try:
                session.flush()
            except IntegrityError as exc:
                session.rollback()
                raise AppError(
                    ErrorCode.RUN_IN_PROGRESS,
                    "该对话已有正在进行的运行，请等待完成后再发送消息",
                ) from exc

    def complete_run(
        self,
        *,
        run_id: str,
        conversation_id: str,
        content: str,
        usage: UsageRecord,
        model_name: str | None,
        duration_ms: int,
    ) -> None:
        """一次事务完成：写入助手消息并把运行置为 completed。"""

        with self._session() as session:
            run = session.get(AgentRun, run_id)
            if run is None:
                raise AppError(ErrorCode.NOT_FOUND, "运行记录不存在")
            conversation = session.get(Conversation, conversation_id)
            session.add(
                Message(
                    conversation_id=conversation_id,
                    role=MessageRole.ASSISTANT.value,
                    content=content,
                    run_id=run_id,
                )
            )
            if conversation is not None:
                conversation.updated_at = utcnow()
            run.status = RunStatus.COMPLETED.value
            run.finished_at = utcnow()
            run.duration_ms = duration_ms
            run.input_tokens = usage.input_tokens
            run.output_tokens = usage.output_tokens
            run.estimated_cost = usage.estimated_cost
            if model_name:
                run.model_name = model_name

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
                        provider=snapshot.provider,
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

    def fail_stale_runs(self) -> int:
        """把启动前遗留的 pending/running 运行标记为失败。

        单实例本地应用中，进程启动时不可能存在真正运行中的任务；残留记录只可能
        来自上次进程被中断，若不清理会永久占用「单对话单活动运行」约束。
        """

        with self._session() as session:
            result = session.execute(
                update(AgentRun)
                .where(
                    AgentRun.status.in_(
                        [RunStatus.PENDING.value, RunStatus.RUNNING.value]
                    )
                )
                .values(
                    status=RunStatus.FAILED.value,
                    finished_at=utcnow(),
                    error_code=ErrorCode.RUN_INTERRUPTED.value,
                    error_category=ErrorCategory.INTERNAL.value,
                )
            )
            return int(result.rowcount or 0)

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
        provider: str = "fetch",
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
                provider=provider,
                warnings_json=json.dumps(list(content.warnings), ensure_ascii=False),
            )
            session.add(snapshot)
            session.flush()
            return snapshot.id

    def find_snapshot_id(
        self,
        *,
        conversation_id: str,
        canonical_url: str,
        content_hash: str,
        provider: str = "fetch",
    ) -> int | None:
        """按 (对话, 规范化 URL, 内容哈希) 查找已有快照 ID，用于去重。"""

        with self._session() as session:
            row = session.execute(
                select(DocumentSnapshot.id)
                .where(
                    DocumentSnapshot.conversation_id == conversation_id,
                    DocumentSnapshot.canonical_url == canonical_url,
                    DocumentSnapshot.content_hash == content_hash,
                    DocumentSnapshot.provider == provider,
                )
                .order_by(DocumentSnapshot.id.desc())
                .limit(1)
            ).scalar_one_or_none()
            return int(row) if row is not None else None

    def list_snapshots(
        self,
        conversation_id: str,
        *,
        limit: int = 20,
        provider: str | None = None,
    ) -> list[SnapshotRecord]:
        with self._session() as session:
            condition = [DocumentSnapshot.conversation_id == conversation_id]
            if provider is not None:
                condition.append(DocumentSnapshot.provider == provider)
            rows = session.scalars(
                select(DocumentSnapshot)
                .where(*condition)
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

    def list_run_sources(
        self, conversation_id: str
    ) -> dict[str, tuple[RunSourceView, ...]]:
        """一次性取出该对话所有运行的来源，按 run_id 分组（避免逐条查询）。"""

        with self._session() as session:
            rows = session.execute(
                select(RunSource, DocumentSnapshot)
                .join(AgentRun, AgentRun.id == RunSource.run_id)
                .join(
                    DocumentSnapshot,
                    RunSource.document_snapshot_id == DocumentSnapshot.id,
                )
                .where(AgentRun.conversation_id == conversation_id)
                .order_by(RunSource.run_id, RunSource.source_id)
            ).all()
        grouped: dict[str, list[RunSourceView]] = {}
        for source, snapshot in rows:
            grouped.setdefault(source.run_id, []).append(
                RunSourceView(
                    source_id=source.source_id,
                    snapshot_id=snapshot.id,
                    canonical_url=snapshot.canonical_url,
                    title=snapshot.title,
                    fetched_at=snapshot.fetched_at,
                    truncated=snapshot.truncated,
                    provider=snapshot.provider,
                )
            )
        return {run_id: tuple(items) for run_id, items in grouped.items()}

    def run_statistics(self) -> dict[str, object]:
        """基础运行统计：用 SQL 聚合，避免把全部运行与工具调用载入内存。"""

        with self._session() as session:
            runs_total = session.scalar(select(func.count()).select_from(AgentRun)) or 0
            runs_completed = (
                session.scalar(
                    select(func.count())
                    .select_from(AgentRun)
                    .where(AgentRun.status == RunStatus.COMPLETED.value)
                )
                or 0
            )
            runs_failed = (
                session.scalar(
                    select(func.count())
                    .select_from(AgentRun)
                    .where(AgentRun.status == RunStatus.FAILED.value)
                )
                or 0
            )
            input_tokens_total = (
                session.scalar(
                    select(func.coalesce(func.sum(AgentRun.input_tokens), 0))
                )
                or 0
            )
            output_tokens_total = (
                session.scalar(
                    select(func.coalesce(func.sum(AgentRun.output_tokens), 0))
                )
                or 0
            )
            cost_sum = session.scalar(select(func.sum(AgentRun.estimated_cost)))
            durations = list(
                session.scalars(
                    select(AgentRun.duration_ms)
                    .where(AgentRun.duration_ms.is_not(None))
                    .order_by(AgentRun.duration_ms)
                ).all()
            )
            tool_calls_total = (
                session.scalar(select(func.count()).select_from(ToolCall)) or 0
            )
            tool_calls_failed = (
                session.scalar(
                    select(func.count())
                    .select_from(ToolCall)
                    .where(
                        ToolCall.status.in_(
                            [
                                ToolCallStatus.FAILED.value,
                                ToolCallStatus.REJECTED.value,
                            ]
                        )
                    )
                )
                or 0
            )
            document_fetches_total = (
                session.scalar(
                    select(func.count())
                    .select_from(ToolCall)
                    .where(ToolCall.tool_name == "fetch_document")
                )
                or 0
            )
            document_fetches_succeeded = (
                session.scalar(
                    select(func.count())
                    .select_from(ToolCall)
                    .where(
                        ToolCall.tool_name == "fetch_document",
                        ToolCall.status == ToolCallStatus.SUCCEEDED.value,
                    )
                )
                or 0
            )
            error_rows = session.execute(
                select(AgentRun.error_code, func.count())
                .where(AgentRun.error_code.is_not(None))
                .group_by(AgentRun.error_code)
            ).all()
            knowledge_tool_names = (
                "ingest_urls",
                "get_ingestion",
                "list_sources",
                "search_knowledge",
            )
            knowledge_tool_calls = (
                session.scalar(
                    select(func.count())
                    .select_from(ToolCall)
                    .where(ToolCall.tool_name.in_(knowledge_tool_names))
                )
                or 0
            )
            knowledge_tool_failed = (
                session.scalar(
                    select(func.count())
                    .select_from(ToolCall)
                    .where(
                        ToolCall.tool_name.in_(knowledge_tool_names),
                        ToolCall.status.in_(
                            [
                                ToolCallStatus.FAILED.value,
                                ToolCallStatus.REJECTED.value,
                                ToolCallStatus.TIMEOUT.value,
                            ]
                        ),
                    )
                )
                or 0
            )
            knowledge_tool_timeout = (
                session.scalar(
                    select(func.count())
                    .select_from(ToolCall)
                    .where(
                        ToolCall.tool_name.in_(knowledge_tool_names),
                        ToolCall.status == ToolCallStatus.TIMEOUT.value,
                    )
                )
                or 0
            )
            knowledge_durations = list(
                session.scalars(
                    select(ToolCall.duration_ms).where(
                        ToolCall.tool_name.in_(knowledge_tool_names),
                        ToolCall.duration_ms.is_not(None),
                    )
                ).all()
            )
            ingestion_watch_backlog = (
                session.scalar(
                    select(func.count())
                    .select_from(IngestionWatch)
                    .where(IngestionWatch.status == "watching")
                )
                or 0
            )

        error_counts = {str(code): int(count) for code, count in error_rows}
        return {
            "runs_total": runs_total,
            "runs_completed": runs_completed,
            "runs_failed": runs_failed,
            "success_rate": (
                round(runs_completed / runs_total, 4) if runs_total else None
            ),
            "failure_rate": round(runs_failed / runs_total, 4) if runs_total else None,
            "duration_ms_avg": (
                int(sum(durations) / len(durations)) if durations else None
            ),
            "duration_ms_p50": durations[len(durations) // 2] if durations else None,
            "duration_ms_p95": (
                durations[min(len(durations) - 1, int(len(durations) * 0.95))]
                if durations
                else None
            ),
            "input_tokens_total": int(input_tokens_total),
            "output_tokens_total": int(output_tokens_total),
            "estimated_cost_total": (
                round(float(cost_sum), 6) if cost_sum is not None else None
            ),
            "tool_calls_total": tool_calls_total,
            "tool_calls_failed": tool_calls_failed,
            "document_fetches_total": document_fetches_total,
            "document_fetch_success_rate": (
                round(document_fetches_succeeded / document_fetches_total, 4)
                if document_fetches_total
                else None
            ),
            "knowledge_tool_calls": int(knowledge_tool_calls),
            "knowledge_tool_failed": int(knowledge_tool_failed),
            "knowledge_tool_failure_rate": (
                round(knowledge_tool_failed / knowledge_tool_calls, 4)
                if knowledge_tool_calls
                else None
            ),
            "knowledge_tool_timeout": int(knowledge_tool_timeout),
            "knowledge_tool_timeout_rate": (
                round(knowledge_tool_timeout / knowledge_tool_calls, 4)
                if knowledge_tool_calls
                else None
            ),
            "knowledge_tool_avg_ms": (
                int(sum(knowledge_durations) / len(knowledge_durations))
                if knowledge_durations
                else None
            ),
            "ingestion_watch_backlog": int(ingestion_watch_backlog),
            "error_codes": dict(
                sorted(error_counts.items(), key=lambda item: (-item[1], item[0]))
            ),
        }

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
        kind=row.kind,
        created_at=row.created_at,
    )


def _to_watch(row: IngestionWatch) -> IngestionWatchRecord:
    return IngestionWatchRecord(
        id=row.id,
        job_id=row.job_id,
        conversation_id=row.conversation_id,
        status=row.status,
        attempts=row.attempts,
        last_checked_at=row.last_checked_at,
        next_check_at=row.next_check_at,
        notified_at=row.notified_at,
        last_error=row.last_error,
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
        provider=row.provider,
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
