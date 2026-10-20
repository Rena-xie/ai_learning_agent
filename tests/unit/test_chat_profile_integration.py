"""ChatService 完成运行后写入学习画像的集成点。"""

from __future__ import annotations

import pytest

from learning_agent.agent.runtime import RunOutcome
from learning_agent.application.chat_service import ChatService
from learning_agent.application.run_scheduler import RunScheduler
from learning_agent.domain.enums import RunStatus


class FakeRepositories:
    async def get_conversation(self, _conversation_id: str) -> object:
        return object()

    async def set_title_if_default(self, _conversation_id: str, *, title: str) -> None:
        return None


class FakeRuntime:
    async def run(self, *, conversation_id: str, user_message: str) -> RunOutcome:
        assert conversation_id == "conv-1"
        assert user_message == "RAG 的 RRF 怎么理解？"
        return RunOutcome(
            run_id="run-1",
            status=RunStatus.COMPLETED,
            content="RRF 是排名融合算法。",
        )


class FakeProfile:
    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    async def record_completed_run(self, **kwargs: str) -> None:
        self.calls.append(kwargs)


@pytest.mark.asyncio
async def test_completed_chat_records_profile_event() -> None:
    profile = FakeProfile()
    service = ChatService(
        runtime=FakeRuntime(),  # type: ignore[arg-type]
        repositories=FakeRepositories(),
        max_input_chars=1000,
        scheduler=RunScheduler(),
        profile_service=profile,  # type: ignore[arg-type]
    )

    await service.submit_message(
        conversation_id="conv-1", content="RAG 的 RRF 怎么理解？"
    )

    assert profile.calls == [
        {
            "conversation_id": "conv-1",
            "run_id": "run-1",
            "user_message": "RAG 的 RRF 怎么理解？",
            "assistant_message": "RRF 是排名融合算法。",
        }
    ]
