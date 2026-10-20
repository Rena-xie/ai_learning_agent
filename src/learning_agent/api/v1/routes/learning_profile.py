"""V3 跨对话学习画像查询。"""

from __future__ import annotations

from fastapi import APIRouter, Request

from learning_agent.api.v1.schemas import (
    LearningConceptResponse,
    LearningConceptUpdateRequest,
    LearningProfileResponse,
    LearningProfileSettingsRequest,
    LearningProfileSettingsResponse,
    LearningTopicResponse,
    LearningTopicUpdateRequest,
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
    service = _service(request)
    snapshot = service.snapshot()
    return LearningProfileResponse(
        enabled=service.is_enabled(),
        topics=[
            LearningTopicResponse(
                id=int(item["id"]),
                name=str(item["name"]),
                status=str(item["status"]),
                last_seen_at=item["last_seen_at"],  # type: ignore[arg-type]
                concepts=[
                    LearningConceptResponse(
                        id=int(concept["id"]),
                        name=str(concept["name"]),
                        mastery=str(concept["mastery"]),
                        confidence=float(concept["confidence"]),
                        evidence_count=int(concept["evidence_count"]),
                        user_corrected=bool(concept["user_corrected"]),
                    )
                    for concept in item["concepts"]  # type: ignore[union-attr]
                ],
            )
            for item in snapshot.topics
        ],
    )


@router.patch("/settings", response_model=LearningProfileSettingsResponse)
async def update_settings(
    payload: LearningProfileSettingsRequest, request: Request
) -> LearningProfileSettingsResponse:
    enabled = _service(request).set_enabled(enabled=payload.enabled)
    return LearningProfileSettingsResponse(enabled=enabled)


@router.patch("/topics/{topic_id}", status_code=204)
async def update_topic(
    topic_id: int, payload: LearningTopicUpdateRequest, request: Request
) -> None:
    _service(request).update_topic_status(topic_id, status=payload.status)


@router.delete("/topics/{topic_id}", status_code=204)
async def delete_topic(topic_id: int, request: Request) -> None:
    _service(request).delete_topic(topic_id)


@router.patch("/concepts/{concept_id}", status_code=204)
async def update_concept(
    concept_id: int,
    payload: LearningConceptUpdateRequest,
    request: Request,
) -> None:
    _service(request).update_concept_mastery(concept_id, mastery=payload.mastery)


@router.post("/reset", status_code=204)
async def reset_profile(request: Request) -> None:
    _service(request).clear()
