"""V3 每日资讯来源管理。"""

from __future__ import annotations

import uuid
from urllib.parse import urlsplit

from fastapi import APIRouter, Request

from learning_agent.api.v1.schemas import (
    PushSourceCreateRequest,
    PushSourceResponse,
)
from learning_agent.domain.errors import AppError, ErrorCode
from learning_agent.persistence.v3_store import SourceRecord, V3Store

router = APIRouter(prefix="/api/v1/push/sources", tags=["push"])


def _store(request: Request) -> V3Store:
    store = getattr(request.app.state, "v3_store", None)
    if store is None:
        raise AppError(ErrorCode.DIGEST_UNAVAILABLE, "资讯来源服务未启用")
    return store


@router.get("", response_model=list[PushSourceResponse])
async def list_sources(request: Request) -> list[PushSourceResponse]:
    return [_source_response(item) for item in _store(request).list_sources()]


@router.post("", response_model=PushSourceResponse, status_code=201)
async def add_source(
    payload: PushSourceCreateRequest, request: Request
) -> PushSourceResponse:
    parts = urlsplit(payload.feed_url.strip())
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise AppError(ErrorCode.INVALID_REQUEST, "Feed URL 必须是 HTTP(S) 地址")
    if parts.hostname.lower().rstrip(".") != payload.domain.strip().lower().rstrip("."):
        raise AppError(ErrorCode.INVALID_REQUEST, "Feed 域名必须与 domain 字段一致")
    try:
        record = _store(request).add_source(
            source_id=f"user-{uuid.uuid4().hex}",
            name=payload.name,
            feed_url=payload.feed_url,
            domain=payload.domain,
            category=payload.category,
        )
    except Exception as exc:
        raise AppError(ErrorCode.INVALID_REQUEST, f"添加来源失败: {exc}") from exc
    return _source_response(record)


def _source_response(record: SourceRecord) -> PushSourceResponse:
    return PushSourceResponse(
        id=record.id,
        name=record.name,
        feed_url=record.feed_url,
        domain=record.domain,
        category=record.category,
        trust_level=record.trust_level,
        enabled=record.enabled,
    )
