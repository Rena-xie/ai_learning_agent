"""知识库工具行为（v2 M2）。"""

from __future__ import annotations

from typing import Any

from learning_agent.domain.contracts import ToolContext
from learning_agent.domain.errors import AppError, ErrorCode
from learning_agent.tools.knowledge.tool import (
    GetIngestionArgs,
    GetIngestionTool,
    IngestUrlsArgs,
    IngestUrlsTool,
    ListSourcesArgs,
    ListSourcesTool,
    SearchKnowledgeArgs,
    SearchKnowledgeTool,
)


class NoopAllocator:
    def next_source_id(self) -> str:
        return "S1"


class CountingAllocator:
    def __init__(self) -> None:
        self.count = 0

    def next_source_id(self) -> str:
        self.count += 1
        return f"S{self.count}"


def make_context() -> ToolContext:
    return ToolContext(
        run_id="run-1", conversation_id="conv-1", allocator=NoopAllocator()
    )


def make_counting_context() -> ToolContext:
    return ToolContext(
        run_id="run-1",
        conversation_id="conv-1",
        allocator=CountingAllocator(),
    )


class FakeKnowledgeClient:
    def __init__(
        self,
        *,
        create_response: dict[str, Any] | None = None,
        get_response: dict[str, Any] | None = None,
        sources_response: dict[str, Any] | None = None,
        search_response: dict[str, Any] | None = None,
        error: AppError | None = None,
    ) -> None:
        self.create_response = create_response or {}
        self.get_response = get_response or {}
        self.sources_response = sources_response or {}
        self.search_response = search_response or {}
        self.error = error
        self.calls: list[tuple[str, object]] = []

    async def create_ingestion(
        self,
        urls: list[str],
        *,
        client_run_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, object]:
        self.calls.append(("create", (list(urls), client_run_id, idempotency_key)))
        if self.error is not None:
            raise self.error
        return self.create_response

    async def get_ingestion(self, job_id: str) -> dict[str, object]:
        self.calls.append(("get", job_id))
        if self.error is not None:
            raise self.error
        return self.get_response

    async def list_sources(self, limit: int = 10) -> dict[str, object]:
        self.calls.append(("sources", limit))
        if self.error is not None:
            raise self.error
        return self.sources_response

    async def search(self, question: str, *, top_k: int = 5) -> dict[str, object]:
        self.calls.append(("search", question))
        if self.error is not None:
            raise self.error
        return self.search_response

    async def aclose(self) -> None:
        return None


class FakeWatchRegistrar:
    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[tuple[str, str]] = []
        self.fail = fail

    async def register(self, *, job_id: str, conversation_id: str) -> None:
        if self.fail:
            raise RuntimeError("watcher unavailable")
        self.calls.append((job_id, conversation_id))


async def test_ingest_urls_dedupes_and_reports_started() -> None:
    client = FakeKnowledgeClient(
        create_response={
            "job_id": "job_1",
            "status": "queued",
            "total": 2,
            "accepted": 1,
            "skipped": [{"url": "https://example.com/a", "reason": "already_indexed"}],
            "created_at": "2026-10-19T00:00:00Z",
        }
    )
    registrar = FakeWatchRegistrar()
    tool = IngestUrlsTool(client, max_urls=20, watch_registrar=registrar)

    outcome = await tool(
        IngestUrlsArgs(
            urls=[
                "https://example.com/a",
                "https://example.com/a",
                "https://example.com/b",
            ]
        ),
        make_context(),
    )

    assert outcome.ok is True
    assert outcome.model_payload["job_id"] == "job_1"
    assert outcome.model_payload["accepted"] == 1
    assert "已开始入库" in outcome.model_payload["note"]
    assert outcome.source_id is None
    assert outcome.content is None
    call = client.calls[0][1]
    assert call[0] == ["https://example.com/a", "https://example.com/b"]
    assert call[1] == "run-1"
    assert call[2].startswith("run-1:")
    assert registrar.calls == [("job_1", "conv-1")]


async def test_ingest_urls_does_not_watch_terminal_job() -> None:
    client = FakeKnowledgeClient(
        create_response={
            "job_id": "job_2",
            "status": "completed",
            "total": 1,
            "accepted": 0,
            "skipped": [{"url": "https://example.com/a", "reason": "already_indexed"}],
        }
    )
    registrar = FakeWatchRegistrar(fail=True)
    tool = IngestUrlsTool(client, max_urls=20, watch_registrar=registrar)

    outcome = await tool(IngestUrlsArgs(urls=["https://example.com/a"]), make_context())

    assert outcome.ok is True
    assert registrar.calls == []


async def test_ingest_urls_survives_watch_registration_failure() -> None:
    client = FakeKnowledgeClient(
        create_response={
            "job_id": "job_3",
            "status": "queued",
            "total": 1,
            "accepted": 1,
            "skipped": [],
        }
    )
    tool = IngestUrlsTool(
        client, max_urls=20, watch_registrar=FakeWatchRegistrar(fail=True)
    )

    outcome = await tool(IngestUrlsArgs(urls=["https://example.com/a"]), make_context())

    assert outcome.ok is True
    assert "未能注册自动通知" in outcome.model_payload["note"]


