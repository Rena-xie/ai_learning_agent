"""知识库 HTTP 客户端（v2 M2）。"""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest

from learning_agent.domain.errors import AppError, ErrorCode
from learning_agent.tools.knowledge.client import KnowledgeBaseClient


def build_client(
    handler: Callable[[httpx.Request], httpx.Response],
) -> KnowledgeBaseClient:
    return KnowledgeBaseClient(
        base_url="http://kb.test",
        connect_timeout_seconds=1,
        read_timeout_seconds=1,
        transport=httpx.MockTransport(handler),
    )


async def test_create_ingestion_sends_urls_and_idempotency_key() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        seen["idempotency"] = request.headers.get("Idempotency-Key")
        return httpx.Response(
            202,
            json={
                "job_id": "job_1",
                "status": "queued",
                "total": 2,
                "accepted": 2,
                "skipped": [],
                "created_at": "2026-10-19T00:00:00Z",
            },
        )

    client = build_client(handler)
    try:
        result = await client.create_ingestion(
            ["https://example.com/a", "https://example.com/b"],
            client_run_id="run-1",
            idempotency_key="key-1",
        )
    finally:
        await client.aclose()

    assert result["job_id"] == "job_1"
    assert seen["method"] == "POST"
    assert seen["path"] == "/api/v1/ingestions"
    assert seen["body"] == {
        "urls": ["https://example.com/a", "https://example.com/b"],
        "client_run_id": "run-1",
    }
    assert seen["idempotency"] == "key-1"


async def test_list_sources_uses_pagination() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["page"] = request.url.params.get("page", "")
        seen["size"] = request.url.params.get("size", "")
        return httpx.Response(
            200,
            json={"items": [], "total": 0, "page": 1, "size": 5},
        )

    client = build_client(handler)
    try:
        await client.list_sources(5)
    finally:
        await client.aclose()

    assert seen == {"page": "1", "size": "5"}


@pytest.mark.parametrize(
    ("status_code", "payload", "expected"),
    [
        (
            429,
            {"error": {"code": "INGESTION_QUEUE_FULL", "message": "队列已满"}},
            ErrorCode.KNOWLEDGE_BASE_BUSY,
        ),
        (
            404,
            {"error": {"code": "JOB_NOT_FOUND", "message": "任务不存在"}},
            ErrorCode.INGESTION_NOT_FOUND,
        ),
        (
            400,
            {"error": {"code": "TOO_MANY_URLS", "message": "链接过多"}},
            ErrorCode.KNOWLEDGE_BASE_REJECTED,
        ),
    ],
)
async def test_rag_errors_are_mapped(
    status_code: int, payload: dict[str, object], expected: ErrorCode
) -> None:
    client = build_client(lambda _request: httpx.Response(status_code, json=payload))
    try:
        with pytest.raises(AppError) as exc_info:
            await client.get_ingestion("job_missing")
    finally:
        await client.aclose()

    assert exc_info.value.code is expected


async def test_timeout_is_mapped() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timeout", request=request)

    client = build_client(handler)
    try:
        with pytest.raises(AppError) as exc_info:
            await client.get_ingestion("job_1")
    finally:
        await client.aclose()

    assert exc_info.value.code is ErrorCode.KNOWLEDGE_BASE_TIMEOUT


async def test_connection_error_is_mapped() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    client = build_client(handler)
    try:
        with pytest.raises(AppError) as exc_info:
            await client.get_ingestion("job_1")
    finally:
        await client.aclose()

    assert exc_info.value.code is ErrorCode.KNOWLEDGE_BASE_UNAVAILABLE
