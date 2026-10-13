"""Runtime 资源上限与循环终止测试。

循环边界（轮数、工具次数、总时限）由脚本化模型客户端确定性地触发；
端到端真实链路见 tests/e2e 与 tests/integration/test_runtime_real_model.py。
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from tests.support.scripted_model import (
    ScriptedModelClient,
    final_response,
    tool_call_response,
)
from tests.support.web_app import running_client

from learning_agent.agent.context_builder import ContextBuilder
from learning_agent.agent.runtime import AgentRuntime
from learning_agent.agent.tool_registry import ToolRegistry
from learning_agent.config import Settings
from learning_agent.domain.contracts import ToolContext
from learning_agent.domain.entities import DocumentContent, ToolOutcome
from learning_agent.domain.enums import RunStatus
from learning_agent.main import create_app
from learning_agent.persistence.database import build_engine, build_session_factory
from learning_agent.persistence.migrations import apply_migrations
from learning_agent.persistence.repositories import Repositories
from learning_agent.tools.document.tool import FetchDocumentArgs, FetchDocumentTool

_DOC_URL = "https://docs.example.com/guide"
_TOOL_CALL = ("fetch_document", f'{{"url": "{_DOC_URL}"}}')


class StubDocumentTool:
    """不联网的工具替身：返回固定文档结果，用于驱动循环边界。"""

    name = "fetch_document"
    description = "测试替身"
    args_model = FetchDocumentArgs
    parameters_schema = FetchDocumentTool.parameters_schema

    async def __call__(
        self, args: FetchDocumentArgs, context: ToolContext
    ) -> ToolOutcome:
        source_id = context.allocator.next_source_id()
        content = DocumentContent(
            title="替身文档",
            canonical_url=_DOC_URL,
            fetched_at=datetime.now(UTC),
            content_hash="d" * 64,
            content_text="替身正文内容",
        )
        return ToolOutcome(
            ok=True,
            source_id=source_id,
            content=content,
            model_payload={
                "status": "ok",
                "source_id": source_id,
                "content_text": content.content_text,
                "truncated": False,
            },
            result_summary={"status": "ok", "source_id": source_id},
        )


@pytest.fixture
def repositories(tmp_path) -> Repositories:
    database_url = f"sqlite:///{(tmp_path / 'limits.db').as_posix()}"
    apply_migrations(database_url)
    return Repositories(build_session_factory(build_engine(database_url)))


def build_runtime(
    model: ScriptedModelClient,
    repositories: Repositories,
    *,
    max_model_rounds: int = 3,
    max_tool_calls: int = 2,
    run_timeout_seconds: float = 5.0,
) -> AgentRuntime:
    return AgentRuntime(
        model_client=model,
        tools=ToolRegistry([StubDocumentTool()]),
        context_builder=ContextBuilder(
            max_tokens=8_000, max_history_messages=10, output_reserve_tokens=500
        ),
        repositories=repositories,
        model_timeout_seconds=2.0,
        run_timeout_seconds=run_timeout_seconds,
        max_model_rounds=max_model_rounds,
        max_tool_calls=max_tool_calls,
        max_output_tokens=500,
        max_tool_result_chars=2_000,
    )


async def test_model_round_limit_is_enforced(repositories: Repositories) -> None:
    model = ScriptedModelClient([tool_call_response(_TOOL_CALL) for _ in range(5)])
    runtime = build_runtime(model, repositories, max_model_rounds=2)
    conversation = repositories.create_conversation(title="轮数上限")

    outcome = await runtime.run(
        conversation_id=conversation.id, user_message="请阅读文档"
    )

    assert outcome.status is RunStatus.FAILED
    assert outcome.error_code == "model_round_limit_exceeded"
    assert model.calls == 2
    assert repositories.get_active_run(conversation.id) is None


async def test_tool_call_limit_is_enforced(repositories: Repositories) -> None:
    model = ScriptedModelClient(
        [
            tool_call_response(
                _TOOL_CALL,
                _TOOL_CALL,
                call_ids=["call-1", "call-2"],
            )
        ]
    )
    runtime = build_runtime(model, repositories, max_tool_calls=1)
    conversation = repositories.create_conversation(title="工具次数上限")

    outcome = await runtime.run(
        conversation_id=conversation.id, user_message="请阅读文档"
    )

    assert outcome.status is RunStatus.FAILED
    assert outcome.error_code == "tool_limit_exceeded"


async def test_run_timeout_stops_the_run(repositories: Repositories) -> None:
    model = ScriptedModelClient([tool_call_response(_TOOL_CALL)], delay_seconds=0.5)
    runtime = build_runtime(model, repositories, run_timeout_seconds=0.05)
    conversation = repositories.create_conversation(title="总时限")

    outcome = await runtime.run(
        conversation_id=conversation.id, user_message="请阅读文档"
    )

    assert outcome.status is RunStatus.FAILED
    assert outcome.error_code == "run_timeout"
    assert repositories.get_active_run(conversation.id) is None


async def test_scripted_loop_completes_with_source(repositories: Repositories) -> None:
    """脚本化客户端走完整循环：工具调用 → 带引用回答。"""

    model = ScriptedModelClient(
        [tool_call_response(_TOOL_CALL), final_response("结论如下 [S1]。")]
    )
    runtime = build_runtime(model, repositories)
    conversation = repositories.create_conversation(title="正常循环")

    outcome = await runtime.run(
        conversation_id=conversation.id, user_message="请阅读文档"
    )

    assert outcome.status is RunStatus.COMPLETED
    assert outcome.sources and outcome.sources[0].source_id == "S1"
    assert "[S1]" in (outcome.content or "")
    run = repositories.get_run(outcome.run_id)
    assert [call.tool_name for call in run.tool_calls] == ["fetch_document"]


async def test_repeated_tool_call_ids_do_not_break_persistence(
    repositories: Repositories,
) -> None:
    """供应商复用同一个工具调用 ID 时，运行记录写入仍然成功。"""

    model = ScriptedModelClient(
        [
            tool_call_response(_TOOL_CALL, call_ids=["call-x"]),
            tool_call_response(_TOOL_CALL, call_ids=["call-x"]),
            final_response("结论如下 [S1]。"),
        ]
    )
    runtime = build_runtime(model, repositories)
    conversation = repositories.create_conversation(title="重复调用 ID")

    outcome = await runtime.run(
        conversation_id=conversation.id, user_message="请阅读文档"
    )

    assert outcome.status is RunStatus.COMPLETED
    run = repositories.get_run(outcome.run_id)
    assert len(run.tool_calls) == 2
    assert len({call.id for call in run.tool_calls}) == 2


@pytest.mark.real_model
async def test_real_model_run_timeout_is_reported(tmp_path) -> None:
    """真实模型链路：总时限生效时运行以 run_timeout 结束，不伪装成功。"""

    settings = Settings().model_copy(
        update={
            "database_url": f"sqlite:///{(tmp_path / 'timeout.db').as_posix()}",
            "run_timeout_seconds": 0.05,
        }
    )
    app = create_app(settings)
    async with running_client(app, timeout_seconds=120) as client:
        conversation_id = (await client.post("/api/v1/conversations", json={})).json()[
            "id"
        ]
        response = await client.post(
            f"/api/v1/conversations/{conversation_id}/messages",
            json={
                "content": (
                    "请阅读 "
                    "https://liaoxuefeng.com/books/python/introduction/index.html "
                    "并说明主题。"
                )
            },
        )

    assert response.status_code == 504
    assert response.json()["error"]["code"] == "run_timeout"
    assert response.json()["status"] == "failed"
