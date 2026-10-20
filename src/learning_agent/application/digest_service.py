"""V3 每日资讯抓取、筛选、Digest 生成与飞书投递。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from learning_agent.config import Settings
from learning_agent.digest.feed import FeedFetcher
from learning_agent.digest.feishu import FeishuNotifier
from learning_agent.digest.rss import FeedEntry, parse_feed
from learning_agent.observability.logging import get_logger
from learning_agent.persistence.v3_store import (
    DigestItemRecord,
    DigestRecord,
    PushPreferenceRecord,
    SourceRecord,
    V3Store,
)

logger = get_logger(__name__)


@dataclass(frozen=True)
class DigestResult:
    digest: DigestRecord
    items: tuple[DigestItemRecord, ...]
    delivery_status: str | None = None


@dataclass
class _Candidate:
    item_id: int
    source: SourceRecord
    entry: FeedEntry


class DailyDigestService:
    """单进程每日 Digest 服务；用日期作为幂等键。"""

    def __init__(
        self,
        *,
        settings: Settings,
        store: V3Store,
        feed_fetcher: FeedFetcher,
        notifier: FeishuNotifier | None = None,
        now: datetime | None = None,
    ) -> None:
        self._settings = settings
        self._store = store
        self._feed_fetcher = feed_fetcher
        self._notifier = notifier
        self._now = now
        self._lock = asyncio.Lock()

    def today(self) -> str:
        now = self._now or datetime.now(UTC)
        try:
            local = now.astimezone(ZoneInfo(self._settings.digest_timezone))
        except Exception:
            local = now
        return local.date().isoformat()

    async def run_today(self, *, force: bool = False) -> DigestResult:
        async with self._lock:
            date = self.today()
            preferences = self._preferences()
            return await self._run_date(date, force=force, preferences=preferences)

    async def run_due(self) -> DigestResult:
        """依次处理补发窗口内未完成的 Digest，返回最新一天的结果。"""

        async with self._lock:
            preferences = self._preferences()
            now = self._now or datetime.now(UTC)
            try:
                local_date = now.astimezone(
                    ZoneInfo(self._settings.digest_timezone)
                ).date()
            except Exception:
                local_date = now.date()
            result: DigestResult | None = None
            for offset in range(preferences.catch_up_days, -1, -1):
                date = (local_date - timedelta(days=offset)).isoformat()
                result = await self._run_date(
                    date, force=False, preferences=preferences
                )
            if result is None:
                raise RuntimeError("没有可执行的 Digest 日期")
            return result

    def get_today(self) -> DigestResult | None:
        record = self._store.get_digest(self.today())
        return self._view(record) if record else None

    def get(self, date: str) -> DigestResult | None:
        record = self._store.get_digest(date)
        return self._view(record) if record else None

    def _preferences(self) -> PushPreferenceRecord:
        return self._store.get_push_preferences(
            default_top_n=self._settings.digest_top_n,
            default_catch_up_days=self._settings.digest_catch_up_days,
        )

    async def _run_date(
        self,
        date: str,
        *,
        force: bool,
        preferences: PushPreferenceRecord,
    ) -> DigestResult:
        existing = self._store.get_digest(date)
        if existing is not None and not force:
            delivery = self._store.get_delivery(existing.id)
            delivery_complete = delivery is None or delivery.status in {
                "delivered",
                "disabled",
            }
            if existing.status == "completed" and delivery_complete:
                return self._view(existing)
            if existing.status == "completed":
                return await self._deliver(
                    existing, existing.content, existing.item_count
                )

        digest = self._store.create_digest(date)
        self._store.start_digest(digest.id)
        try:
            return await self._generate(digest, preferences=preferences)
        except Exception as exc:  # noqa: BLE001 - Digest 失败要落库并返回
            logger.exception(
                "daily_digest_failed",
                extra={"extra_fields": {"date": date}},
            )
            self._store.fail_digest(digest.id, f"{type(exc).__name__}: {exc}")
            failed = self._store.get_digest(date)
            if failed is None:
                raise
            return self._view(failed)

    async def _generate(
        self, digest: DigestRecord, *, preferences: PushPreferenceRecord
    ) -> DigestResult:
        sources = self._store.list_sources(enabled_only=True)
        sources = [
            source
            for source in sources
            if source.auto_push
            and (not preferences.source_ids or source.id in preferences.source_ids)
            and (
                not preferences.topics
                or source.category.casefold()
                in {topic.casefold() for topic in preferences.topics}
            )
        ]
        if not sources:
            content = (
                f"# AI 日报 {digest.digest_date}\n\n"
                "当前偏好下没有启用的资讯来源。请调整主题、来源或添加 RSS/Atom。"
            )
            self._store.complete_digest(
                digest.id, content=content, item_count=0, items=[]
            )
            if digest.digest_date != self.today():
                completed = self._store.get_digest(digest.digest_date)
                return self._view(completed or digest)
            return await self._deliver(digest, content, 0)

        semaphore = asyncio.Semaphore(self._settings.digest_source_concurrency)

        async def fetch(
            source: SourceRecord,
        ) -> tuple[SourceRecord, str | None, str | None]:
            async with semaphore:
                try:
                    xml = await self._feed_fetcher.fetch(
                        source.feed_url, allowed_domains=(source.domain,)
                    )
                    return source, xml, None
                except Exception as exc:  # noqa: BLE001 - 单源失败隔离
                    return source, None, f"{type(exc).__name__}: {exc}"

        results = await asyncio.gather(*(fetch(source) for source in sources))
        candidates: list[_Candidate] = []
        failed_sources: list[str] = []
        seen_item_ids: set[int] = set()
        for source, xml, error in results:
            if error is not None:
                failed_sources.append(source.name)
                self._store.mark_source_checked(
                    source.id, etag=None, last_modified=None, last_error=error
                )
                logger.warning(
                    "digest_source_failed",
                    extra={"extra_fields": {"source": source.name, "error": error}},
                )
                continue
            self._store.mark_source_checked(
                source.id, etag=None, last_modified=None, last_error=None
            )
            entries = parse_feed(
                xml or "", feed_url=source.feed_url, source_topic=source.category
            )
            for entry in entries:
                if not _matches_digest_date(
                    entry.published_at,
                    digest_date=digest.digest_date,
                    timezone=self._settings.digest_timezone,
                ):
                    continue
                item_id, _created = self._store.upsert_item(
                    source_id=source.id,
                    title=entry.title,
                    url=entry.url,
                    summary=entry.summary,
                    topic=entry.topic,
                    published_at=entry.published_at,
                    content_hash=entry.content_hash,
                )
                if item_id in seen_item_ids:
                    continue
                seen_item_ids.add(item_id)
                candidates.append(_Candidate(item_id, source, entry))

        candidates.sort(key=_candidate_sort_key, reverse=True)
        selected = _limit_per_source(candidates, preferences.top_n, 3)
        content = _render_digest(
            digest.digest_date, selected, failed_sources=failed_sources
        )
        items = [
            (candidate.item_id, _summary(candidate), candidate.entry.title)
            for candidate in selected
        ]
        self._store.complete_digest(
            digest.id, content=content, item_count=len(items), items=items
        )
        if digest.digest_date != self.today() and not items:
            completed = self._store.get_digest(digest.digest_date)
            return self._view(completed or digest)
        return await self._deliver(digest, content, len(items))

    async def _deliver(
        self, digest: DigestRecord, content: str, item_count: int
    ) -> DigestResult:
        delivery_status: str | None = None
        if self._notifier is not None and self._settings.feishu_enabled:
            result = await self._notifier.send(content)
            delivery_status = result.status
            self._store.save_delivery(
                digest_id=digest.id,
                channel="feishu",
                status=result.status,
                attempts=result.attempts,
                error=result.error,
                delivered_at=datetime.now(UTC)
                if result.status == "delivered"
                else None,
            )
        record = self._store.get_digest(digest.digest_date)
        return DigestResult(
            digest=record or digest,
            items=tuple(self._store.list_digest_items(digest.id)),
            delivery_status=delivery_status,
        )

    def _view(self, record: DigestRecord) -> DigestResult:
        delivery = self._store.get_delivery(record.id)
        return DigestResult(
            digest=record,
            items=tuple(self._store.list_digest_items(record.id)),
            delivery_status=delivery.status if delivery else None,
        )


def _matches_digest_date(
    value: datetime | None, *, digest_date: str, timezone: str
) -> bool:
    if value is None:
        return True
    try:
        local = value.astimezone(ZoneInfo(timezone))
    except Exception:
        local = value
    return local.date().isoformat() == digest_date


def _summary(candidate: _Candidate) -> str:
    summary = candidate.entry.summary.strip()
    return summary or candidate.entry.title


def _candidate_sort_key(candidate: _Candidate) -> tuple[int, float, str]:
    priority = {"trusted": 2, "verified": 1}.get(candidate.source.trust_level, 0)
    timestamp = (
        candidate.entry.published_at.timestamp() if candidate.entry.published_at else 0
    )
    return priority, timestamp, candidate.entry.title


def _limit_per_source(
    candidates: list[_Candidate], top_n: int, per_source: int
) -> list[_Candidate]:
    counts: dict[str, int] = {}
    selected: list[_Candidate] = []
    for candidate in candidates:
        count = counts.get(candidate.source.id, 0)
        if count >= per_source:
            continue
        selected.append(candidate)
        counts[candidate.source.id] = count + 1
        if len(selected) >= top_n:
            break
    return selected


def _render_digest(
    date: str, selected: list[_Candidate], *, failed_sources: list[str]
) -> str:
    lines = [f"# AI 日报 {date}", ""]
    if not selected:
        if failed_sources:
            lines.append(
                "今天没有获取到资讯。以下来源抓取失败："
                + "、".join(failed_sources[:5])
                + "。"
            )
        else:
            lines.append("今天暂无新的 AI 资讯。")
        return "\n".join(lines)
    for index, candidate in enumerate(selected, start=1):
        lines.append(f"## {index}. {candidate.entry.title}")
        lines.append("")
        lines.append(_summary(candidate))
        lines.append("")
        lines.append(f"来源：{candidate.source.name}")
        lines.append(candidate.entry.url)
        lines.append("")
    return "\n".join(lines).strip()
