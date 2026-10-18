"""知识库工具接入 Agent 的集成链路（v2 M2）。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from tests.support.scripted_model import (
    ScriptedModelClient,
    final_response,
    tool_call_response,
)

from learning_agent.config import Settings
from learning_agent.main import create_app


def make_settings(tmp_path: Path, *, enabled: bool = True) -> Settings:
    return Settings(
        model_api_key="test-key",
        model_name="test-model",
        model_base_url="https://api.example.com/v1",
        database_url=f"sqlite:///{(tmp_path / 'agent.db').as_posix()}",
        allowed_document_hosts=(),
        log_level="WARNING",
        knowledge_base_enabled=enabled,
        max_tool_calls=5,
    )


class FakeKnowledgeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.closed = False

    async def create_ingestion(
        self,
        urls: list[str],
        *,
        client_run_id: str | None = None,
        idempotency_key: str | None = None,
        force_refresh: bool = False,
    ) -> dict[str, object]:
        self.calls.append(("create", list(urls)))
        return {
            "job_id": "job_test_1",
            "status": "queued",
            "total": len(urls),
            "accepted": len(urls),
            "skipped": [],
            "created_at": "2026-10-19T00:00:00Z",
        }

    async def get_ingestion(self, job_id: str) -> dict[str, object]:
        self.calls.append(("get", job_id))
        return {
            "job_id": job_id,
            "status": "completed",
            "total": 1,
            "succeeded": 1,
            "failed": 0,
            "skipped": 0,
            "items": [],
        }

    async def list_sources(self, limit: int = 10) -> dict[str, object]:
        self.calls.append(("sources", limit))
        return {"items": [], "total": 0, "page": 1, "size": limit}

    async def search(self, question: str, *, top_k: int = 5) -> dict[str, object]:
        self.calls.append(("search", question))
        return {
            "question": question,
            "max_similarity": 0.9,
            "evidences": [
                {
                    "source_id": 10,
                    "child_id": 20,
                    "parent_id": 30,
                    "title": "BM25 指南",
                    "url": "https://example.com/bm25",
                    "heading_path": "检索 / BM25",
                    "score": 0.9,
                    "text": (
                        "BM25 是一种稀疏检索算法，通过词频和文档长度归一化计算相关性。"
                    ),
                }
            ],
            "elapsed_ms": 5.0,
            "dense_ids": [20],
            "bm25_ids": [20],
            "fused_ids": [20],
        }

    async def aclose(self) -> None:
        self.closed = True


async def test_agent_starts_ingestion_without_claiming_completion(
    tmp_path: Path,
) -> None:
    knowledge = FakeKnowledgeClient()
    model = ScriptedModelClient(
        [
            tool_call_response(("ingest_urls", '{"urls": ["https://example.com/a"]}')),
            final_response("已开始入库，任务编号 job_test_1。完成后会自动通知。"),
            tool_call_response(("search_knowledge", '{"question": "BM25 是什么？"}')),
            final_response("BM25 是一种稀疏检索算法 [S1]。"),
        ]
    )
    app = create_app(
        make_settings(tmp_path),
        model_client=model,
        knowledge_client=knowledge,
    )

    async with app.router.lifespan_context(app):
        # 测试中停掉后台循环，后续手动触发一次轮询，避免定时器竞争。
        await app.state.ingestion_watcher.stop()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            created = await client.post("/api/v1/conversations", json={})
            conversation_id = created.json()["id"]

            response = await client.post(
                f"/api/v1/conversations/{conversation_id}/messages",
                json={"content": "把 https://example.com/a 存进知识库"},
            )

            assert response.status_code == 201, response.text
            payload = response.json()
            assert payload["status"] == "completed"
            assert "已开始入库" in payload["message"]["content"]
            assert "已完成" not in payload["message"]["content"]

            run = (await client.get(f"/api/v1/runs/{payload['run_id']}")).json()
            assert run["prompt_version"] == "v3"
            assert run["tool_calls"][0]["tool_name"] == "ingest_urls"
            assert run["tool_calls"][0]["status"] == "succeeded"

            watches = await app.state.async_repositories.list_due_ingestion_watches(
                now=datetime.now(UTC) + timedelta(seconds=10)
            )
            assert [watch.job_id for watch in watches] == ["job_test_1"]

            await app.state.async_repositories.save_ingestion_watch(
                job_id="job_test_1",
                status="watching",
                attempts=0,
                last_checked_at=None,
                next_check_at=datetime.now(UTC) - timedelta(seconds=1),
                notified_at=None,
                last_error=None,
            )
            assert await app.state.ingestion_watcher.poll_once() == 1

            history = (
                await client.get(f"/api/v1/conversations/{conversation_id}/messages")
            ).json()
            notices = [
                message for message in history if message["kind"] == "ingestion_notice"
            ]
            assert len(notices) == 1
            assert "已完成入库" in notices[0]["content"]
            assert notices[0]["run_id"] is None

            answer = await client.post(
                f"/api/v1/conversations/{conversation_id}/messages",
                json={"content": "根据知识库说说 BM25 是什么"},
            )
            assert answer.status_code == 201, answer.text
            answer_payload = answer.json()
            assert "[S1]" in answer_payload["message"]["content"]
            assert answer_payload["sources"][0]["source_id"] == "S1"
            assert answer_payload["sources"][0]["provider"] == "knowledge_base"
            assert answer_payload["sources"][0]["url"] == "https://example.com/bm25"

            answer_run = (
                await client.get(f"/api/v1/runs/{answer_payload['run_id']}")
            ).json()
            assert answer_run["prompt_version"] == "v3"
            assert answer_run["tool_calls"][0]["tool_name"] == "search_knowledge"

    assert ("create", ["https://example.com/a"]) in knowledge.calls
    assert knowledge.closed is True


async def test_knowledge_tools_can_be_disabled(tmp_path: Path) -> None:
    app = create_app(
        make_settings(tmp_path, enabled=False),
        model_client=ScriptedModelClient([]),
    )

    async with app.router.lifespan_context(app):
        assert app.state.tool_registry.names == frozenset({"fetch_document"})


async def test_knowledge_tools_are_registered_when_enabled(
    tmp_path: Path,
) -> None:
    app = create_app(
        make_settings(tmp_path),
        model_client=ScriptedModelClient([]),
        knowledge_client=FakeKnowledgeClient(),
    )

    async with app.router.lifespan_context(app):
        assert {
            "fetch_document",
            "ingest_urls",
            "get_ingestion",
            "list_sources",
            "search_knowledge",
        } <= app.state.tool_registry.names
