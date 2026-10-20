"""V3 Digest、来源与学习画像 API 集成测试。"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from learning_agent.config import Settings
from learning_agent.digest.feishu import FeishuResult
from learning_agent.main import create_app

_RSS = """<rss version="2.0"><channel>
<item>
  <title>Agent 工程更新</title>
  <link>https://example.com/agent</link>
  <description>介绍 Agent 工具调用。</description>
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
        self.calls = 0

    async def send(self, _text: str) -> FeishuResult:
        self.calls += 1
        return FeishuResult(status="delivered", attempts=1)

    async def close(self) -> None:
        return None


def build_settings(database_url: str) -> Settings:
    return Settings(
        _env_file=None,
        database_url=database_url,
        model_api_key="test",
        model_name="test-model",
        model_base_url="https://example.invalid/v1",
        digest_enabled=True,
        digest_seed_default_sources=False,
        digest_trigger="manual",
        digest_timezone="UTC",
        feishu_enabled=True,
    )


def test_v3_digest_and_profile_api(tmp_path) -> None:
    notifier = FakeNotifier()
    settings = build_settings(f"sqlite:///{(tmp_path / 'app.db').as_posix()}")
    app = create_app(
        settings,
        digest_feed_fetcher=FakeFeedFetcher(),  # type: ignore[arg-type]
        feishu_notifier=notifier,  # type: ignore[arg-type]
        digest_now=datetime(2026, 10, 20, 8, tzinfo=UTC),
    )

    with TestClient(app) as client:
        source = client.post(
            "/api/v1/push/sources",
            json={
                "name": "Example AI",
                "feed_url": "https://example.com/feed.xml",
                "domain": "example.com",
                "category": "Agent",
            },
        )
        assert source.status_code == 201

        run = client.post("/api/v1/digests/run")
        assert run.status_code == 200
        assert run.json()["status"] == "completed"
        assert run.json()["item_count"] == 1
        assert run.json()["delivery_status"] == "delivered"
        assert notifier.calls == 1

        today = client.get("/api/v1/digests/today")
        assert today.status_code == 200
        assert today.json()["digest_date"] == "2026-10-20"

        profile = client.get("/api/v1/learning-profile")
        assert profile.status_code == 200
    assert profile.json() == {"enabled": True, "topics": []}
