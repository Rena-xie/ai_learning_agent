"""V3 跨对话学习画像查询。"""

from __future__ import annotations

from fastapi import APIRouter, Request

from learning_agent.api.v1.schemas import (
    LearningConceptResponse,
    LearningProfileResponse,
    LearningTopicResponse,
)
from learning_agent.application.learning_profile import LearningProfileService
from learning_agent.domain.errors import AppError, ErrorCode

router = APIRouter(prefix="/api/v1/learning-profile", tags=["learning"])


def _service(request: Request) -> LearningProfileService:
    if not request.app.state.settings.learning_profile_enabled:
        raise AppError(ErrorCode.LEARNING_PROFILE_UNAVAILABLE, "学习画像未启用")
    service = getattr(request.app.state, "learning_profile_service", None)
    if service is None:
        raise AppError(ErrorCode.LEARNING_PROFILE_UNAVAILABLE, "学习画像未启用")
    return service


@router.get("", response_model=LearningProfileResponse)
async def get_profile(request: Request) -> LearningProfileResponse:
    snapshot = _service(request).snapshot()
    return LearningProfileResponse(
        topics=[
            LearningTopicResponse(
                name=str(item["name"]),
                status=str(item["status"]),
                last_seen_at=item["last_seen_at"],  # type: ignore[arg-type]
                concepts=[
                    LearningConceptResponse(
                        name=str(concept["name"]),
                        mastery=str(concept["mastery"]),
                        confidence=float(concept["confidence"]),
                        evidence_count=int(concept["evidence_count"]),
                    )
                    for concept in item["concepts"]  # type: ignore[union-attr]
                ],
            )
            for item in snapshot.topics
        ]
    )
