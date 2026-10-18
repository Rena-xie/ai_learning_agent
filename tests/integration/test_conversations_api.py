"""对话管理接口测试：删除、级联与运行中删除保护（不调用模型）。"""

from __future__ import annotations

from datetime import UTC, datetime

from tests.conftest import build_test_settings
from tests.support.web_app import running_client

from learning_agent.config import Settings
from learning_agent.domain.entities import DocumentContent, Section, UsageRecord
from learning_agent.domain.enums import RunStatus
from learning_agent.main import create_app


def make_content(url: str = "https://docs.example.com/guide") -> DocumentContent:
    return DocumentContent(
        title="示例指南",
        canonical_url=url,
        fetched_at=datetime.now(UTC),
        content_hash="b" * 64,
        content_text="正文内容",
        sections=(Section(title="核心概念", level=1, text="正文内容"),),
    )


def make_settings(tmp_path) -> Settings:
    return build_test_settings(
        database_url=f"sqlite:///{(tmp_path / 'api.db').as_posix()}"
    )


async def test_delete_conversation_removes_all_related_data(tmp_path) -> None:
    app = create_app(make_settings(tmp_path))
    repositories = app.state.repositories
    async with running_client(app) as client:
        created = await client.post("/api/v1/conversations", json={"title": "待删除"})
        conversation_id = created.json()["id"]
        run_id = "11111111-1111-1111-1111-111111111111"
        repositories.add_message(
            conversation_id=conversation_id, role="user", content="将被级联删除"
        )
        repositories.create_run(
            run_id=run_id,
            conversation_id=conversation_id,
            model_name="test-model",
            prompt_version="v1",
        )
        snapshot_id = repositories.add_snapshot(
            conversation_id=conversation_id,
            content=make_content(),
            parse_status="ok",
        )
        repositories.link_run_source(
            run_id=run_id, snapshot_id=snapshot_id, source_id="S1"
        )
        repositories.finish_run(
            run_id=run_id, status=RunStatus.COMPLETED, usage=UsageRecord()
        )

        response = await client.delete(f"/api/v1/conversations/{conversation_id}")

    assert response.status_code == 204
    assert repositories.get_conversation(conversation_id) is None
    assert repositories.list_messages(conversation_id) == []
    assert repositories.list_snapshots(conversation_id) == []
    assert repositories.get_run(run_id) is None


async def test_delete_unknown_conversation_returns_404(tmp_path) -> None:
    app = create_app(make_settings(tmp_path))
    async with running_client(app) as client:
        response = await client.delete(
            "/api/v1/conversations/00000000-0000-0000-0000-000000000000"
        )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


async def test_delete_is_rejected_while_run_is_active(tmp_path) -> None:
    app = create_app(make_settings(tmp_path))
    repositories = app.state.repositories
    async with running_client(app) as client:
        conversation_id = (await client.post("/api/v1/conversations", json={})).json()[
            "id"
        ]
        repositories.create_run(
            run_id="22222222-2222-2222-2222-222222222222",
            conversation_id=conversation_id,
            model_name="test-model",
            prompt_version="v1",
        )
        blocked = await client.delete(f"/api/v1/conversations/{conversation_id}")
        assert blocked.status_code == 409
        assert blocked.json()["error"]["code"] == "run_in_progress"

        repositories.finish_run(
            run_id="22222222-2222-2222-2222-222222222222",
            status=RunStatus.COMPLETED,
            usage=UsageRecord(),
        )
        allowed = await client.delete(f"/api/v1/conversations/{conversation_id}")
    assert allowed.status_code == 204


async def test_messages_are_isolated_between_conversations(tmp_path) -> None:
    app = create_app(make_settings(tmp_path))
    repositories = app.state.repositories
    async with running_client(app) as client:
        first = (await client.post("/api/v1/conversations", json={})).json()["id"]
        second = (await client.post("/api/v1/conversations", json={})).json()["id"]
        repositories.add_message(
            conversation_id=first, role="user", content="只属于对话一"
        )
        repositories.add_message(
            conversation_id=second, role="user", content="只属于对话二"
        )
        first_messages = (
            await client.get(f"/api/v1/conversations/{first}/messages")
        ).json()
        second_messages = (
            await client.get(f"/api/v1/conversations/{second}/messages")
        ).json()
    assert [message["content"] for message in first_messages] == ["只属于对话一"]
    assert [message["content"] for message in second_messages] == ["只属于对话二"]


async def test_list_messages_includes_run_sources(tmp_path) -> None:
    """消息列表一次性带出各运行的来源（覆盖批量来源查询）。"""

    app = create_app(make_settings(tmp_path))
    repositories = app.state.repositories
    async with running_client(app) as client:
        conversation_id = (await client.post("/api/v1/conversations", json={})).json()[
            "id"
        ]
        run_id = "33333333-0000-0000-0000-000000000001"
        repositories.create_run(
            run_id=run_id,
            conversation_id=conversation_id,
            model_name="test-model",
            prompt_version="v1",
        )
        repositories.add_message(
            conversation_id=conversation_id, role="user", content="请解释文档"
        )
        snapshot_id = repositories.add_snapshot(
            conversation_id=conversation_id,
            content=make_content(),
            parse_status="ok",
        )
        repositories.link_run_source(
            run_id=run_id, snapshot_id=snapshot_id, source_id="S1"
        )
        repositories.add_message(
            conversation_id=conversation_id,
            role="assistant",
            content="结论如下 [S1]。",
            run_id=run_id,
        )
        repositories.finish_run(
            run_id=run_id, status=RunStatus.COMPLETED, usage=UsageRecord()
        )

        messages = (
            await client.get(f"/api/v1/conversations/{conversation_id}/messages")
        ).json()

    assistant = next(item for item in messages if item["role"] == "assistant")
    assert [source["source_id"] for source in assistant["sources"]] == ["S1"]
    assert assistant["sources"][0]["url"] == "https://docs.example.com/guide"
