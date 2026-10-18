"""知识库工具：异步入库、任务查询与来源列表（v2 M2）。"""

from __future__ import annotations

import hashlib
import logging
from typing import Any, Protocol

from pydantic import BaseModel, Field

from learning_agent.domain.contracts import ToolContext
from learning_agent.domain.entities import ToolOutcome
from learning_agent.domain.errors import AppError, ErrorCode
from learning_agent.tools.knowledge.client import KnowledgeBasePort

logger = logging.getLogger(__name__)

_MAX_ITEMS = 5
_MAX_URL_CHARS = 2_048
_TERMINAL_STATUSES = frozenset({"completed", "partial", "failed"})


class WatchRegistrar(Protocol):
    """入库完成后写入对话通知的注册接口。"""

    async def register(self, *, job_id: str, conversation_id: str) -> None: ...


class IngestUrlsArgs(BaseModel):
    """批量入库参数。"""

    urls: list[str] = Field(
        min_length=1,
        description="要长期保存到知识库的公开网页 URL 列表",
    )


class GetIngestionArgs(BaseModel):
    """任务查询参数。"""

    job_id: str = Field(
        min_length=1,
        max_length=128,
        description="ingest_urls 返回的入库任务 ID",
    )


class ListSourcesArgs(BaseModel):
    """来源列表参数。"""

    limit: int = Field(
        default=10,
        ge=1,
        le=20,
        description="最多返回多少条最近来源",
    )


class IngestUrlsTool:
    """把用户提供的链接提交到 RAG 的长期知识库。"""

    name = "ingest_urls"
    description = (
        "把用户明确要求记住、收藏或入库的公开网页链接提交到长期知识库。"
        "提交成功后只代表任务已开始，不代表已完成；不要用它读取或讲解正文。"
    )
    args_model = IngestUrlsArgs
    parameters_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "urls": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "maxItems": 20,
                "description": "要长期保存到知识库的公开网页 URL 列表",
            }
        },
        "required": ["urls"],
        "additionalProperties": False,
    }

    def __init__(
        self,
        client: KnowledgeBasePort,
        *,
        max_urls: int,
        watch_registrar: WatchRegistrar | None = None,
    ) -> None:
        self._client = client
        self._max_urls = max_urls
        self._watch_registrar = watch_registrar

    async def __call__(self, args: IngestUrlsArgs, context: ToolContext) -> ToolOutcome:
        urls, error = _normalize_urls(args.urls, max_urls=self._max_urls)
        if error is not None:
            return _error_outcome(ErrorCode.INVALID_REQUEST, error, status="error")

        try:
            payload = await self._client.create_ingestion(
                urls,
                client_run_id=context.run_id,
                idempotency_key=_idempotency_key(context.run_id, urls),
            )
        except AppError as exc:
            logger.info(
                "knowledge_ingest_failed",
                extra={
                    "extra_fields": {
                        "error_code": str(exc.code),
                        "urls": len(urls),
                    }
                },
            )
            return _error_outcome(exc.code, exc.message, status="error")

        job_id = _as_text(payload.get("job_id"))
        if not job_id:
            return _error_outcome(
                ErrorCode.KNOWLEDGE_BASE_REJECTED,
                "知识库没有返回任务编号",
                status="error",
            )

        status = _as_text(payload.get("status")) or "unknown"
        accepted = _as_int(payload.get("accepted"))
        total = _as_int(payload.get("total"))
        skipped = _as_list(payload.get("skipped"))
        note = _ingest_note(status=status, accepted=accepted)
        if status in {"queued", "running"} and self._watch_registrar is not None:
            try:
                await self._watch_registrar.register(
                    job_id=job_id,
                    conversation_id=context.conversation_id,
                )
            except Exception:  # noqa: BLE001 - 通知注册失败不应让入库失败
                logger.exception(
                    "ingestion_watch_register_failed",
                    extra={"extra_fields": {"job_id": job_id}},
                )
                note += "；未能注册自动通知，请用 get_ingestion 查询"
        logger.info(
            "knowledge_ingest_started",
            extra={
                "extra_fields": {
                    "job_id": job_id,
                    "status": status,
                    "accepted": accepted,
                    "total": total,
                }
            },
        )
        return ToolOutcome(
            ok=True,
            model_payload={
                "status": status,
                "job_id": job_id,
                "total": total,
                "accepted": accepted,
                "skipped": _compact_skipped(skipped),
                "skipped_count": len(skipped),
                "urls": list(urls[:_MAX_ITEMS]),
                "note": note,
            },
            result_summary={
                "status": status,
                "job_id": job_id,
                "accepted": accepted,
                "total": total,
                "skipped": len(skipped),
            },
        )


