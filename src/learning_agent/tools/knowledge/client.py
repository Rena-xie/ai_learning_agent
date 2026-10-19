"""RAG 知识库 HTTP 客户端（v2 M2）。"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Protocol
from urllib.parse import quote

import httpx

from learning_agent.domain.errors import AppError, ErrorCode

_RAG_ERROR_CODES: dict[str, ErrorCode] = {
    "INVALID_REQUEST": ErrorCode.KNOWLEDGE_BASE_REJECTED,
    "TOO_MANY_URLS": ErrorCode.KNOWLEDGE_BASE_REJECTED,
    "JOB_NOT_FOUND": ErrorCode.INGESTION_NOT_FOUND,
    "JOB_NOT_RETRYABLE": ErrorCode.KNOWLEDGE_BASE_REJECTED,
    "INGESTION_QUEUE_FULL": ErrorCode.KNOWLEDGE_BASE_BUSY,
}
_RETRYABLE_STATUS = frozenset({502, 503, 504})


class KnowledgeBasePort(Protocol):
    """知识库工具的客户端契约；测试可注入实现。"""

    async def create_ingestion(
        self,
        urls: Sequence[str],
        *,
        client_run_id: str | None = None,
        idempotency_key: str | None = None,
        force_refresh: bool = False,
    ) -> dict[str, object]: ...

    async def get_ingestion(self, job_id: str) -> dict[str, object]: ...

    async def search(self, question: str, *, top_k: int = 5) -> dict[str, object]: ...

    async def list_sources(self, limit: int = 10) -> dict[str, object]: ...

    async def aclose(self) -> None: ...


class KnowledgeBaseClient:
    """调用 RAG 的异步入库与来源查询接口。"""

    def __init__(
        self,
        *,
        base_url: str,
        connect_timeout_seconds: float,
        read_timeout_seconds: float,
        max_retries: int = 2,
        retry_delay_seconds: float = 0.2,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._max_retries = max(0, max_retries)
        self._retry_delay_seconds = max(0.0, retry_delay_seconds)
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=httpx.Timeout(
                read_timeout_seconds, connect=connect_timeout_seconds
            ),
            transport=transport,
            headers={"Accept": "application/json"},
        )

    async def create_ingestion(
        self,
        urls: Sequence[str],
        *,
        client_run_id: str | None = None,
        idempotency_key: str | None = None,
        force_refresh: bool = False,
    ) -> dict[str, object]:
        headers = {"Idempotency-Key": idempotency_key} if idempotency_key else None
        return await self._request(
            "POST",
            "/api/v1/ingestions",
            json={
                "urls": list(urls),
                "client_run_id": client_run_id,
                "force_refresh": force_refresh,
            },
            headers=headers,
        )

    async def get_ingestion(self, job_id: str) -> dict[str, object]:
        return await self._request(
            "GET", f"/api/v1/ingestions/{quote(job_id, safe='')}"
        )

    async def search(self, question: str, *, top_k: int = 5) -> dict[str, object]:
        return await self._request(
            "POST",
            "/api/v1/search",
            json={"question": question},
        )

    async def list_sources(self, limit: int = 10) -> dict[str, object]:
        return await self._request(
            "GET",
            "/api/v1/sources",
            params={"page": 1, "size": limit},
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, object] | None = None,
        params: dict[str, object] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, object]:
        attempts = self._max_retries + 1 if _is_retryable(method, path, headers) else 1
        for attempt in range(attempts):
            try:
                response = await self._client.request(
                    method,
                    path,
                    json=json,
                    params=params,
                    headers=headers,
                )
            except httpx.TimeoutException as exc:
                if self._can_retry(attempt, attempts):
                    await self._sleep_before_retry(attempt)
                    continue
                raise AppError(
                    ErrorCode.KNOWLEDGE_BASE_TIMEOUT, "知识库服务响应超时"
                ) from exc
            except httpx.HTTPError as exc:
                if self._can_retry(attempt, attempts):
                    await self._sleep_before_retry(attempt)
                    continue
                raise AppError(
                    ErrorCode.KNOWLEDGE_BASE_UNAVAILABLE, "无法连接知识库服务"
                ) from exc

            if response.status_code in _RETRYABLE_STATUS and self._can_retry(
                attempt, attempts
            ):
                await self._sleep_before_retry(attempt)
                continue
            if response.status_code >= 400:
                _raise_for_response(response)

            try:
                payload = response.json()
            except ValueError as exc:
                raise AppError(
                    ErrorCode.KNOWLEDGE_BASE_UNAVAILABLE, "知识库返回了无效响应"
                ) from exc
            if not isinstance(payload, dict):
                raise AppError(
                    ErrorCode.KNOWLEDGE_BASE_UNAVAILABLE, "知识库返回了无效响应"
                )
            return payload
        raise AssertionError("unreachable")

    @staticmethod
    def _can_retry(attempt: int, attempts: int) -> bool:
        return attempt + 1 < attempts

    async def _sleep_before_retry(self, attempt: int) -> None:
        await asyncio.sleep(self._retry_delay_seconds * (2**attempt))


def _is_retryable(method: str, path: str, headers: dict[str, str] | None) -> bool:
    """GET 与检索可安全重试；入库仅在带幂等键时重试。"""

    if method == "GET" or path == "/api/v1/search":
        return True
    return bool(headers and headers.get("Idempotency-Key"))


def _raise_for_response(response: httpx.Response) -> None:
    code_value = ""
    message = ""
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            code_value = str(error.get("code") or "")
            message = str(error.get("message") or "")

    code = _RAG_ERROR_CODES.get(code_value)
    if code is None:
        if response.status_code == 404:
            code = ErrorCode.INGESTION_NOT_FOUND
        elif response.status_code == 429:
            code = ErrorCode.KNOWLEDGE_BASE_BUSY
        elif 400 <= response.status_code < 500:
            code = ErrorCode.KNOWLEDGE_BASE_REJECTED
        else:
            code = ErrorCode.KNOWLEDGE_BASE_UNAVAILABLE

    safe_message = message or _default_message(code)
    raise AppError(code, safe_message)


def _default_message(code: ErrorCode) -> str:
    if code is ErrorCode.KNOWLEDGE_BASE_TIMEOUT:
        return "知识库服务响应超时"
    if code is ErrorCode.KNOWLEDGE_BASE_BUSY:
        return "知识库正在处理其他任务，请稍后再试"
    if code is ErrorCode.INGESTION_NOT_FOUND:
        return "没有找到这次入库任务"
    if code is ErrorCode.KNOWLEDGE_BASE_REJECTED:
        return "知识库拒绝了本次请求"
    return "知识库服务暂时不可用"
