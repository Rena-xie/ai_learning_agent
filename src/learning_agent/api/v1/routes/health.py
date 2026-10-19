"""存活与就绪检查。"""

from __future__ import annotations

from fastapi import APIRouter, Request

from learning_agent.api.v1.schemas import HealthResponse
from learning_agent.domain.errors import ConfigurationError

router = APIRouter(tags=["health"])


@router.get("/health/live", response_model=HealthResponse)
async def live() -> HealthResponse:
    """进程存活检查，不依赖模型 API 与数据库。"""

    return HealthResponse(status="ok")


@router.get("/health/ready", response_model=HealthResponse)
async def ready(request: Request) -> HealthResponse:
    """检查必要配置与数据库可用性，不执行模型调用。"""

    settings = request.app.state.settings
    repositories = request.app.state.repositories
    model_configured = True
    try:
        settings.require_model_credentials()
    except ConfigurationError:
        model_configured = False
    database_ok = repositories.ping()

    knowledge_client = getattr(request.app.state, "knowledge_client", None)
    knowledge_base: str | None = None
    if knowledge_client is None:
        knowledge_base = "disabled"
    else:
        probe = getattr(knowledge_client, "health", None)
        if probe is None:
            knowledge_base = "unknown"
        else:
            knowledge_base = "ok" if await probe() else "unavailable"

    status = "ok" if (database_ok and model_configured) else "degraded"
    return HealthResponse(
        status=status,
        database_ok=database_ok,
        model_configured=model_configured,
        knowledge_base=knowledge_base,
    )
