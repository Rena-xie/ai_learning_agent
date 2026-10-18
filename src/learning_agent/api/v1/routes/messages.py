"""消息提交路由（触发一次 Agent Run）。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from learning_agent.api.v1.schemas import (
    ErrorDetail,
    MessageCreateRequest,
    MessageWithSourcesResponse,
    RunAcceptedResponse,
    SendMessageResponse,
    SourceResponse,
)
from learning_agent.application.chat_service import ChatService
from learning_agent.domain.enums import RunStatus
from learning_agent.domain.errors import ErrorCode, http_status_for
from learning_agent.persistence.async_repositories import AsyncRepositories

router = APIRouter(prefix="/api/v1/conversations", tags=["messages"])


def get_chat_service(request: Request) -> ChatService:
    return request.app.state.chat_service


def get_repositories(request: Request) -> AsyncRepositories:
    return request.app.state.async_repositories


@router.post("/{conversation_id}/messages")
async def create_message(
    conversation_id: str,
    payload: MessageCreateRequest,
    service: Annotated[ChatService, Depends(get_chat_service)],
    repositories: Annotated[AsyncRepositories, Depends(get_repositories)],
    mode: Annotated[Literal["sync", "async"], Query()] = "sync",
) -> JSONResponse:
    """提交一条用户消息。

    默认 ``mode=sync``：等待运行结束后返回最终结果；
    ``mode=async``：立即返回 202 与 ``run_id``，运行在后台执行，
    调用方轮询 ``GET /api/v1/runs/{run_id}`` 获取进度与结果。
    """

    if mode == "async":
        run_id = await service.start_run(
            conversation_id=conversation_id, content=payload.content
        )
        accepted = RunAcceptedResponse(run_id=run_id, status=RunStatus.RUNNING.value)
        return JSONResponse(status_code=202, content=accepted.model_dump(mode="json"))

    outcome = await service.submit_message(
        conversation_id=conversation_id, content=payload.content
    )
    sources = [
        SourceResponse(
            source_id=item.source_id,
            title=item.title,
            url=item.canonical_url,
            fetched_at=item.fetched_at,
            truncated=item.truncated,
            provider=item.provider,
        )
        for item in outcome.sources
    ]

    if outcome.status is not RunStatus.COMPLETED:
        code = _resolve_error_code(outcome.error_code)
        body = SendMessageResponse(
            run_id=outcome.run_id,
            status=outcome.status.value,
            message=None,
            sources=sources,
            error=ErrorDetail(
                code=str(code),
                category=outcome.error_category or "internal",
                message=outcome.error_message or "本次运行失败",
            ),
            notes=list(outcome.notes),
        )
        return JSONResponse(
            status_code=http_status_for(code),
            content=body.model_dump(mode="json"),
        )

    message_record = await repositories.get_message_by_run(
        conversation_id=conversation_id, run_id=outcome.run_id
    )
    body = SendMessageResponse(
        run_id=outcome.run_id,
        status=outcome.status.value,
        message=MessageWithSourcesResponse(
            id=message_record.id if message_record else -1,
            role="assistant",
            content=outcome.content or "",
            run_id=outcome.run_id,
            created_at=message_record.created_at
            if message_record
            else datetime.now(UTC),
            sources=sources,
        ),
        sources=sources,
        error=None,
        notes=list(outcome.notes),
    )
    return JSONResponse(status_code=201, content=body.model_dump(mode="json"))


def _resolve_error_code(raw: str | None) -> ErrorCode:
    if not raw:
        return ErrorCode.INTERNAL_ERROR
    try:
        return ErrorCode(raw)
    except ValueError:
        return ErrorCode.INTERNAL_ERROR
