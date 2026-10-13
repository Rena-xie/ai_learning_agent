"""运行统计路由：基础观测数据（不触发模型调用）。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from learning_agent.api.v1.schemas import RunStatsResponse
from learning_agent.persistence.repositories import Repositories

router = APIRouter(prefix="/api/v1/stats", tags=["stats"])


def get_repositories(request: Request) -> Repositories:
    return request.app.state.repositories


@router.get("", response_model=RunStatsResponse)
async def run_statistics(
    repositories: Annotated[Repositories, Depends(get_repositories)],
) -> RunStatsResponse:
    return RunStatsResponse(**repositories.run_statistics())
