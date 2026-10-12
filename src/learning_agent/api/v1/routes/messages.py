"""消息提交路由（触发一次 Agent Run）。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from learning_agent.api.v1.schemas import (
    ErrorDetail,
    MessageCreateRequest,
    MessageWithSourcesResponse,
    SendMessageResponse,
    SourceResponse,
)
from learning_agent.application.chat_service import ChatService
from learning_agent.domain.enums import RunStatus
from learning_agent.domain.errors import ErrorCode, http_status_for
from learning_agent.persistence.repositories import Repositories

router = APIRouter(prefix="/api/v1/conversations", tags=["messages"])


def get_chat_service(request: Request) -> ChatService:
    return request.app.state.chat_service


def get_repositories(request: Request) -> Repositories:
    return request.app.state.repositories


@router.post("/{conversation_id}/messages")
async def create_message(
    conversation_id: str,
    payload: MessageCreateRequest,
    service: Annotated[ChatService, Depends(get_chat_service)],
    repositories: Annotated[Repositories, Depends(get_repositories)],
) -> JSONResponse:
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

    message_record = repositories.get_message_by_run(
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
