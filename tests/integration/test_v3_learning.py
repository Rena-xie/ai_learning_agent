"""V3.2 学习闭环 API 集成测试。"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from learning_agent.config import Settings
from learning_agent.main import create_app

_RSS = """<rss version="2.0"><channel>
<item>
  <title>Agent 记忆设计</title>
  <link>https://example.com/agent-memory</link>
  <description>介绍长期记忆的写入、检索与删除生命周期。</description>
  <pubDate>Tue, 20 Oct 2026 09:00:00 GMT</pubDate>
</item>
</channel></rss>"""


class FakeFeedFetcher:
    async def fetch(self, feed_url: str, *, allowed_domains: tuple[str, ...]) -> str:
        assert feed_url == "https://example.com/feed.xml"
        assert allowed_domains == ("example.com",)
        return _RSS


class FakeKnowledgeClient:
    async def create_ingestion(
        self,
        urls: list[str],
        *,
        client_run_id: str | None = None,
        idempotency_key: str | None = None,
        force_refresh: bool = False,
    ) -> dict[str, object]:
        return {"job_id": "job-1", "status": "queued"}

    async def get_ingestion(self, job_id: str) -> dict[str, object]:
        return {"job_id": job_id, "status": "completed", "items": []}

    async def search(self, question: str, *, top_k: int = 5) -> dict[str, object]:
        return {
            "question": question,
            "top_k": top_k,
            "evidences": [
                {
                    "source_id": "S1",
                    "title": "Agent 记忆设计",
                    "url": "https://example.com/agent-memory",
                }
            ],
        }

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
        feishu_enabled=False,
    )


def test_v32_learning_loop(tmp_path) -> None:
    knowledge = FakeKnowledgeClient()
    app = create_app(
        _settings(f"sqlite:///{(tmp_path / 'app.db').as_posix()}"),
        digest_feed_fetcher=FakeFeedFetcher(),  # type: ignore[arg-type]
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
        )
        assert source.status_code == 201
        digest = client.post("/api/v1/digests/run").json()
        item_id = digest["items"][0]["item_id"]

        note_response = client.post(
            "/api/v1/learning/notes",
            json={"digest_date": "2026-10-20", "item_id": item_id},
        )
        assert note_response.status_code == 200
        note = note_response.json()
        assert note["topic_name"] == "Agent"
        assert "长期记忆" in note["content"]
        assert "[S1]" in note["content"]
        assert note["sources"][0]["url"] == "https://example.com/agent-memory"
        repeated_note = client.post(
            "/api/v1/learning/notes",
            json={"digest_date": "2026-10-20", "item_id": item_id},
        )
        assert repeated_note.json()["id"] == note["id"]

        quiz_response = client.post(
            "/api/v1/learning/quizzes",
            json={"note_id": note["id"], "question_count": 4},
        )
        assert quiz_response.status_code == 200
        quiz = quiz_response.json()
        assert len(quiz["questions"]) == 4
        assert quiz["questions"][0]["source"]["url"].endswith("/agent-memory")

        cards_response = client.post(
            "/api/v1/learning/flashcards",
            json={"note_id": note["id"], "card_count": 4},
        )
        assert cards_response.status_code == 200
        cards = cards_response.json()
        assert len(cards) == 4
        repeated_cards = client.post(
            "/api/v1/learning/flashcards",
            json={"note_id": note["id"], "card_count": 4},
        )
        assert len(repeated_cards.json()) == 4

        due = client.get("/api/v1/learning/reviews/due").json()
        assert due["due_count"] == 4
        reviewed = client.post(
            f"/api/v1/learning/reviews/{cards[0]['id']}",
            json={"rating": "good"},
        )
        assert reviewed.status_code == 200
        assert reviewed.json()["interval_days"] == 3
        assert reviewed.json()["review_count"] == 1

        lapsed = client.post(
            f"/api/v1/learning/reviews/{cards[1]['id']}",
            json={"rating": "again"},
        )
        assert lapsed.json()["lapse_count"] == 1

        due_after = client.get("/api/v1/learning/reviews/due").json()
        assert due_after["due_count"] == 2
        progress = client.get("/api/v1/learning/progress").json()
        assert progress["notes_total"] == 1
        assert progress["quizzes_total"] == 1
        assert progress["flashcards_total"] == 4
        assert progress["reviews_total"] == 2
        assert progress["retention_rate"] == 0.5
        assert progress["topics"][0]["needs_attention"] is True

        notes = client.get("/api/v1/learning/notes").json()
        assert [item["id"] for item in notes] == [note["id"]]

        search = client.post(
            "/api/v1/learning/collections/default/search",
            json={"question": "长期记忆有哪些生命周期？", "top_k": 3},
        )
        assert search.status_code == 200
        assert search.json()["collection_id"] == "default"
        assert search.json()["evidences"][0]["url"].endswith("/agent-memory")

        missing_collection = client.post(
            "/api/v1/learning/collections/unknown/search",
            json={"question": "测试", "top_k": 3},
        )
        assert missing_collection.status_code == 404
