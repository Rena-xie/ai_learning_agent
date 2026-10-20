"""RSS/Atom 解析与资讯标准化。"""

from __future__ import annotations

import email.utils
import hashlib
import html
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urljoin, urlsplit, urlunsplit
from xml.etree import ElementTree

_TAG_PATTERN = re.compile(r"<[^>]+>")
_SPACE_PATTERN = re.compile(r"\s+")


@dataclass(frozen=True)
class FeedEntry:
    """标准化后的单条 RSS/Atom 资讯。"""

    title: str
    url: str
    summary: str
    published_at: datetime | None
    content_hash: str
    topic: str


def parse_feed(
    body: str,
    *,
    feed_url: str,
    source_topic: str = "AI",
    max_summary_chars: int = 500,
) -> list[FeedEntry]:
    """解析 RSS/Atom；单条格式错误时跳过，不阻断整份 Feed。"""

    try:
        root = ElementTree.fromstring(body.strip())
    except ElementTree.ParseError:
        return []

    entries: list[FeedEntry] = []
    for element in root.iter():
        if _local_name(element.tag) not in {"item", "entry"}:
            continue
        title = _child_text(element, "title")
        link = _entry_link(element)
        if not title or not link:
            continue
        url = _canonical_url(urljoin(feed_url, link))
        summary = _clean_text(
            _child_text(element, "description")
            or _child_text(element, "summary")
            or _child_text(element, "content")
        )
        summary = _truncate(summary, max_summary_chars)
        published = _parse_date(
            _child_text(element, "pubDate")
            or _child_text(element, "published")
            or _child_text(element, "updated")
        )
        digest_source = f"{title}\n{summary}\n{url}".encode()
        entries.append(
            FeedEntry(
                title=_truncate(_clean_text(title), 500),
                url=url,
                summary=summary,
                published_at=published,
                content_hash=hashlib.sha256(digest_source).hexdigest(),
                topic=source_topic,
            )
        )
    return entries


def _local_name(tag: object) -> str:
    text = str(tag)
    return text.rsplit("}", 1)[-1].lower()


def _child_text(element: ElementTree.Element, name: str) -> str:
    for child in element:
        if _local_name(child.tag) == name.lower():
            return "".join(child.itertext()).strip()
    return ""


def _entry_link(element: ElementTree.Element) -> str:
    for child in element:
        if _local_name(child.tag) != "link":
            continue
        href = (child.attrib.get("href") or "").strip()
        rel = (child.attrib.get("rel") or "alternate").lower()
        if href and rel in {"alternate", ""}:
            return href
        text = "".join(child.itertext()).strip()
        if text:
            return text
    return ""


def _clean_text(value: str) -> str:
    return _SPACE_PATTERN.sub(" ", html.unescape(_TAG_PATTERN.sub(" ", value))).strip()


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)].rstrip() + "…"


def _canonical_url(value: str) -> str:
    parts = urlsplit(value.strip())
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    if scheme not in {"http", "https"} or not host:
        return value.strip()
    netloc = host
    if parts.port is not None:
        netloc = f"{netloc}:{parts.port}"
    return urlunsplit((scheme, netloc, parts.path or "/", parts.query, ""))


def _parse_date(value: str) -> datetime | None:
    text = value.strip()
    if not text:
        return None
    try:
        parsed = email.utils.parsedate_to_datetime(text)
        return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except (TypeError, ValueError):
        pass
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except ValueError:
        return None
