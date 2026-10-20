"""V3.1 偏好、Digest 操作与学习画像控制 API。"""

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
    async def send(self, _text: str) -> FeishuResult:
        return FeishuResult(status="delivered", attempts=1)

    async def close(self) -> None:
        return None


class FakeKnowledgeClient:
    def __init__(self) -> None:
        self.create_calls = 0

    async def create_ingestion(
        self,
        urls: list[str],
        *,
        client_run_id: str | None = None,
        idempotency_key: str | None = None,
        force_refresh: bool = False,
    ) -> dict[str, object]:
        assert urls == ["https://example.com/agent"]
        assert client_run_id == "digest-2026-10-20"
        assert idempotency_key == "digest-2026-10-20-1"
        self.create_calls += 1
        return {"job_id": "job-1", "status": "queued"}

    async def get_ingestion(self, job_id: str) -> dict[str, object]:
        return {"job_id": job_id, "status": "completed", "items": []}

    async def search(self, question: str, *, top_k: int = 5) -> dict[str, object]:
        return {"question": question, "top_k": top_k, "evidences": []}

    async def list_sources(self, limit: int = 10) -> dict[str, object]:
        return {"items": [], "total": 0}

    async def health(self) -> bool:
        return True

    async def aclose(self) -> None:
        return None


def _settings(database_url: str) -> Settings:
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


def test_v31_control_flow(tmp_path) -> None:
    knowledge = FakeKnowledgeClient()
    app = create_app(
        _settings(f"sqlite:///{(tmp_path / 'app.db').as_posix()}"),
        digest_feed_fetcher=FakeFeedFetcher(),  # type: ignore[arg-type]
        feishu_notifier=FakeNotifier(),  # type: ignore[arg-type]
        knowledge_client=knowledge,  # type: ignore[arg-type]
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
        ).json()
        preferences = client.patch(
            "/api/v1/push/preferences",
            json={
                "topics": ["Agent"],
                "source_ids": [source["id"]],
                "top_n": 1,
                "catch_up_days": 0,
            },
        )
        assert preferences.status_code == 200
        assert preferences.json()["topics"] == ["Agent"]
        assert source["last_checked_at"] is None

        disabled_source = client.patch(
            f"/api/v1/push/sources/{source['id']}",
            json={"enabled": False},
        )
        assert disabled_source.status_code == 200
        assert disabled_source.json()["enabled"] is False
        reenabled_source = client.patch(
            f"/api/v1/push/sources/{source['id']}",
            json={"enabled": True},
        )
        assert reenabled_source.json()["enabled"] is True
        missing_source = client.patch(
            "/api/v1/push/sources/not-found",
            json={"enabled": False},
        )
        assert missing_source.status_code == 404

        digest = client.post("/api/v1/digests/run").json()
        item_id = digest["items"][0]["item_id"]
        checked_source = client.get("/api/v1/push/sources").json()[0]
        assert checked_source["last_checked_at"] is not None
        assert checked_source["last_error"] is None
        feedback = client.post(
            "/api/v1/digests/2026-10-20/feedback",
            json={"item_id": item_id, "action": "useful"},
        )
        assert feedback.status_code == 200
        saved = client.post(f"/api/v1/digests/2026-10-20/items/{item_id}/save")
        assert saved.status_code == 200
        assert saved.json()["job_id"] == "job-1"
        repeated = client.post(f"/api/v1/digests/2026-10-20/items/{item_id}/save")
        assert repeated.json()["job_id"] == "job-1"
        assert knowledge.create_calls == 1
        ingestion_status = client.get(
            f"/api/v1/digests/2026-10-20/items/{item_id}/ingestion"
        )
        assert ingestion_status.status_code == 200
        assert ingestion_status.json()["status"] == "completed"

        today = client.get("/api/v1/digests/today").json()
        assert today["items"][0]["feedback_action"] == "useful"
        assert today["items"][0]["ingestion_status"] == "completed"

        app.state.v3_store.record_learning_event(
            topic_name="RAG",
            concept_name="rrf",
            conversation_id="conv-1",
            run_id="run-1",
            event_type="weak_signal",
            evidence_text="我总是记不住 RRF",
            confidence=0.8,
        )
        profile = client.get("/api/v1/learning-profile").json()
        concept_id = profile["topics"][0]["concepts"][0]["id"]
        corrected = client.patch(
            f"/api/v1/learning-profile/concepts/{concept_id}",
            json={"mastery": "strong"},
        )
        assert corrected.status_code == 204
        app.state.v3_store.record_learning_event(
            topic_name="RAG",
            concept_name="rrf",
            conversation_id="conv-2",
            run_id="run-2",
            event_type="weak_signal",
            evidence_text="这里还是不太懂 RRF",
            confidence=0.8,
        )
        corrected_profile = client.get("/api/v1/learning-profile").json()
        concept = corrected_profile["topics"][0]["concepts"][0]
        assert concept["mastery"] == "strong"
        assert concept["user_corrected"] is True

        disabled = client.patch(
            "/api/v1/learning-profile/settings",
            json={"enabled": False},
        )
        assert disabled.json() == {"enabled": False}
        assert app.state.learning_profile_service.is_enabled() is False

        reset = client.post("/api/v1/learning-profile/reset")
        assert reset.status_code == 204
        assert client.get("/api/v1/learning-profile").json()["topics"] == []
