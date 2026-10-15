"""运行统计接口测试（离线：直接构造运行与工具调用数据）。"""

from __future__ import annotations

from datetime import UTC, datetime

from tests.conftest import build_test_settings
from tests.support.web_app import running_client

from learning_agent.domain.entities import UsageRecord
from learning_agent.domain.enums import RunStatus
from learning_agent.main import create_app


def make_settings(tmp_path):
    return build_test_settings(
        database_url=f"sqlite:///{(tmp_path / 'stats.db').as_posix()}"
    )


def add_tool_call(repositories, run_id: str, *, status: str, error_code: str | None):
    now = datetime.now(UTC)
    repositories.add_tool_call(
        run_id=run_id,
        tool_call_id=f"tc-{status}",
        tool_name="fetch_document",
        status=status,
        started_at=now,
        finished_at=now,
        duration_ms=500,
        arguments_summary='{"url": "https://docs.example.com/x"}',
        result_summary='{"status": "ok"}',
        error_code=error_code,
    )


async def test_stats_are_empty_by_default(tmp_path) -> None:
    app = create_app(make_settings(tmp_path))
    async with running_client(app) as client:
        response = await client.get("/api/v1/stats")
    assert response.status_code == 200
    body = response.json()
    assert body["runs_total"] == 0
    assert body["success_rate"] is None
    assert body["document_fetch_success_rate"] is None


async def test_stats_aggregate_runs_and_tools(tmp_path) -> None:
    app = create_app(make_settings(tmp_path))
    repositories = app.state.repositories
    conversation = repositories.create_conversation(title="统计")

    succeeded_run = "aaaaaaaa-0000-0000-0000-000000000001"
    failed_run = "aaaaaaaa-0000-0000-0000-000000000002"
    repositories.create_run(
        run_id=succeeded_run,
        conversation_id=conversation.id,
        model_name="test-model",
        prompt_version="v1",
    )
    repositories.finish_run(
        run_id=succeeded_run,
        status=RunStatus.COMPLETED,
        usage=UsageRecord(input_tokens=100, output_tokens=50),
        duration_ms=1_000,
    )
    add_tool_call(repositories, succeeded_run, status="succeeded", error_code=None)

    repositories.create_run(
        run_id=failed_run,
        conversation_id=conversation.id,
        model_name="test-model",
        prompt_version="v1",
    )
    repositories.finish_run(
        run_id=failed_run,
        status=RunStatus.FAILED,
        usage=UsageRecord(),
        duration_ms=3_000,
        error_code="run_timeout",
        error_category="resource_limit",
    )
    add_tool_call(repositories, failed_run, status="failed", error_code="fetch_timeout")

    async with running_client(app) as client:
        response = await client.get("/api/v1/stats")
    body = response.json()

    assert body["runs_total"] == 2
    assert body["runs_completed"] == 1
    assert body["runs_failed"] == 1
    assert body["success_rate"] == 0.5
    assert body["duration_ms_avg"] == 2_000
    assert body["input_tokens_total"] == 100
    assert body["output_tokens_total"] == 50
    assert body["tool_calls_total"] == 2
    assert body["tool_calls_failed"] == 1
    assert body["document_fetches_total"] == 2
    assert body["document_fetch_success_rate"] == 0.5
    assert body["error_codes"] == {"run_timeout": 1}
    # 未配置单价时成本保持为空，不编造
    assert body["estimated_cost_total"] is None


async def test_stats_include_estimated_cost(tmp_path) -> None:
    app = create_app(make_settings(tmp_path))
    repositories = app.state.repositories
    conversation = repositories.create_conversation(title="成本")
    run_id = "bbbbbbbb-0000-0000-0000-000000000001"
    repositories.create_run(
        run_id=run_id,
        conversation_id=conversation.id,
        model_name="test-model",
        prompt_version="v1",
    )
    repositories.finish_run(
        run_id=run_id,
        status=RunStatus.COMPLETED,
        usage=UsageRecord(
            input_tokens=1000, output_tokens=500, estimated_cost=0.002
        ),
        duration_ms=100,
    )

    async with running_client(app) as client:
        response = await client.get("/api/v1/stats")

    assert response.json()["estimated_cost_total"] == 0.002
