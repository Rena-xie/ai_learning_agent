"""V3 Daily Digest 服务测试。"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from learning_agent.application.digest_service import DailyDigestService
from learning_agent.config import Settings
from learning_agent.digest.feishu import FeishuResult
from learning_agent.persistence.database import build_engine, build_session_factory
from learning_agent.persistence.migrations import apply_migrations
from learning_agent.persistence.v3_store import V3Store

_RSS = """<rss version="2.0"><channel>
<item>
  <title>RAG 检索质量提升</title>
  <link>https://example.com/rag</link>
  <description>一个关于混合检索的更新。</description>
  <pubDate>Tue, 20 Oct 2026 09:00:00 GMT</pubDate>
</item>
</channel></rss>"""

_CATCH_UP_RSS = """<rss version="2.0"><channel>
<item>
  <title>昨天的 Agent 更新</title>
  <link>https://example.com/agent-yesterday</link>
  <description>昨天发布。</description>
  <pubDate>Mon, 19 Oct 2026 09:00:00 GMT</pubDate>
</item>
<item>
  <title>今天的 RAG 更新</title>
  <link>https://example.com/rag-today</link>
  <description>今天发布。</description>
  <pubDate>Tue, 20 Oct 2026 09:00:00 GMT</pubDate>
</item>
</channel></rss>"""


class FakeFeedFetcher:
    async def fetch(self, feed_url: str, *, allowed_domains: tuple[str, ...]) -> str:
        assert feed_url == "https://example.com/feed.xml"
        assert allowed_domains == ("example.com",)
        return _RSS


class FakeNotifier:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def send(self, text: str) -> FeishuResult:
        self.calls.append(text)
        return FeishuResult(status="delivered", attempts=1)


class FlakyNotifier:
    def __init__(self) -> None:
        self.calls = 0

    async def send(self, _text: str) -> FeishuResult:
        self.calls += 1
        if self.calls == 1:
            return FeishuResult(
                status="failed",
                attempts=1,
                error="temporary failure",
            )
        return FeishuResult(status="delivered", attempts=1)


class CatchUpFeedFetcher(FakeFeedFetcher):
    async def fetch(self, feed_url: str, *, allowed_domains: tuple[str, ...]) -> str:
        assert feed_url == "https://example.com/feed.xml"
        assert allowed_domains == ("example.com",)
        return _CATCH_UP_RSS


def build_settings(database_url: str) -> Settings:
    return Settings(
        _env_file=None,
        database_url=database_url,
        model_api_key="test",
        model_name="test-model",
        model_base_url="https://example.invalid/v1",
        digest_enabled=True,
        digest_seed_default_sources=False,
        digest_timezone="UTC",
        digest_top_n=3,
        feishu_enabled=True,
    )


@pytest.fixture
def store(tmp_path: Path) -> tuple[V3Store, str]:
    database_url = f"sqlite:///{(tmp_path / 'app.db').as_posix()}"
    apply_migrations(database_url)
    engine = build_engine(database_url)
    store = V3Store(build_session_factory(engine))
    store.add_source(
        source_id="source-1",
        name="Example AI",
        feed_url="https://example.com/feed.xml",
        domain="example.com",
        category="RAG",
    )
    yield store, database_url
    engine.dispose()


@pytest.mark.asyncio
async def test_generates_and_delivers_digest_once(store: tuple[V3Store, str]) -> None:
    v3_store, database_url = store
    notifier = FakeNotifier()
    service = DailyDigestService(
        settings=build_settings(database_url),
        store=v3_store,
        feed_fetcher=FakeFeedFetcher(),  # type: ignore[arg-type]
        notifier=notifier,  # type: ignore[arg-type]
        now=datetime(2026, 10, 20, 8, tzinfo=UTC),
    )

    first = await service.run_today()
    second = await service.run_today()

    assert first.digest.status == "completed"
    assert first.digest.item_count == 1
    assert first.items[0].title == "RAG 检索质量提升"
    assert first.delivery_status == "delivered"
    assert second.digest.status == "completed"
    assert len(notifier.calls) == 1


@pytest.mark.asyncio
async def test_retries_failed_delivery_without_regenerating(
    store: tuple[V3Store, str],
) -> None:
    v3_store, database_url = store
    notifier = FlakyNotifier()
    service = DailyDigestService(
        settings=build_settings(database_url),
        store=v3_store,
        feed_fetcher=FakeFeedFetcher(),  # type: ignore[arg-type]
        notifier=notifier,  # type: ignore[arg-type]
        now=datetime(2026, 10, 20, 8, tzinfo=UTC),
    )

    first = await service.run_today()
    second = await service.run_today()

    assert first.delivery_status == "failed"
    assert second.delivery_status == "delivered"
    assert notifier.calls == 2


@pytest.mark.asyncio
async def test_run_due_catches_up_yesterday_and_today(
    store: tuple[V3Store, str],
) -> None:
    v3_store, database_url = store
    v3_store.save_push_preferences(
        topics=(),
        source_ids=(),
        top_n=3,
        catch_up_days=1,
    )
    notifier = FakeNotifier()
    service = DailyDigestService(
        settings=build_settings(database_url),
        store=v3_store,
        feed_fetcher=CatchUpFeedFetcher(),  # type: ignore[arg-type]
        notifier=notifier,  # type: ignore[arg-type]
        now=datetime(2026, 10, 20, 8, tzinfo=UTC),
    )

    result = await service.run_due()

    assert result.digest.digest_date == "2026-10-20"
    assert result.items[0].title == "今天的 RAG 更新"
    assert len(notifier.calls) == 2
