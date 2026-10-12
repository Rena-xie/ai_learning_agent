"""运行查询路由。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from learning_agent.api.v1.schemas import RunResponse, SourceResponse, ToolCallResponse
from learning_agent.domain.errors import AppError, ErrorCode
from learning_agent.persistence.repositories import Repositories

router = APIRouter(prefix="/api/v1/runs", tags=["runs"])


def get_repositories(request: Request) -> Repositories:
    return request.app.state.repositories


@router.get("/{run_id}", response_model=RunResponse)
async def get_run(
    run_id: str, repositories: Annotated[Repositories, Depends(get_repositories)]
) -> RunResponse:
    run = repositories.get_run(run_id)
    if run is None:
        raise AppError(ErrorCode.NOT_FOUND, "运行记录不存在")
    return RunResponse(
        run_id=run.id,
        conversation_id=run.conversation_id,
        status=run.status,
        model_name=run.model_name,
        prompt_version=run.prompt_version,
        started_at=run.started_at,
        finished_at=run.finished_at,
        duration_ms=run.duration_ms,
        input_tokens=run.input_tokens,
        output_tokens=run.output_tokens,
        error_code=run.error_code,
        error_category=run.error_category,
        tool_calls=[
            ToolCallResponse(
                id=call.id,
                tool_name=call.tool_name,
                status=call.status,
                duration_ms=call.duration_ms,
                error_code=call.error_code,
                arguments_summary=call.arguments_summary,
                result_summary=call.result_summary,
            )
            for call in run.tool_calls
        ],
        sources=[
            SourceResponse(
                source_id=item.source_id,
                title=item.title,
                url=item.canonical_url,
                fetched_at=item.fetched_at,
                truncated=item.truncated,
            )
            for item in run.sources
        ],
    )
