"""对话相关路由。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from learning_agent.api.v1.schemas import (
    ConversationCreateRequest,
    ConversationResponse,
    MessageWithSourcesResponse,
    SourceResponse,
)
from learning_agent.domain.errors import AppError, ErrorCode
from learning_agent.persistence.repositories import Repositories

router = APIRouter(prefix="/api/v1/conversations", tags=["conversations"])


def get_repositories(request: Request) -> Repositories:
    return request.app.state.repositories


RepositoriesDep = Annotated[Repositories, Depends(get_repositories)]


@router.post("", response_model=ConversationResponse, status_code=201)
async def create_conversation(
    payload: ConversationCreateRequest, repositories: RepositoriesDep
) -> ConversationResponse:
    title = (payload.title or "").strip() or "新对话"
    record = repositories.create_conversation(title=title)
    return ConversationResponse(
        id=record.id,
        title=record.title,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


@router.get("", response_model=list[ConversationResponse])
async def list_conversations(
    repositories: RepositoriesDep,
) -> list[ConversationResponse]:
    return [
        ConversationResponse(
            id=record.id,
            title=record.title,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )
        for record in repositories.list_conversations()
    ]


@router.get(
    "/{conversation_id}/messages", response_model=list[MessageWithSourcesResponse]
)
async def list_messages(
    conversation_id: str, repositories: RepositoriesDep
) -> list[MessageWithSourcesResponse]:
    if repositories.get_conversation(conversation_id) is None:
        raise AppError(ErrorCode.NOT_FOUND, "对话不存在或已被删除")
    messages = repositories.list_messages(conversation_id)
    responses: list[MessageWithSourcesResponse] = []
    source_cache: dict[str, list[SourceResponse]] = {}
    for message in messages:
        sources: list[SourceResponse] = []
        if message.role == "assistant" and message.run_id:
            if message.run_id not in source_cache:
                run = repositories.get_run(message.run_id)
                source_cache[message.run_id] = [
                    SourceResponse(
                        source_id=item.source_id,
                        title=item.title,
                        url=item.canonical_url,
                        fetched_at=item.fetched_at,
                        truncated=item.truncated,
                    )
                    for item in (run.sources if run else ())
                ]
            sources = source_cache[message.run_id]
        responses.append(
            MessageWithSourcesResponse(
                id=message.id,
                role=message.role,
                content=message.content,
                run_id=message.run_id,
                created_at=message.created_at,
                sources=sources,
            )
        )
    return responses
