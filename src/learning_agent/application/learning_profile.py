"""V3 跨对话学习画像的基础规则提取与读取。"""

from __future__ import annotations

import re

from learning_agent.observability.logging import get_logger
from learning_agent.persistence.v3_store import LearningProfileSnapshot, V3Store

logger = get_logger(__name__)

_WEAK_PATTERN = re.compile(
    r"(没懂|不懂|不明白|记不住|总记不住|容易混|不会|薄弱|没掌握)"
)

_TOPIC_KEYWORDS: dict[str, tuple[str, ...]] = {
    "RAG": ("rag", "检索增强", "召回", "rerank", "向量数据库", "chunk", "rrf"),
    "Agent": ("agent", "智能体", "工具调用", "function calling", "tool loop"),
    "LangChain": ("langchain",),
    "LangGraph": ("langgraph",),
    "FastAPI": ("fastapi",),
    "Pydantic": ("pydantic",),
    "SQLAlchemy": ("sqlalchemy",),
    "评测": ("评测", "评估", "recall", "mrr", "ndcg", "hitrate"),
}

_CONCEPT_KEYWORDS: dict[str, tuple[str, ...]] = {
    "RAG": (
        "rrf",
        "rerank",
        "chunk",
        "embedding",
        "向量数据库",
        "recall@5",
        "recall",
        "mrr",
        "ndcg",
        "hitrate",
        "召回",
    ),
    "Agent": ("tool loop", "工具调用", "function calling", "上下文", "规划"),
    "LangChain": ("tool", "prompt", "chain", "memory"),
    "LangGraph": ("state", "node", "edge", "checkpoint", "中断"),
    "FastAPI": ("依赖注入", "lifespan", "中间件", "路由", "async"),
    "Pydantic": ("model", "validator", "settings", "schema"),
    "SQLAlchemy": ("session", "transaction", "orm", "migration"),
    "评测": ("recall@5", "recall", "precision", "mrr", "ndcg", "hitrate"),
}


class LearningProfileService:
    """从已完成的普通对话中提取可解释的学习事件。"""

    def __init__(self, *, store: V3Store, enabled: bool = True) -> None:
        self._store = store
        self._enabled = enabled

    async def record_completed_run(
        self,
        *,
        conversation_id: str,
        run_id: str,
        user_message: str,
        assistant_message: str,
    ) -> None:
        if not self.is_enabled():
            return
        try:
            self._record(
                conversation_id=conversation_id,
                run_id=run_id,
                user_message=user_message,
                assistant_message=assistant_message,
            )
        except Exception:  # noqa: BLE001 - 画像失败不能影响主回答
            logger.exception("learning_profile_extract_failed")

    def _record(
        self,
        *,
        conversation_id: str,
        run_id: str,
        user_message: str,
        assistant_message: str,
    ) -> None:
        text = f"{user_message}\n{assistant_message}".casefold()
        weak = bool(_WEAK_PATTERN.search(user_message.casefold()))
        matched_topics = [
            topic
            for topic, keywords in _TOPIC_KEYWORDS.items()
            if any(keyword in text for keyword in keywords)
        ]
        for topic in matched_topics:
            concepts = [
                concept
                for concept in _CONCEPT_KEYWORDS.get(topic, ())
                if concept in text
            ]
            if not concepts:
                self._store.record_learning_event(
                    topic_name=topic,
                    concept_name=None,
                    conversation_id=conversation_id,
                    run_id=run_id,
                    event_type="weak_signal" if weak else "topic_seen",
                    evidence_text=user_message,
                    confidence=0.8 if weak else 0.6,
                )
                continue
            for concept in concepts:
                self._store.record_learning_event(
                    topic_name=topic,
                    concept_name=concept,
                    conversation_id=conversation_id,
                    run_id=run_id,
                    event_type="weak_signal" if weak else "topic_seen",
                    evidence_text=user_message,
                    confidence=0.8 if weak else 0.6,
                )

    def snapshot(self) -> LearningProfileSnapshot:
        return self._store.learning_profile()

    def is_enabled(self) -> bool:
        return self._enabled and self._store.learning_profile_enabled()

    def set_enabled(self, *, enabled: bool) -> bool:
        if not self._enabled:
            return False
        return self._store.set_learning_profile_enabled(enabled=enabled)

    def update_topic_status(self, topic_id: int, *, status: str) -> None:
        self._store.update_learning_topic_status(topic_id, status=status)

    def delete_topic(self, topic_id: int) -> None:
        self._store.delete_learning_topic(topic_id)

    def update_concept_mastery(self, concept_id: int, *, mastery: str) -> None:
        self._store.update_learning_concept_mastery(concept_id, mastery=mastery)

    def clear(self) -> None:
        self._store.clear_profile()
