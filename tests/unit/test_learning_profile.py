"""V3 学习画像规则提取测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from learning_agent.application.learning_profile import LearningProfileService
from learning_agent.persistence.database import build_engine, build_session_factory
from learning_agent.persistence.migrations import apply_migrations
from learning_agent.persistence.v3_store import V3Store


@pytest.fixture
def service(tmp_path: Path) -> LearningProfileService:
    database_url = f"sqlite:///{(tmp_path / 'app.db').as_posix()}"
    apply_migrations(database_url)
    engine = build_engine(database_url)
    yield LearningProfileService(
        store=V3Store(build_session_factory(engine)), enabled=True
    )
    engine.dispose()


@pytest.mark.asyncio
async def test_extracts_weak_concept_across_conversations(
    service: LearningProfileService,
) -> None:
    await service.record_completed_run(
        conversation_id="conv-1",
        run_id="run-1",
        user_message="我总是记不住 RRF 和 Rerank",
        assistant_message="RRF 是排名融合算法。",
    )
    await service.record_completed_run(
        conversation_id="conv-2",
        run_id="run-2",
        user_message="RAG 的 Recall@5 怎么理解？",
        assistant_message="Recall@5 衡量相关片段覆盖率。",
    )

    profile = service.snapshot()

    topic = next(item for item in profile.topics if item["name"] == "RAG")
    assert topic["name"] == "RAG"
    concepts = {item["name"]: item for item in topic["concepts"]}
    assert concepts["rrf"]["mastery"] == "weak"
    assert concepts["rrf"]["evidence_count"] == 1
    assert concepts["recall"]["evidence_count"] == 1


@pytest.mark.asyncio
async def test_ignores_messages_without_known_topic(
    service: LearningProfileService,
) -> None:
    await service.record_completed_run(
        conversation_id="conv-1",
        run_id="run-1",
        user_message="今天天气怎么样？",
        assistant_message="我无法回答天气。",
    )

    assert service.snapshot().topics == ()
