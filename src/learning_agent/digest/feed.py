"""可信 Feed 抓取器：复用文档抓取的安全策略。"""

from __future__ import annotations

from collections.abc import Sequence

import httpx

from learning_agent.tools.document.fetcher import DocumentFetcher
from learning_agent.tools.document.url_policy import Resolver, UrlPolicy

_FEED_CONTENT_TYPES = (
    "application/rss+xml",
    "application/atom+xml",
    "application/rdf+xml",
    "application/xml",
    "text/xml",
    "text/plain",
)


class FeedFetcher:
    """只从注册来源域名抓取 RSS/Atom。"""

    def __init__(
        self,
        *,
        timeout_s: float,
        max_bytes: int,
        max_redirects: int,
        transport: httpx.AsyncBaseTransport | None = None,
        resolver: Resolver | None = None,
    ) -> None:
        self._timeout_s = timeout_s
        self._max_bytes = max_bytes
        self._max_redirects = max_redirects
        self._transport = transport
        self._resolver = resolver

    async def fetch(self, feed_url: str, *, allowed_domains: Sequence[str]) -> str:
        policy = UrlPolicy(
            allowed_hosts=frozenset(domain.lower() for domain in allowed_domains),
            **({"resolver": self._resolver} if self._resolver else {}),
        )
        fetcher = DocumentFetcher(
            policy,
            timeout_seconds=self._timeout_s,
            max_bytes=self._max_bytes,
            max_redirects=self._max_redirects,
            transport=self._transport,
            verify_peer_address=self._transport is None,
            allowed_content_types=_FEED_CONTENT_TYPES,
        )
        result = await fetcher.fetch(feed_url)
        return result.html
