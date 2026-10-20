"""V3 每日 Digest 查询与手动触发。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Request

from learning_agent.api.v1.schemas import (
    DigestFeedbackRequest,
    DigestFeedbackResponse,
    DigestIngestionResponse,
    DigestItemResponse,
    DigestResponse,
)
from learning_agent.application.digest_service import DailyDigestService, DigestResult
from learning_agent.domain.errors import AppError, ErrorCode
from learning_agent.domain.urls import normalize_external_url

router = APIRouter(prefix="/api/v1/digests", tags=["digests"])


def _service(request: Request) -> DailyDigestService:
    if not request.app.state.settings.digest_enabled:
        raise AppError(ErrorCode.DIGEST_UNAVAILABLE, "每日资讯服务未启用")
    service = getattr(request.app.state, "digest_service", None)
    if service is None:
        raise AppError(ErrorCode.DIGEST_UNAVAILABLE, "每日资讯服务未启用")
    return service


@router.post("/run", response_model=DigestResponse)
async def run_digest(
    request: Request,
    force: Annotated[bool, Query()] = False,
) -> DigestResponse:
    """手动触发今天的 Digest；默认当天已完成时直接返回已有结果。"""

    return _response(await _service(request).run_today(force=force))


@router.get("/today", response_model=DigestResponse)
async def get_today(request: Request) -> DigestResponse:
    result = _service(request).get_today()
    if result is None:
        raise AppError(ErrorCode.NOT_FOUND, "今天还没有 Digest")
    return _response(result)


@router.get("/{date}", response_model=DigestResponse)
async def get_digest(date: str, request: Request) -> DigestResponse:
    result = _service(request).get(date)
    if result is None:
        raise AppError(ErrorCode.NOT_FOUND, f"没有该日期的 Digest: {date}")
    return _response(result)


@router.post("/{date}/feedback", response_model=DigestFeedbackResponse)
async def submit_feedback(
    date: str,
    payload: DigestFeedbackRequest,
    request: Request,
) -> DigestFeedbackResponse:
    store = request.app.state.v3_store
    item = store.get_digest_item(digest_date=date, item_id=payload.item_id)
    if item is None:
        raise AppError(ErrorCode.NOT_FOUND, "该资讯不在指定日期的 Digest 中")
    store.save_feedback(item_id=payload.item_id, action=payload.action)
    return DigestFeedbackResponse(item_id=payload.item_id, action=payload.action)


@router.post(
    "/{date}/items/{item_id}/save",
    response_model=DigestIngestionResponse,
)
async def save_item_to_knowledge(
    date: str,
    item_id: int,
    request: Request,
) -> DigestIngestionResponse:
    store = request.app.state.v3_store
    item = store.get_digest_item(digest_date=date, item_id=item_id)
    if item is None:
        raise AppError(ErrorCode.NOT_FOUND, "该资讯不在指定日期的 Digest 中")
    existing = store.get_ingestion_link(item_id)
    if existing is not None:
        return DigestIngestionResponse(
            item_id=item_id,
            url=item.url,
            job_id=existing.job_id,
            status=existing.status,
        )
    client = request.app.state.knowledge_client
    if client is None:
        raise AppError(
            ErrorCode.KNOWLEDGE_BASE_UNAVAILABLE,
            "知识库服务未启用，暂时无法收藏",
        )
    payload = await client.create_ingestion(
        [item.url],
        client_run_id=f"digest-{date}",
        idempotency_key=f"digest-{date}-{item_id}",
    )
    job_id = str(payload.get("job_id") or "").strip()
    if not job_id:
        raise AppError(
            ErrorCode.KNOWLEDGE_BASE_REJECTED,
            "知识库没有返回入库任务编号",
        )
    status = str(payload.get("status") or "queued")
    store.save_ingestion_link(
        item_id=item_id,
        job_id=job_id,
        status=status,
    )
    return DigestIngestionResponse(
        item_id=item_id,
        url=item.url,
        job_id=job_id,
        status=status,
    )


@router.get(
    "/{date}/items/{item_id}/ingestion",
    response_model=DigestIngestionResponse,
)
async def get_item_ingestion_status(
    date: str,
    item_id: int,
    request: Request,
) -> DigestIngestionResponse:
    store = request.app.state.v3_store
    item = store.get_digest_item(digest_date=date, item_id=item_id)
    if item is None:
        raise AppError(ErrorCode.NOT_FOUND, "该资讯不在指定日期的 Digest 中")
    existing = store.get_ingestion_link(item_id)
    if existing is None:
        raise AppError(ErrorCode.INGESTION_NOT_FOUND, "该资讯还没有提交入库")
    client = request.app.state.knowledge_client
    if client is None:
        raise AppError(
            ErrorCode.KNOWLEDGE_BASE_UNAVAILABLE,
            "知识库服务未启用，暂时无法查询入库状态",
        )
    payload = await client.get_ingestion(existing.job_id)
    status = str(payload.get("status") or existing.status)
    error_message = _ingestion_error(payload)
    store.save_ingestion_link(
        item_id=item_id,
        job_id=existing.job_id,
        status=status,
        error_message=error_message,
    )
    return DigestIngestionResponse(
        item_id=item_id,
        url=item.url,
        job_id=existing.job_id,
        status=status,
        error_message=error_message,
    )


def _response(result: DigestResult) -> DigestResponse:
    record = result.digest
    safe_items = []
    for item in result.items:
        try:
            normalize_external_url(item.url)
        except ValueError:
            continue
        safe_items.append(item)
    return DigestResponse(
        digest_date=record.digest_date,
        status=record.status,
        item_count=record.item_count,
        content=record.content,
        error_message=record.error_message,
        delivery_status=result.delivery_status,
        items=[
            DigestItemResponse(
                item_id=item.item_id,
                title=item.title,
                url=item.url,
                summary=item.summary,
                topic=item.topic,
                source_name=item.source_name,
                published_at=item.published_at,
                rank=item.rank,
                feedback_action=item.feedback_action,
                ingestion_job_id=item.ingestion_job_id,
                ingestion_status=item.ingestion_status,
            )
            for item in safe_items
        ],
    )


def _ingestion_error(payload: dict[str, object]) -> str | None:
    items = payload.get("items")
    if not isinstance(items, list):
        return None
    for item in items:
        if not isinstance(item, dict):
            continue
        message = item.get("error_message")
        if isinstance(message, str) and message.strip():
            return message.strip()
    return None
