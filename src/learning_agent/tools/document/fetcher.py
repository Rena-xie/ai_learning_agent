"""HTTP 获取：逐跳重定向校验、内容类型检查与大小/超时限制。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

from learning_agent.domain.errors import DocumentToolError, ErrorCode, UrlPolicyError
from learning_agent.tools.document.url_policy import (
    UrlPolicy,
    ValidatedTarget,
    is_public_ip,
)

logger = logging.getLogger(__name__)

_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_HTML_CONTENT_TYPES = ("text/html", "application/xhtml+xml", "text/plain")
_USER_AGENT = "AILearningAgent/0.1 (local document reader)"


@dataclass(frozen=True)
class FetchResult:
    """一次成功的文档获取结果（HTML 原文只在本进程内流转，不落盘）。"""

    canonical_url: str
    final_url: str
    html: str
    content_type: str
    byte_size: int
    fetched_at: datetime
    redirects: tuple[str, ...] = ()


class DocumentFetcher:
    """受控的文档获取器：安全策略 + 资源上限。"""

    def __init__(
        self,
        policy: UrlPolicy,
        *,
        timeout_seconds: float,
        max_bytes: int,
        max_redirects: int,
        transport: httpx.AsyncBaseTransport | None = None,
        verify_peer_address: bool = True,
    ) -> None:
        self._policy = policy
        self._timeout_seconds = timeout_seconds
        self._max_bytes = max_bytes
        self._max_redirects = max_redirects
        self._transport = transport
        # 连接建立后校验真实对端地址；只有本地可控测试服务器需要关闭
        self._verify_peer_address = verify_peer_address

    async def fetch(self, url: str) -> FetchResult:
        target = self._policy.validate(url)
        redirects: list[str] = []
        timeout = httpx.Timeout(self._timeout_seconds)
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=False,
            transport=self._transport,
            headers={"User-Agent": _USER_AGENT, "Accept": "text/html,*/*;q=0.8"},
        ) as client:
            for _hop in range(self._max_redirects + 1):
                try:
                    response = await self._send(client, target)
                except httpx.TimeoutException as exc:
                    raise DocumentToolError(
                        ErrorCode.FETCH_TIMEOUT,
                        "获取文档超时，请稍后重试或更换文档地址",
                        detail=str(exc),
                    ) from exc
                except httpx.HTTPError as exc:
                    raise DocumentToolError(
                        ErrorCode.FETCH_HTTP_ERROR,
                        "网络请求失败，无法获取该文档",
                        detail=type(exc).__name__,
                    ) from exc

                try:
                    if response.status_code in _REDIRECT_STATUSES:
                        location = response.headers.get("location", "")
                        target, hop_url = self._follow(target, location)
                        redirects.append(hop_url)
                        continue
                    return await self._read(response, target)
                except httpx.TimeoutException as exc:
                    raise DocumentToolError(
                        ErrorCode.FETCH_TIMEOUT,
                        "读取文档超时，请稍后重试或更换文档地址",
                        detail=str(exc),
                    ) from exc
                finally:
                    await response.aclose()
            raise DocumentToolError(
                ErrorCode.FETCH_HTTP_ERROR,
                "该文档的重定向次数超过限制",
                detail=f"max_redirects={self._max_redirects}",
            )

    async def _send(
        self, client: httpx.AsyncClient, target: ValidatedTarget
    ) -> httpx.Response:
        request = client.build_request("GET", target.url)
        return await client.send(request, stream=True)

    def _follow(
        self, current: ValidatedTarget, location: str
    ) -> tuple[ValidatedTarget, str]:
        try:
            new_target = self._policy.resolve_redirect(current, location)
        except UrlPolicyError as exc:
            raise DocumentToolError(
                exc.code,
                f"文档重定向被安全策略拒绝：{exc.message}",
                detail=exc.detail,
            ) from exc
        logger.info(
            "document_redirect",
            extra={
                "extra_fields": {
                    "from_host": current.host,
                    "to_host": new_target.host,
                }
            },
        )
        return new_target, new_target.url

    async def _read(
        self, response: httpx.Response, target: ValidatedTarget
    ) -> FetchResult:
        self._verify_peer(response, target)
        if response.status_code >= 400:
            raise DocumentToolError(
                ErrorCode.FETCH_HTTP_ERROR,
                f"文档获取失败（HTTP {response.status_code}）",
                detail=f"status={response.status_code}",
            )
        content_type = (
            response.headers.get("content-type", "").split(";")[0].strip().lower()
        )
        if not any(content_type.startswith(item) for item in _HTML_CONTENT_TYPES):
            raise DocumentToolError(
                ErrorCode.UNSUPPORTED_CONTENT_TYPE,
                f"暂不支持该类型的内容（{content_type or '未知类型'}），"
                "当前仅支持 HTML 文档",
            )
        declared = self._declared_length(response)
        if declared is not None and declared > self._max_bytes:
            raise DocumentToolError(
                ErrorCode.DOCUMENT_TOO_LARGE,
                "文档体积超过限制，已拒绝下载",
                detail=f"content_length={declared} max={self._max_bytes}",
            )
        chunks: list[bytes] = []
        size = 0
        async for chunk in response.aiter_bytes():
            size += len(chunk)
            if size > self._max_bytes:
                raise DocumentToolError(
                    ErrorCode.DOCUMENT_TOO_LARGE,
                    "文档体积超过限制，无法完整获取",
                    detail=f"bytes>{self._max_bytes}",
                )
            chunks.append(chunk)
        body = b"".join(chunks)
        encoding = response.charset_encoding or "utf-8"
        try:
            html_text = body.decode(encoding, errors="replace")
        except LookupError:
            html_text = body.decode("utf-8", errors="replace")
        return FetchResult(
            canonical_url=target.url,
            final_url=str(response.url),
            html=html_text,
            content_type=content_type,
            byte_size=len(body),
            fetched_at=datetime.now(UTC),
        )

    def _verify_peer(self, response: httpx.Response, target: ValidatedTarget) -> None:
        """校验实际连接的对端地址（缓解解析与建连之间的时间差）。"""

        if not self._verify_peer_address:
            return
        peer = _peer_ip(response)
        if peer is None:
            logger.warning(
                "document_peer_unknown",
                extra={"extra_fields": {"host": target.host}},
            )
            raise DocumentToolError(
                ErrorCode.SSRF_TARGET_BLOCKED,
                "无法确认文档服务器的真实地址，已拒绝本次访问",
            )
        if not is_public_ip(peer):
            logger.warning(
                "document_peer_blocked",
                extra={"extra_fields": {"host": target.host}},
            )
            raise DocumentToolError(
                ErrorCode.SSRF_TARGET_BLOCKED,
                "文档服务器实际地址不是公网地址，已拒绝访问",
                detail=f"peer_not_public host={target.host}",
            )

    def _declared_length(self, response: httpx.Response) -> int | None:
        raw = response.headers.get("content-length")
        if raw is None:
            return None
        try:
            return int(raw)
        except ValueError:
            return None


def _peer_ip(response: httpx.Response) -> str | None:
    """读取连接的底层对端 IP（httpcore 的 server_addr）。"""

    stream = response.extensions.get("network_stream")
    if stream is None:
        return None
    try:
        value = stream.get_extra_info("server_addr")
    except Exception:  # noqa: BLE001 - 取不到即按未知处理
        return None
    if isinstance(value, (tuple, list)) and value:
        return str(value[0])
    if isinstance(value, str) and value:
        return value.rsplit(":", 1)[0] if value.count(":") == 1 else value
    return None