async def test_search_knowledge_allocates_one_source_per_evidence() -> None:
    client = FakeKnowledgeClient(
        search_response={
            "max_similarity": 0.9,
            "evidences": [
                {
                    "title": "BM25 指南",
                    "url": "https://example.com/bm25",
                    "heading_path": "检索 / BM25",
                    "score": 0.91,
                    "text": "BM25 是一种稀疏检索算法。",
                },
                {
                    "title": "RRF 指南",
                    "url": "https://example.com/rrf",
                    "heading_path": "检索 / RRF",
                    "score": 0.87,
                    "text": "RRF 使用排名而不是原始分数融合结果。",
                },
            ],
        }
    )
    tool = SearchKnowledgeTool(client, min_similarity=0.58)

    outcome = await tool(
        SearchKnowledgeArgs(question="BM25 是什么？"), make_counting_context()
    )

    assert outcome.ok is True
    assert outcome.model_payload["insufficient"] is False
    evidence_ids = [
        evidence["source_id"] for evidence in outcome.model_payload["evidences"]
    ]
    assert evidence_ids == ["S1", "S2"]
    assert len(outcome.sources) == 2
    assert outcome.sources[0].provider == "knowledge_base"
    assert outcome.sources[0].content.canonical_url == "https://example.com/bm25"
    assert outcome.sources[0].content.truncated is True


async def test_search_knowledge_marks_insufficient() -> None:
    client = FakeKnowledgeClient(
        search_response={"max_similarity": 0.4, "evidences": []}
    )
    tool = SearchKnowledgeTool(client, min_similarity=0.58)

    outcome = await tool(
        SearchKnowledgeArgs(question="无关问题"), make_counting_context()
    )

    assert outcome.ok is True
    assert outcome.model_payload["insufficient"] is True
    assert outcome.sources == ()


async def test_search_knowledge_maps_service_error() -> None:
    client = FakeKnowledgeClient(
        error=AppError(ErrorCode.KNOWLEDGE_BASE_UNAVAILABLE, "无法连接知识库服务")
    )
    tool = SearchKnowledgeTool(client, min_similarity=0.58)

    outcome = await tool(SearchKnowledgeArgs(question="BM25"), make_counting_context())

    assert outcome.ok is False
    assert outcome.error_code == ErrorCode.KNOWLEDGE_BASE_UNAVAILABLE.value


async def test_ingest_urls_rejects_non_http_without_calling_rag() -> None:
    client = FakeKnowledgeClient()
    tool = IngestUrlsTool(client, max_urls=20)

    outcome = await tool(IngestUrlsArgs(urls=["ftp://example.com/a"]), make_context())

    assert outcome.ok is False
    assert outcome.error_code == ErrorCode.INVALID_REQUEST.value
    assert client.calls == []


async def test_ingest_urls_reports_unavailable() -> None:
    client = FakeKnowledgeClient(
        error=AppError(ErrorCode.KNOWLEDGE_BASE_UNAVAILABLE, "无法连接知识库服务")
    )
    tool = IngestUrlsTool(client, max_urls=20)

    outcome = await tool(IngestUrlsArgs(urls=["https://example.com/a"]), make_context())

    assert outcome.ok is False
    assert outcome.error_code == ErrorCode.KNOWLEDGE_BASE_UNAVAILABLE.value
    assert outcome.model_payload["message"] == "无法连接知识库服务"


async def test_get_ingestion_compacts_items() -> None:
    items = [
        {
            "url": f"https://example.com/{index}",
            "status": "completed",
            "title": f"文章 {index}",
            "error_code": None,
            "error_message": None,
        }
        for index in range(6)
    ]
    client = FakeKnowledgeClient(
        get_response={
            "job_id": "job_1",
            "status": "partial",
            "total": 6,
            "succeeded": 5,
            "failed": 1,
            "skipped": 0,
            "items": items,
            "items_truncated": False,
        }
    )
    tool = GetIngestionTool(client)

    outcome = await tool(GetIngestionArgs(job_id="job_1"), make_context())

    assert outcome.ok is True
    assert outcome.model_payload["summary"] == "入库部分完成"
    assert len(outcome.model_payload["items"]) == 5
    assert outcome.model_payload["items_truncated"] is True


async def test_list_sources_returns_compact_items() -> None:
    client = FakeKnowledgeClient(
        sources_response={
            "items": [
                {
                    "id": 1,
                    "title": "BM25 指南",
                    "canonical_url": "https://example.com/bm25",
                    "status": "completed",
                }
            ],
            "total": 1,
        }
    )
    tool = ListSourcesTool(client)

    outcome = await tool(ListSourcesArgs(limit=10), make_context())

    assert outcome.ok is True
    assert outcome.model_payload["total"] == 1
    assert outcome.model_payload["items"] == [
        {
            "id": 1,
            "title": "BM25 指南",
            "url": "https://example.com/bm25",
            "status": "completed",
        }
    ]
