"""真实模型集成测试：Agent Runtime 全链路（真实 DeepSeek + 本地可控文档）。"""

from __future__ import annotations

import pytest
from tests.support.local_doc_server import TEST_HOST, LocalDocServer, stub_resolver

from learning_agent.agent.context_builder import ContextBuilder
from learning_agent.agent.runtime import AgentRuntime
from learning_agent.agent.tool_registry import ToolRegistry
from learning_agent.config import Settings
from learning_agent.domain.enums import RunStatus
from learning_agent.models.provider_adapter import OpenAICompatibleModelClient
from learning_agent.persistence.database import (
    build_engine,
    build_session_factory,
)
from learning_agent.persistence.migrations import apply_migrations
from learning_agent.persistence.repositories import Repositories
from learning_agent.tools.document.fetcher import DocumentFetcher
from learning_agent.tools.document.tool import FetchDocumentTool
from learning_agent.tools.document.url_policy import UrlPolicy

pytestmark = pytest.mark.real_model

DOC_HTML = """<html><head><title>示例框架快速开始</title></head><body><article>
<h1>核心概念</h1>
<p>示例框架是一个现代 Python Web 框架，基于 Starlette 处理 ASGI 请求，</p>
<p>并使用 Pydantic 做数据校验。</p>
<h2>依赖注入</h2>
<p>通过 Depends 声明依赖，测试时可以替换为假实现。</p>
<h2>重要限制</h2>
<p>默认运行在单进程内，不内置分布式任务队列。</p>
</article></body></html>"""


def build_stack(settings: Settings, repositories: Repositories, server: LocalDocServer):
    policy = UrlPolicy(
        allowed_hosts=frozenset({TEST_HOST}),
        allowed_ports=frozenset({80, 443, server.port}),
        resolver=stub_resolver(),
    )
    fetcher = DocumentFetcher(
        policy,
        timeout_seconds=10.0,
        max_bytes=500_000,
        max_redirects=3,
        transport=server.transport(),
        verify_peer_address=False,
    )
    tool = FetchDocumentTool(fetcher, max_document_chars=20_000)
    model = OpenAICompatibleModelClient(
        api_key=settings.model_api_key,
        base_url=settings.model_base_url,
        model_name=settings.model_name,
        max_retries=1,
    )
    runtime = AgentRuntime(
        model_client=model,
        tools=ToolRegistry([tool]),
        context_builder=ContextBuilder(
            max_tokens=settings.context_max_tokens,
            max_history_messages=10,
            output_reserve_tokens=settings.model_max_output_tokens,
        ),
        repositories=repositories,
        model_timeout_seconds=90.0,
        run_timeout_seconds=180.0,
        max_model_rounds=4,
        max_tool_calls=2,
        max_output_tokens=1000,
        max_tool_result_chars=20_000,
    )
    return runtime, model


@pytest.fixture
def repositories(tmp_path) -> Repositories:
    database_url = f"sqlite:///{(tmp_path / 'runtime.db').as_posix()}"
    apply_migrations(database_url)
    engine = build_engine(database_url)
    return Repositories(build_session_factory(engine))


async def test_runtime_loop_with_real_model(
    local_server: LocalDocServer, repositories: Repositories
) -> None:
    settings = Settings()
    local_server.add_html("/guide", DOC_HTML)
    runtime, model = build_stack(settings, repositories, local_server)
    conversation = repositories.create_conversation(title="真实模型测试")
    try:
        outcome = await runtime.run(
            conversation_id=conversation.id,
            user_message=(
                f"请阅读 {local_server.url('/guide')} 这份文档，"
                "解释它的核心概念和重要限制。"
            ),
        )
    finally:
        await model.aclose()

    assert outcome.status is RunStatus.COMPLETED, outcome.error_message
    assert outcome.content and "[S1]" in outcome.content
    assert outcome.sources and outcome.sources[0].source_id == "S1"
    assert outcome.sources[0].canonical_url == local_server.url("/guide")
    assert outcome.usage.input_tokens and outcome.usage.output_tokens

    run = repositories.get_run(outcome.run_id)
    assert run.status == "completed"
    assert [call.tool_name for call in run.tool_calls] == ["fetch_document"]
    assert run.tool_calls[0].status == "succeeded"
    assert run.tool_calls[0].error_code is None

    snapshots = repositories.list_snapshots(conversation.id)
    assert len(snapshots) == 1
    assert "依赖注入" in snapshots[0].extracted_text
    assert "Starlette" in snapshots[0].extracted_text

    messages = repositories.list_messages(conversation.id)
    assert [message.role for message in messages] == ["user", "assistant"]


async def test_follow_up_reuses_snapshot_without_refetching(
    local_server: LocalDocServer, repositories: Repositories
) -> None:
    settings = Settings()
    local_server.add_html("/guide", DOC_HTML)
    runtime, model = build_stack(settings, repositories, local_server)
    conversation = repositories.create_conversation(title="追问复用测试")
    try:
        first = await runtime.run(
            conversation_id=conversation.id,
            user_message=f"请阅读 {local_server.url('/guide')} 并总结核心概念。",
        )
        assert first.status is RunStatus.COMPLETED, first.error_message
        second = await runtime.run(
            conversation_id=conversation.id,
            user_message="那份文档里提到的依赖注入有什么好处？",
        )
    finally:
        await model.aclose()

    assert second.status is RunStatus.COMPLETED, second.error_message
    assert second.content and "[S1]" in second.content
    assert second.sources and second.sources[0].reused is True
    first_run = repositories.get_run(first.run_id)
    second_run = repositories.get_run(second.run_id)
    assert len(first_run.tool_calls) == 1
    assert len(second_run.tool_calls) == 0, "追问应复用快照，不应重复抓取"
    assert (
        repositories.list_snapshots(conversation.id)[0].id
        == second.sources[0].snapshot_id
    )
