"""Agent Runtime：一次 Agent Run 的生命周期编排。

职责：创建运行记录、组装上下文、驱动“模型 ↔ 工具”循环、校验来源标记、
持久化结果并记录运行状态。Runtime 不直接抓网页、不写 SQL、不实现模型协议。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from learning_agent.agent.citation_validator import validate_citations
from learning_agent.agent.context_builder import ContextBuilder, DocumentContext
from learning_agent.agent.prompts import PROMPT_VERSION, SYSTEM_PROMPT
from learning_agent.agent.tool_registry import (
    ToolExecution,
    ToolExecutionState,
    ToolRegistry,
)
from learning_agent.domain.contracts import SourceAllocator, ToolContext
from learning_agent.domain.entities import (
    DocumentContent,
    SourceReference,
    UsageRecord,
    estimate_cost,
)
from learning_agent.domain.enums import DocumentParseStatus, MessageRole, RunStatus
from learning_agent.domain.errors import AppError, ErrorCode
from learning_agent.models.base import ChatMessage, ModelClient
from learning_agent.observability.logging import (
    conversation_id_var,
    run_id_var,
)

logger = logging.getLogger(__name__)

_MAX_REUSABLE_DOCUMENTS = 5


class SourceRegistry(SourceAllocator):
    """一次运行内的来源注册表：来源编号只在本运行内有效。"""

    def __init__(self) -> None:
        self._counter = 0
        self._sources: dict[str, SourceReference] = {}

    def next_source_id(self) -> str:
        self._counter += 1
        return f"S{self._counter}"

    def register(self, reference: SourceReference) -> None:
        self._sources[reference.source_id] = reference

    def get(self, source_id: str) -> SourceReference | None:
        return self._sources.get(source_id)

    def ids(self) -> set[str]:
        return set(self._sources)

    def ordered(self, source_ids: Sequence[str]) -> tuple[SourceReference, ...]:
        result = []
        for source_id in source_ids:
            reference = self._sources.get(source_id)
            if reference is not None:
                result.append(reference)
        return tuple(result)


@dataclass(frozen=True)
class RunOutcome:
    """一次运行的最终结果（成功或失败都返回，供 API 如实展示）。"""

    run_id: str
    status: RunStatus
    content: str | None = None
    sources: tuple[SourceReference, ...] = ()
    usage: UsageRecord = field(default_factory=UsageRecord)
    model_name: str | None = None
    error_code: str | None = None
    error_category: str | None = None
    error_message: str | None = None
    notes: tuple[str, ...] = ()


class AgentRuntime:
    """驱动一次 Agent Run。"""

    def __init__(
        self,
        *,
        model_client: ModelClient,
        tools: ToolRegistry,
        context_builder: ContextBuilder,
        repositories: Any,
        model_timeout_seconds: float,
        run_timeout_seconds: float,
        max_model_rounds: int,
        max_tool_calls: int,
        max_output_tokens: int,
        max_tool_result_chars: int,
        price_input_per_1k: float = 0.0,
        price_output_per_1k: float = 0.0,
    ) -> None:
        self._model = model_client
        self._tools = tools
        self._context_builder = context_builder
        self._repositories = repositories
        self._model_timeout_seconds = model_timeout_seconds
        self._run_timeout_seconds = run_timeout_seconds
        self._max_model_rounds = max_model_rounds
        self._max_tool_calls = max_tool_calls
        self._max_output_tokens = max_output_tokens
        self._max_tool_result_chars = max_tool_result_chars
        self._price_input_per_1k = price_input_per_1k
        self._price_output_per_1k = price_output_per_1k

    def _apply_cost(self, usage: UsageRecord) -> UsageRecord:
        """按配置单价补全成本；未配置单价时保持 None。"""

        return estimate_cost(
            usage,
            input_price_per_1k=self._price_input_per_1k,
            output_price_per_1k=self._price_output_per_1k,
        )

    async def run(self, *, conversation_id: str, user_message: str) -> RunOutcome:
        """执行一次完整运行；请求级错误抛出，运行级失败返回失败结果。"""

        run_id = str(uuid.uuid4())
        await self.begin(
            run_id=run_id, conversation_id=conversation_id, user_message=user_message
        )
        return await self.resume(
            run_id=run_id, conversation_id=conversation_id, user_message=user_message
        )

    async def begin(
        self, *, run_id: str, conversation_id: str, user_message: str
    ) -> None:
        """创建运行记录（含用户消息）；已有活动运行时抛 RUN_IN_PROGRESS。

        与 :meth:`resume` 分离，便于后台执行时先同步占位，再异步执行，
        从而让提交方能立即拿到 ``run_id`` 并轮询。
        """

        await self._repositories.start_run(
            run_id=run_id,
            conversation_id=conversation_id,
            user_message=user_message,
            model_name=self._model_name,
            prompt_version=PROMPT_VERSION,
        )

    async def resume(
        self, *, run_id: str, conversation_id: str, user_message: str
    ) -> RunOutcome:
        """在运行记录已创建后执行剩余流程，并写入最终结果。"""

        run_token = run_id_var.set(run_id)
        conversation_token = conversation_id_var.set(conversation_id)
        started_perf = time.perf_counter()
        registry = SourceRegistry()
        usage = UsageRecord()
        model_name: str | None = None
        notes: list[str] = []
        try:
            logger.info("run_started")
            try:
                content, sources, usage, model_name, notes = await self._execute(
                    run_id=run_id,
                    conversation_id=conversation_id,
                    user_message=user_message,
                    registry=registry,
                )
            except TimeoutError as exc:
                timeout_error = AppError(
                    ErrorCode.RUN_TIMEOUT,
                    "本次运行超出总时限，已停止；请稍后重试或缩小问题范围",
                    detail=type(exc).__name__,
                )
                return await self._finish_failure(
                    run_id=run_id,
                    exc=timeout_error,
                    started_perf=started_perf,
                    usage=usage,
                    model_name=model_name,
                )
            except AppError as exc:
                return await self._finish_failure(
                    run_id=run_id,
                    exc=exc,
                    started_perf=started_perf,
                    usage=usage,
                    model_name=model_name,
                )
            except Exception as exc:  # noqa: BLE001 - 未知异常统一映射
                logger.exception("run_unexpected_error")
                internal = AppError(
                    ErrorCode.INTERNAL_ERROR,
                    "服务内部错误，本次运行已停止",
                    detail=type(exc).__name__,
                )
                return await self._finish_failure(
                    run_id=run_id,
                    exc=internal,
                    started_perf=started_perf,
                    usage=usage,
                    model_name=model_name,
                )

            duration_ms = int((time.perf_counter() - started_perf) * 1000)
            usage = self._apply_cost(usage)
            await self._repositories.complete_run(
                run_id=run_id,
                conversation_id=conversation_id,
                content=content,
                usage=usage,
                model_name=model_name,
                duration_ms=duration_ms,
            )
            logger.info(
                "run_completed",
                extra={
                    "extra_fields": {
                        "duration_ms": duration_ms,
                        "sources": len(sources),
                        "input_tokens": usage.input_tokens,
                        "output_tokens": usage.output_tokens,
                    }
                },
            )
            return RunOutcome(
                run_id=run_id,
                status=RunStatus.COMPLETED,
                content=content,
                sources=sources,
                usage=usage,
                model_name=model_name,
                notes=tuple(notes),
            )
        finally:
            run_id_var.reset(run_token)
            conversation_id_var.reset(conversation_token)

    @property
    def _model_name(self) -> str:
        return getattr(self._model, "model_name", None) or "unknown"

    async def _execute(
        self,
        *,
        run_id: str,
        conversation_id: str,
        user_message: str,
        registry: SourceRegistry,
    ) -> tuple[str, tuple[SourceReference, ...], UsageRecord, str | None, list[str]]:
        async with asyncio.timeout(self._run_timeout_seconds):
            history = await self._load_history(conversation_id, run_id=run_id)
            documents, reuse_notes = await self._prepare_reusable_documents(
                conversation_id, registry
            )
            bundle = self._context_builder.build(
                system_prompt=SYSTEM_PROMPT,
                history=history,
                user_message=user_message,
                documents=documents,
            )
            notes = list(bundle.notes) + reuse_notes
            logger.info(
                "context_built",
                extra={
                    "extra_fields": {
                        "history_messages": bundle.included_history,
                        "documents": bundle.included_documents,
                    }
                },
            )
            messages: list[ChatMessage] = list(bundle.messages)
            tool_schemas = self._tools.schemas()
            state = ToolExecutionState(max_calls=self._max_tool_calls)
            used_tool_call_ids: set[str] = set()
            usage = UsageRecord()
            model_name: str | None = None

            for round_index in range(self._max_model_rounds):
                response = await self._model.complete(
                    messages=messages,
                    tools=tool_schemas,
                    max_output_tokens=self._max_output_tokens,
                    timeout_seconds=self._model_timeout_seconds,
                )
                usage = usage + response.usage
                model_name = response.model_name
                assistant = response.message
                logger.info(
                    "model_call_finished",
                    extra={
                        "extra_fields": {
                            "round": round_index + 1,
                            "tool_calls": len(assistant.tool_calls),
                            "input_tokens": response.usage.input_tokens,
                            "output_tokens": response.usage.output_tokens,
                        }
                    },
                )
                messages.append(assistant)

                if not assistant.tool_calls:
                    content = (assistant.content or "").strip()
                    if not content:
                        raise AppError(
                            ErrorCode.MODEL_ERROR, "模型没有返回有效回答，请重试"
                        )
                    return await self._finalize_success(
                        run_id=run_id,
                        content=content,
                        registry=registry,
                        notes=notes,
                        usage=usage,
                        model_name=model_name,
                    )

                for call in assistant.tool_calls:
                    execution = await self._tools.execute(
                        tool_call_id=call.id,
                        name=call.name,
                        arguments=call.arguments,
                        context=ToolContext(
                            run_id=run_id,
                            conversation_id=conversation_id,
                            allocator=registry,
                        ),
                        state=state,
                    )
                    await self._persist_tool_execution(
                        run_id=run_id,
                        conversation_id=conversation_id,
                        execution=execution,
                        registry=registry,
                        used_tool_call_ids=used_tool_call_ids,
                    )
                    messages.append(
                        ChatMessage(
                            role="tool",
                            content=_tool_payload_text(
                                execution.model_payload, self._max_tool_result_chars
                            ),
                            tool_call_id=call.id,
                        )
                    )

            raise AppError(
                ErrorCode.MODEL_ROUND_LIMIT_EXCEEDED,
                f"模型调用轮数超过上限（{self._max_model_rounds} 轮），已停止本次运行",
            )

    async def _finalize_success(
        self,
        *,
        run_id: str,
        content: str,
        registry: SourceRegistry,
        notes: list[str],
        usage: UsageRecord,
        model_name: str | None,
    ) -> tuple[str, tuple[SourceReference, ...], UsageRecord, str | None, list[str]]:
        validation = validate_citations(content, allowed_source_ids=registry.ids())
        if validation.invalid_source_ids:
            notes.append(
                "模型输出了无效来源标记，已被移除："
                + "、".join(validation.invalid_source_ids)
            )
            logger.warning(
                "citation_invalid",
                extra={
                    "extra_fields": {
                        "invalid_source_ids": list(validation.invalid_source_ids)
                    }
                },
            )
        if validation.stripped_links:
            notes.append("模型输出中的外部链接已被移除，来源链接由系统生成")
            logger.info(
                "citation_links_stripped",
                extra={"extra_fields": {"links": len(validation.stripped_links)}},
            )
        if validation.used_source_ids:
            logger.info(
                "citation_validated",
                extra={"extra_fields": {"sources": list(validation.used_source_ids)}},
            )
        sources = registry.ordered(validation.used_source_ids)
        for reference in sources:
            if reference.reused:
                await self._repositories.link_run_source(
                    run_id=run_id,
                    snapshot_id=reference.snapshot_id,
                    source_id=reference.source_id,
                )
        return validation.content, sources, usage, model_name, notes

    async def _load_history(
        self, conversation_id: str, *, run_id: str
    ) -> list[ChatMessage]:
        records = await self._repositories.list_messages(conversation_id)
        messages: list[ChatMessage] = []
        for record in records:
            role = record.role
            if role not in {MessageRole.USER.value, MessageRole.ASSISTANT.value}:
                continue
            # 显式排除本次运行刚写入的用户消息，不依赖「最后一条」的位置假设
            if record.run_id == run_id:
                continue
            messages.append(ChatMessage(role=role, content=record.content))
        return messages

    async def _prepare_reusable_documents(
        self, conversation_id: str, registry: SourceRegistry
    ) -> tuple[list[DocumentContext], list[str]]:
        snapshots = await self._repositories.list_snapshots(
            conversation_id, provider="fetch"
        )
        documents: list[DocumentContext] = []
        notes: list[str] = []
        seen_urls: set[str] = set()
        for snapshot in snapshots:
            if snapshot.canonical_url in seen_urls:
                continue
            if len(documents) >= _MAX_REUSABLE_DOCUMENTS:
                notes.append("可复用文档数量超过上限，较早的文档未送入上下文")
                break
            seen_urls.add(snapshot.canonical_url)
            source_id = registry.next_source_id()
            registry.register(
                SourceReference(
                    source_id=source_id,
                    snapshot_id=snapshot.id,
                    canonical_url=snapshot.canonical_url,
                    title=snapshot.title,
                    fetched_at=snapshot.fetched_at,
                    truncated=snapshot.truncated,
                    reused=True,
                    provider="fetch",
                )
            )
            documents.append(
                DocumentContext(
                    source_id=source_id,
                    title=snapshot.title,
                    url=snapshot.canonical_url,
                    fetched_at=snapshot.fetched_at,
                    text=snapshot.extracted_text,
                    truncated=snapshot.truncated,
                )
            )
        return documents, notes

    async def _resolve_snapshot_id(
        self,
        *,
        conversation_id: str,
        document: DocumentContent,
        provider: str = "fetch",
    ) -> int:
        """复用同 URL 同内容哈希的既有快照，否则新建，避免重复累积。"""

        existing_id = await self._repositories.find_snapshot_id(
            conversation_id=conversation_id,
            canonical_url=document.canonical_url,
            content_hash=document.content_hash,
            provider=provider,
        )
        if existing_id is not None:
            return existing_id
        return await self._repositories.add_snapshot(
            conversation_id=conversation_id,
            content=document,
            parse_status=DocumentParseStatus.OK,
            provider=provider,
        )

    async def _persist_tool_execution(
        self,
        *,
        run_id: str,
        conversation_id: str,
        execution: ToolExecution,
        registry: SourceRegistry,
        used_tool_call_ids: set[str],
    ) -> None:
        for source in execution.sources:
            document = source.content
            snapshot_id = await self._resolve_snapshot_id(
                conversation_id=conversation_id,
                document=document,
                provider=source.provider,
            )
            reference = SourceReference(
                source_id=source.source_id,
                snapshot_id=snapshot_id,
                canonical_url=document.canonical_url,
                title=document.title,
                fetched_at=document.fetched_at,
                truncated=document.truncated,
                provider=source.provider,
            )
            await self._repositories.link_run_source(
                run_id=run_id,
                snapshot_id=snapshot_id,
                source_id=reference.source_id,
            )
            registry.register(reference)
        if execution.content is not None and execution.source_id is not None:
            document = execution.content
            snapshot_id = await self._resolve_snapshot_id(
                conversation_id=conversation_id, document=document
            )
            reference = SourceReference(
                source_id=execution.source_id,
                snapshot_id=snapshot_id,
                canonical_url=document.canonical_url,
                title=document.title,
                fetched_at=document.fetched_at,
                truncated=document.truncated,
                provider="fetch",
            )
            await self._repositories.link_run_source(
                run_id=run_id,
                snapshot_id=snapshot_id,
                source_id=reference.source_id,
            )
            registry.register(reference)
        await self._repositories.add_tool_call(
            run_id=run_id,
            tool_call_id=_unique_tool_call_id(
                execution.tool_call_id, used_tool_call_ids
            ),
            tool_name=execution.tool_name,
            status=str(execution.status),
            started_at=execution.started_at,
            finished_at=execution.finished_at,
            duration_ms=execution.duration_ms,
            arguments_summary=str(execution.result_summary.get("arguments", ""))[:500],
            result_summary=json.dumps(execution.result_summary, ensure_ascii=False)[
                :1000
            ],
            error_code=execution.error_code,
        )
        logger.info(
            "tool_call_finished",
            extra={
                "extra_fields": {
                    "tool_name": execution.tool_name,
                    "status": str(execution.status),
                    "duration_ms": execution.duration_ms,
                    "error_code": execution.error_code,
                }
            },
        )

    async def _finish_failure(
        self,
        *,
        run_id: str,
        exc: AppError,
        started_perf: float,
        usage: UsageRecord,
        model_name: str | None,
    ) -> RunOutcome:
        duration_ms = int((time.perf_counter() - started_perf) * 1000)
        usage = self._apply_cost(usage)
        try:
            await self._repositories.finish_run(
                run_id=run_id,
                status=RunStatus.FAILED,
                usage=usage,
                model_name=model_name,
                duration_ms=duration_ms,
                error_code=str(exc.code),
                error_category=str(exc.category),
            )
        except Exception:  # noqa: BLE001 - 失败记录失败时仍需返回结果
            logger.exception("run_failure_persist_failed")
        logger.warning(
            "run_failed",
            extra={
                "extra_fields": {
                    "error_code": str(exc.code),
                    "error_category": str(exc.category),
                    "duration_ms": duration_ms,
                }
            },
        )
        return RunOutcome(
            run_id=run_id,
            status=RunStatus.FAILED,
            usage=usage,
            model_name=model_name,
            error_code=str(exc.code),
            error_category=str(exc.category),
            error_message=exc.message,
        )


def _tool_payload_text(payload: dict[str, object], max_chars: int) -> str:
    text = json.dumps(payload, ensure_ascii=False)
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "…（工具结果超出长度上限，已截断）"


def _unique_tool_call_id(tool_call_id: str, used: set[str]) -> str:
    """保证同一次运行内的工具调用记录主键唯一（供应商可能复用调用 ID）。"""

    candidate = tool_call_id
    suffix = 2
    while candidate in used:
        candidate = f"{tool_call_id}~{suffix}"
        suffix += 1
    used.add(candidate)
    return candidate
