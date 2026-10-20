"""V3.1 单用户资讯偏好。"""

from __future__ import annotations

from fastapi import APIRouter, Request

from learning_agent.api.v1.schemas import (
    PushPreferencesResponse,
    PushPreferencesUpdateRequest,
)
from learning_agent.domain.errors import AppError, ErrorCode
from learning_agent.persistence.v3_store import PushPreferenceRecord, V3Store

router = APIRouter(prefix="/api/v1/push/preferences", tags=["push"])


def _store(request: Request) -> V3Store:
    store = getattr(request.app.state, "v3_store", None)
    if store is None:
        raise AppError(ErrorCode.DIGEST_UNAVAILABLE, "资讯偏好服务未启用")
    return store


@router.get("", response_model=PushPreferencesResponse)
async def get_preferences(request: Request) -> PushPreferencesResponse:
    settings = request.app.state.settings
    return _response(
        _store(request).get_push_preferences(
            default_top_n=settings.digest_top_n,
            default_catch_up_days=settings.digest_catch_up_days,
        )
    )


@router.patch("", response_model=PushPreferencesResponse)
async def update_preferences(
    payload: PushPreferencesUpdateRequest, request: Request
) -> PushPreferencesResponse:
    store = _store(request)
    settings = request.app.state.settings
    current = store.get_push_preferences(
        default_top_n=settings.digest_top_n,
        default_catch_up_days=settings.digest_catch_up_days,
    )
    source_ids = (
        tuple(item.strip() for item in payload.source_ids if item.strip())
        if payload.source_ids is not None
        else current.source_ids
    )
    known_sources = {source.id for source in store.list_sources()}
    unknown = sorted(set(source_ids) - known_sources)
    if unknown:
        raise AppError(
            ErrorCode.INVALID_REQUEST,
            "包含不存在的资讯来源：" + ", ".join(unknown[:5]),
        )
    saved = store.save_push_preferences(
        topics=(
            tuple(item.strip() for item in payload.topics if item.strip())
            if payload.topics is not None
            else current.topics
        ),
        source_ids=source_ids,
        top_n=payload.top_n if payload.top_n is not None else current.top_n,
        catch_up_days=(
            payload.catch_up_days
            if payload.catch_up_days is not None
            else current.catch_up_days
        ),
    )
    return _response(saved)


def _response(record: PushPreferenceRecord) -> PushPreferencesResponse:
    return PushPreferencesResponse(
        topics=list(record.topics),
        source_ids=list(record.source_ids),
        top_n=record.top_n,
        catch_up_days=record.catch_up_days,
    )
