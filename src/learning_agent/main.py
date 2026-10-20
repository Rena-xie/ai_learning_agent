"""应用装配：配置校验、数据库、工具、模型适配器、Runtime 与路由。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from learning_agent.agent.context_builder import ContextBuilder
from learning_agent.agent.runtime import AgentRuntime
from learning_agent.agent.tool_registry import ToolHandler, ToolRegistry
from learning_agent.api.errors import register_exception_handlers
from learning_agent.api.security_headers import install_security_headers
from learning_agent.api.v1.routes import (
    conversations,
    digests,
    health,
    learning,
    learning_profile,
    messages,
    push_preferences,
    push_sources,
    runs,
    stats,
)
from learning_agent.application.chat_service import ChatService
from learning_agent.application.digest_scheduler import DigestScheduler
from learning_agent.application.digest_service import DailyDigestService
from learning_agent.application.ingestion_watcher import IngestionWatcher
from learning_agent.application.learning_profile import LearningProfileService
from learning_agent.application.learning_workspace import LearningWorkspaceService
from learning_agent.application.run_scheduler import RunScheduler
from learning_agent.config import Settings, get_settings
from learning_agent.digest.feed import FeedFetcher
from learning_agent.digest.feishu import FeishuNotifier
from learning_agent.digest.sources import DEFAULT_SOURCES
from learning_agent.models.base import ModelClient
from learning_agent.models.provider_adapter import OpenAICompatibleModelClient
from learning_agent.observability.logging import configure_logging, get_logger
from learning_agent.persistence.async_repositories import AsyncRepositories
from learning_agent.persistence.database import build_engine, build_session_factory
from learning_agent.persistence.learning_store import LearningStore
from learning_agent.persistence.migrations import apply_migrations
from learning_agent.persistence.repositories import Repositories
from learning_agent.persistence.v3_store import V3Store
from learning_agent.tools.document.fetcher import DocumentFetcher
from learning_agent.tools.document.tool import FetchDocumentTool
from learning_agent.tools.document.url_policy import Resolver, UrlPolicy
from learning_agent.tools.knowledge.client import (
    KnowledgeBaseClient,
    KnowledgeBasePort,
)
from learning_agent.tools.knowledge.tool import (
    GetIngestionTool,
    IngestUrlsTool,
    ListSourcesTool,
    SearchKnowledgeTool,
)

logger = get_logger(__name__)

_WEB_DIR = Path(__file__).resolve().parent / "web"


def create_app(
    settings: Settings | None = None,
    *,
    document_transport: httpx.AsyncBaseTransport | None = None,
    document_resolver: Resolver | None = None,
    model_client: ModelClient | None = None,
    knowledge_client: KnowledgeBasePort | None = None,
    digest_feed_fetcher: FeedFetcher | None = None,
    feishu_notifier: FeishuNotifier | None = None,
    digest_now: datetime | None = None,
    feishu_transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    """构建应用实例；必需配置缺失时抛出错误并停止不完整启动。

    ``document_transport`` / ``document_resolver`` / ``model_client`` /
    ``knowledge_client`` 是测试/评估用的注入点：把外部依赖指向本地可控服务，
    不改变任何安全策略（URL 白名单、端口、逐跳重定向等仍然生效）。
    """

    resolved = settings or get_settings()
    resolved.require_model_credentials()
    configure_logging(resolved.log_level)

    apply_migrations(resolved.database_url)
    engine = build_engine(resolved.database_url)
    session_factory = build_session_factory(engine)
    repositories = Repositories(session_factory)
    async_repositories = AsyncRepositories(repositories)
    v3_store = V3Store(session_factory)
    learning_store = LearningStore(session_factory)
    if resolved.digest_enabled and resolved.digest_seed_default_sources:
        v3_store.seed_sources(DEFAULT_SOURCES)
    quarantined_items = v3_store.quarantine_invalid_push_items()
    if quarantined_items:
        logger.warning(
            "invalid_push_items_quarantined",
            extra={"extra_fields": {"count": quarantined_items}},
        )
    recovered_runs = repositories.fail_stale_runs()
    if recovered_runs:
        logger.warning(
            "stale_runs_recovered",
            extra={"extra_fields": {"count": recovered_runs}},
        )

    policy = UrlPolicy(
        allowed_hosts=resolved.allowed_hosts_set,
        allow_public_web=resolved.manual_fetch_mode == "open_public",
        allowed_ports=resolved.allowed_ports_set,
        **({"resolver": document_resolver} if document_resolver else {}),
    )
    fetcher = DocumentFetcher(
        policy,
        timeout_seconds=resolved.fetch_timeout_seconds,
        max_bytes=resolved.max_document_bytes,
        max_redirects=resolved.max_redirects,
        transport=document_transport,
        verify_peer_address=document_transport is None,
    )
    fetch_tool = FetchDocumentTool(
        fetcher, max_document_chars=resolved.max_document_chars
    )
    tools: list[ToolHandler] = [fetch_tool]
    active_knowledge_client = knowledge_client
    ingestion_watcher: IngestionWatcher | None = None
    if resolved.knowledge_base_enabled:
        if active_knowledge_client is None:
            active_knowledge_client = KnowledgeBaseClient(
                base_url=resolved.knowledge_base_url,
                connect_timeout_seconds=(
                    resolved.knowledge_base_connect_timeout_seconds
                ),
                read_timeout_seconds=(resolved.knowledge_base_read_timeout_seconds),
                max_retries=resolved.knowledge_base_max_retries,
            )
        ingestion_watcher = IngestionWatcher(
            knowledge_client=active_knowledge_client,
            repositories=async_repositories,
            poll_interval_s=resolved.ingestion_poll_interval_s,
            max_backoff_s=resolved.ingestion_poll_max_interval_s,
            timeout_s=resolved.ingestion_watch_timeout_s,
            batch_size=resolved.ingestion_max_active_watches,
            retention_days=resolved.ingestion_watch_retention_days,
        )
        tools.extend(
            [
                IngestUrlsTool(
                    active_knowledge_client,
                    max_urls=resolved.knowledge_base_max_urls,
                    watch_registrar=ingestion_watcher,
                ),
                GetIngestionTool(active_knowledge_client),
                ListSourcesTool(active_knowledge_client),
                SearchKnowledgeTool(
                    active_knowledge_client,
                    min_similarity=resolved.knowledge_base_min_similarity,
                ),
            ]
        )
    tool_registry = ToolRegistry(tools)
    active_model = model_client or OpenAICompatibleModelClient(
        api_key=resolved.model_api_key,
        base_url=resolved.model_base_url,
        model_name=resolved.model_name,
        max_retries=resolved.model_max_retries,
        temperature=resolved.model_temperature,
    )
    context_builder = ContextBuilder(
        max_tokens=resolved.context_max_tokens,
        max_history_messages=resolved.max_history_messages,
        output_reserve_tokens=resolved.model_max_output_tokens,
        token_safety_factor=resolved.context_token_safety_factor,
    )
    runtime = AgentRuntime(
        model_client=active_model,
        tools=tool_registry,
        context_builder=context_builder,
        repositories=async_repositories,
        model_timeout_seconds=resolved.model_timeout_seconds,
        run_timeout_seconds=resolved.run_timeout_seconds,
        max_model_rounds=resolved.max_model_rounds,
        max_tool_calls=resolved.max_tool_calls,
        max_output_tokens=resolved.model_max_output_tokens,
        max_tool_result_chars=resolved.max_tool_result_chars,
        price_input_per_1k=resolved.model_price_input_per_1k,
        price_output_per_1k=resolved.model_price_output_per_1k,
    )
    feed_fetcher = digest_feed_fetcher or FeedFetcher(
        timeout_s=resolved.digest_fetch_timeout_s,
        max_bytes=resolved.digest_max_feed_bytes,
        max_redirects=resolved.max_redirects,
        transport=document_transport,
        resolver=document_resolver,
    )
    active_notifier = feishu_notifier
    if active_notifier is None and resolved.feishu_enabled:
        active_notifier = FeishuNotifier(
            webhook_url=resolved.feishu_webhook_url,
            secret=resolved.feishu_webhook_secret,
            transport=feishu_transport,
        )
    digest_service = DailyDigestService(
        settings=resolved,
        store=v3_store,
        feed_fetcher=feed_fetcher,
        notifier=active_notifier,
        now=digest_now,
    )
    digest_scheduler = DigestScheduler(
        digest_service,
        enabled=resolved.digest_enabled and resolved.digest_trigger == "on_startup",
        recheck_interval_s=resolved.digest_recheck_interval_s,
    )
    profile_service = LearningProfileService(
        store=v3_store, enabled=resolved.learning_profile_enabled
    )
    learning_workspace_service = LearningWorkspaceService(
        store=learning_store,
        digest_store=v3_store,
        profile_service=profile_service,
        knowledge_client=active_knowledge_client,
    )
    scheduler = RunScheduler()
    chat_service = ChatService(
        runtime=runtime,
        repositories=async_repositories,
        max_input_chars=resolved.max_input_chars,
        scheduler=scheduler,
        profile_service=profile_service,
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
                    "knowledge_base": resolved.knowledge_base_enabled,
                }
            },
        )
        try:
            if ingestion_watcher is not None:
                ingestion_watcher.start()
            digest_scheduler.start()
            yield
        finally:
            await digest_scheduler.stop()
            if ingestion_watcher is not None:
                await ingestion_watcher.stop()
            await scheduler.shutdown()
            await active_model.aclose()
            if active_knowledge_client is not None:
                await active_knowledge_client.aclose()
            if active_notifier is not None:
                await active_notifier.close()
            engine.dispose()

    app = FastAPI(title="AI Learning Agent", version="3.3.1", lifespan=lifespan)
    app.state.settings = resolved
    app.state.repositories = repositories
    app.state.async_repositories = async_repositories
    app.state.v3_store = v3_store
    app.state.scheduler = scheduler
    app.state.chat_service = chat_service
    app.state.digest_service = digest_service
    app.state.digest_scheduler = digest_scheduler
    app.state.learning_profile_service = profile_service
    app.state.learning_store = learning_store
    app.state.learning_workspace_service = learning_workspace_service
    app.state.engine = engine
    app.state.tool_registry = tool_registry
    app.state.knowledge_client = (
        active_knowledge_client if resolved.knowledge_base_enabled else None
    )
    app.state.ingestion_watcher = ingestion_watcher
    register_exception_handlers(app)
    install_security_headers(app)
    install_security_headers(app)
    app.include_router(health.router)
    app.include_router(conversations.router)
    app.include_router(messages.router)
    app.include_router(runs.router)
    app.include_router(stats.router)
    app.include_router(digests.router)
    app.include_router(push_sources.router)
    app.include_router(push_preferences.router)
    app.include_router(learning.router)
    app.include_router(learning_profile.router)
    if _WEB_DIR.is_dir():
        # 静态界面挂在最后，保证 /api 与 /health 路由优先匹配
        app.mount("/", StaticFiles(directory=str(_WEB_DIR), html=True), name="web")
    return app
