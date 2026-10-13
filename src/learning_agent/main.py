"""应用装配：配置校验、数据库、工具、模型适配器、Runtime 与路由。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from learning_agent.agent.context_builder import ContextBuilder
from learning_agent.agent.runtime import AgentRuntime
from learning_agent.agent.tool_registry import ToolRegistry
from learning_agent.api.errors import register_exception_handlers
from learning_agent.api.v1.routes import conversations, health, messages, runs
from learning_agent.application.chat_service import ChatService
from learning_agent.config import Settings, get_settings
from learning_agent.models.provider_adapter import OpenAICompatibleModelClient
from learning_agent.observability.logging import configure_logging, get_logger
from learning_agent.persistence.database import build_engine, build_session_factory
from learning_agent.persistence.migrations import apply_migrations
from learning_agent.persistence.repositories import Repositories
from learning_agent.tools.document.fetcher import DocumentFetcher
from learning_agent.tools.document.tool import FetchDocumentTool
from learning_agent.tools.document.url_policy import UrlPolicy

logger = get_logger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    """构建应用实例；必需配置缺失时抛出错误并停止不完整启动。"""

    resolved = settings or get_settings()
    resolved.require_model_credentials()
    configure_logging(resolved.log_level)

    apply_migrations(resolved.database_url)
    engine = build_engine(resolved.database_url)
    repositories = Repositories(build_session_factory(engine))

    policy = UrlPolicy(
        allowed_hosts=resolved.allowed_hosts_set,
        allowed_ports=resolved.allowed_ports_set,
    )
    fetcher = DocumentFetcher(
        policy,
        timeout_seconds=resolved.fetch_timeout_seconds,
        max_bytes=resolved.max_document_bytes,
        max_redirects=resolved.max_redirects,
    )
    fetch_tool = FetchDocumentTool(
        fetcher, max_document_chars=resolved.max_document_chars
    )
    tool_registry = ToolRegistry([fetch_tool])
    model_client = OpenAICompatibleModelClient(
        api_key=resolved.model_api_key,
        base_url=resolved.model_base_url,
        model_name=resolved.model_name,
        max_retries=resolved.model_max_retries,
    )
    context_builder = ContextBuilder(
        max_tokens=resolved.context_max_tokens,
        max_history_messages=resolved.max_history_messages,
        output_reserve_tokens=resolved.model_max_output_tokens,
    )
    runtime = AgentRuntime(
        model_client=model_client,
        tools=tool_registry,
        context_builder=context_builder,
        repositories=repositories,
        model_timeout_seconds=resolved.model_timeout_seconds,
        run_timeout_seconds=resolved.run_timeout_seconds,
        max_model_rounds=resolved.max_model_rounds,
        max_tool_calls=resolved.max_tool_calls,
        max_output_tokens=resolved.model_max_output_tokens,
        max_tool_result_chars=resolved.max_tool_result_chars,
    )
    chat_service = ChatService(
        runtime=runtime,
        repositories=repositories,
        max_input_chars=resolved.max_input_chars,
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        logger.info(
            "app_started",
            extra={
                "extra_fields": {
                    "env": resolved.app_env,
                    "model": resolved.model_name,
                    "allowed_hosts": len(resolved.allowed_document_hosts),
                }
            },
        )
        try:
            yield
        finally:
            await model_client.aclose()
            engine.dispose()

    app = FastAPI(title="AI Learning Agent", version="0.1.0", lifespan=lifespan)
    app.state.settings = resolved
    app.state.repositories = repositories
    app.state.chat_service = chat_service
    app.state.engine = engine
    register_exception_handlers(app)
    app.include_router(health.router)
    app.include_router(conversations.router)
    app.include_router(messages.router)
    app.include_router(runs.router)
    return app