class GetIngestionTool:
    """查询异步入库任务的状态。"""

    name = "get_ingestion"
    description = (
        "查询一次异步入库任务的进度与结果。"
        "用户询问链接是否入库完成、失败原因或任务进度时使用。"
    )
    args_model = GetIngestionArgs
    parameters_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "job_id": {
                "type": "string",
                "description": "ingest_urls 返回的入库任务 ID",
            }
        },
        "required": ["job_id"],
        "additionalProperties": False,
    }

    def __init__(self, client: KnowledgeBasePort) -> None:
        self._client = client

    async def __call__(
        self, args: GetIngestionArgs, context: ToolContext
    ) -> ToolOutcome:
        try:
            payload = await self._client.get_ingestion(args.job_id)
        except AppError as exc:
            return _error_outcome(exc.code, exc.message, status="error")

        status = _as_text(payload.get("status")) or "unknown"
        items = _as_list(payload.get("items"))
        logger.info(
            "knowledge_job_queried",
            extra={
                "extra_fields": {
                    "job_id": args.job_id,
                    "status": status,
                    "items": len(items),
                }
            },
        )
        return ToolOutcome(
            ok=True,
            model_payload={
                "status": status,
                "summary": _job_summary(status),
                "job_id": args.job_id,
                "total": _as_int(payload.get("total")),
                "succeeded": _as_int(payload.get("succeeded")),
                "failed": _as_int(payload.get("failed")),
                "skipped": _as_int(payload.get("skipped")),
                "items": [_compact_item(item) for item in items[:_MAX_ITEMS]],
                "items_truncated": bool(payload.get("items_truncated"))
                or len(items) > _MAX_ITEMS,
            },
            result_summary={
                "status": status,
                "job_id": args.job_id,
                "total": _as_int(payload.get("total")),
                "failed": _as_int(payload.get("failed")),
            },
        )


class ListSourcesTool:
    """列出知识库最近收录的来源。"""

    name = "list_sources"
    description = (
        "列出知识库最近收录的来源标题与状态。"
        "用户询问知识库里有什么、最近收录了哪些内容时使用。"
    )
    args_model = ListSourcesArgs
    parameters_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 20,
                "default": 10,
                "description": "最多返回多少条最近来源",
            }
        },
        "additionalProperties": False,
    }

    def __init__(self, client: KnowledgeBasePort) -> None:
        self._client = client

    async def __call__(
        self, args: ListSourcesArgs, context: ToolContext
    ) -> ToolOutcome:
        try:
            payload = await self._client.list_sources(args.limit)
        except AppError as exc:
            return _error_outcome(exc.code, exc.message, status="error")

        items = _as_list(payload.get("items"))
        logger.info(
            "knowledge_sources_listed",
            extra={
                "extra_fields": {
                    "items": len(items),
                    "total": _as_int(payload.get("total")),
                }
            },
        )
        return ToolOutcome(
            ok=True,
            model_payload={
                "status": "ok",
                "total": _as_int(payload.get("total")),
                "items": [_compact_source(item) for item in items],
            },
            result_summary={
                "status": "ok",
                "items": len(items),
                "total": _as_int(payload.get("total")),
            },
        )


def _normalize_urls(urls: list[str], *, max_urls: int) -> tuple[list[str], str | None]:
    cleaned: list[str] = []
    for raw in urls:
        url = (raw or "").strip()
        if not url:
            continue
        if len(url) > _MAX_URL_CHARS:
            return [], "单个链接过长"
        if not url.lower().startswith(("http://", "https://")):
            return [], "仅支持 HTTP/HTTPS 链接"
        cleaned.append(url)
    if not cleaned:
        return [], "请提供至少一个公开网页链接"
    if len(cleaned) > max_urls:
        return [], f"单次最多提交 {max_urls} 个链接"
    # 保留首次出现顺序，避免模型重复提交同一链接。
    return list(dict.fromkeys(cleaned)), None


def _idempotency_key(run_id: str, urls: list[str]) -> str:
    digest = hashlib.sha256("\n".join(urls).encode("utf-8")).hexdigest()
    return f"{run_id}:{digest[:16]}"


def _ingest_note(*, status: str, accepted: int) -> str:
    if status in _TERMINAL_STATUSES and accepted == 0:
        return "没有新的链接需要入库，可能已在知识库中"
    if status == "started":
        return "已开始入库，完成后会自动通知"
    if status == "queued":
        return "已开始入库（排队中），完成后会自动通知"
    if status == "running":
        return "入库任务正在处理中"
    if status == "completed":
        return "入库任务已完成"
    if status == "partial":
        return "入库任务部分完成，存在失败项"
    if status == "failed":
        return "入库任务失败"
    return "入库任务已提交"


def _job_summary(status: str) -> str:
    if status == "queued":
        return "任务正在排队"
    if status == "running":
        return "任务正在处理中"
    if status == "completed":
        return "已完成入库"
    if status == "partial":
        return "入库部分完成"
    if status == "failed":
        return "入库失败"
    return "任务状态未知"


def _error_outcome(code: ErrorCode, message: str, *, status: str) -> ToolOutcome:
    return ToolOutcome(
        ok=False,
        error_code=str(code),
        model_payload={
            "status": status,
            "error_code": str(code),
            "message": message,
        },
        result_summary={"status": status, "error_code": str(code)},
    )


def _as_text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _as_int(value: Any) -> int:
    return value if isinstance(value, int) else 0


def _as_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _compact_skipped(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "url": _as_text(item.get("url")),
            "reason": _as_text(item.get("reason")),
            "source_id": item.get("source_id"),
        }
        for item in items[:_MAX_ITEMS]
    ]


def _compact_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "url": _as_text(item.get("url")),
        "status": _as_text(item.get("status")),
        "title": _as_text(item.get("title")),
        "error_code": item.get("error_code"),
        "error_message": item.get("error_message"),
    }


def _compact_source(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item.get("id"),
        "title": _as_text(item.get("title")),
        "url": _as_text(item.get("canonical_url") or item.get("url")),
        "status": _as_text(item.get("status")),
    }
