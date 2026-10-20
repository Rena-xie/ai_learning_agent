"""V3 每日 Digest 查询与手动触发。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Request

from learning_agent.api.v1.schemas import DigestItemResponse, DigestResponse
from learning_agent.application.digest_service import DailyDigestService, DigestResult
from learning_agent.domain.errors import AppError, ErrorCode

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


def _response(result: DigestResult) -> DigestResponse:
    record = result.digest
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
            )
            for item in result.items
        ],
    )
