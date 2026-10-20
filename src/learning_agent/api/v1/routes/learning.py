"""V3.2 学习笔记、自测、闪卡、复习与主题检索 API。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Request

from learning_agent.api.v1.schemas import (
    LearningCollectionSearchRequest,
    LearningCollectionSearchResponse,
    LearningConceptUpdateRequest,
    LearningDueReviewsResponse,
    LearningFlashcardCreateRequest,
    LearningFlashcardResponse,
    LearningNoteCreateRequest,
    LearningNoteResponse,
    LearningProgressResponse,
    LearningQuizCreateRequest,
    LearningQuizQuestionResponse,
    LearningQuizResponse,
    LearningReviewRequest,
    LearningSourceResponse,
)
from learning_agent.application.learning_workspace import LearningWorkspaceService
from learning_agent.domain.errors import AppError, ErrorCode
from learning_agent.domain.urls import normalize_external_url
from learning_agent.persistence.learning_store import (
    FlashcardRecord,
    LearningNoteRecord,
    LearningQuizRecord,
    NoteSourceRecord,
)

router = APIRouter(prefix="/api/v1/learning", tags=["learning"])


def _service(request: Request) -> LearningWorkspaceService:
    service = getattr(request.app.state, "learning_workspace_service", None)
    if service is None:
        raise AppError(ErrorCode.LEARNING_PROFILE_UNAVAILABLE, "学习工作台未启用")
    return service


@router.get("/notes", response_model=list[LearningNoteResponse])
async def list_notes(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[LearningNoteResponse]:
    return [_note_response(note) for note in _service(request).list_notes(limit=limit)]


@router.post("/notes", response_model=LearningNoteResponse)
async def create_note(
    payload: LearningNoteCreateRequest, request: Request
) -> LearningNoteResponse:
    note = _service(request).create_note_from_digest(
        digest_date=payload.digest_date,
        item_id=payload.item_id,
    )
    return _note_response(note)


@router.get("/notes/{note_id}", response_model=LearningNoteResponse)
async def get_note(note_id: int, request: Request) -> LearningNoteResponse:
    return _note_response(_service(request).get_note(note_id))


@router.post("/quizzes", response_model=LearningQuizResponse)
async def create_quiz(
    payload: LearningQuizCreateRequest, request: Request
) -> LearningQuizResponse:
    quiz = _service(request).create_quiz(
        note_id=payload.note_id,
        question_count=payload.question_count,
    )
    return _quiz_response(quiz)


@router.get("/quizzes/{quiz_id}", response_model=LearningQuizResponse)
async def get_quiz(quiz_id: int, request: Request) -> LearningQuizResponse:
    return _quiz_response(_service(request).get_quiz(quiz_id))


@router.post("/flashcards", response_model=list[LearningFlashcardResponse])
async def create_flashcards(
    payload: LearningFlashcardCreateRequest, request: Request
) -> list[LearningFlashcardResponse]:
    cards = _service(request).create_flashcards(
        note_id=payload.note_id,
        card_count=payload.card_count,
    )
    return [_flashcard_response(card) for card in cards]


@router.get("/flashcards", response_model=list[LearningFlashcardResponse])
async def list_flashcards(
    request: Request,
    note_id: Annotated[int | None, Query(gt=0)] = None,
) -> list[LearningFlashcardResponse]:
    return [
        _flashcard_response(card)
        for card in _service(request).list_flashcards(note_id=note_id)
    ]


@router.get("/reviews/due", response_model=LearningDueReviewsResponse)
async def due_reviews(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> LearningDueReviewsResponse:
    cards = _service(request).due_reviews(limit=limit)
    return LearningDueReviewsResponse(
        due_count=len(cards),
        cards=[_flashcard_response(card) for card in cards],
    )


@router.post("/reviews/{flashcard_id}", response_model=LearningFlashcardResponse)
async def review_flashcard(
    flashcard_id: int,
    payload: LearningReviewRequest,
    request: Request,
) -> LearningFlashcardResponse:
    card = _service(request).submit_review(
        flashcard_id=flashcard_id,
        rating=payload.rating,
    )
    return _flashcard_response(card)


@router.get("/progress", response_model=LearningProgressResponse)
async def learning_progress(request: Request) -> LearningProgressResponse:
    return LearningProgressResponse(**_service(request).progress())


@router.patch("/concepts/{concept_id}", status_code=204)
async def update_concept(
    concept_id: int,
    payload: LearningConceptUpdateRequest,
    request: Request,
) -> None:
    service = getattr(request.app.state, "learning_profile_service", None)
    if service is None or not service.is_enabled():
        raise AppError(ErrorCode.LEARNING_PROFILE_UNAVAILABLE, "学习画像未启用")
    service.update_concept_mastery(concept_id, mastery=payload.mastery)


@router.post(
    "/collections/{collection_id}/search",
    response_model=LearningCollectionSearchResponse,
)
async def search_collection(
    collection_id: str,
    payload: LearningCollectionSearchRequest,
    request: Request,
) -> LearningCollectionSearchResponse:
    payload_view = await _service(request).search_collection(
        collection_id=collection_id,
        question=payload.question,
        top_k=payload.top_k,
    )
    evidences = payload_view.get("evidences")
    return LearningCollectionSearchResponse(
        collection_id=str(payload_view["collection_id"]),
        question=str(payload_view["question"]),
        top_k=int(payload_view["top_k"]),
        evidences=evidences if isinstance(evidences, list) else [],
    )


def _source_response(source: NoteSourceRecord | None) -> LearningSourceResponse | None:
    if source is None:
        return None
    try:
        safe_url = normalize_external_url(source.url)
    except ValueError:
        return None
    return LearningSourceResponse(
        id=source.id,
        label=source.label,
        title=source.title,
        publisher=source.publisher,
        url=safe_url,
        provider=source.provider,
        excerpt=source.excerpt,
    )


def _note_response(note: LearningNoteRecord) -> LearningNoteResponse:
    return LearningNoteResponse(
        id=note.id,
        push_item_id=note.push_item_id,
        topic_id=note.topic_id,
        digest_date=note.digest_date,
        topic_name=note.topic_name,
        title=note.title,
        content=note.content,
        created_at=note.created_at,
        updated_at=note.updated_at,
        sources=[
            source
            for item in note.sources
            if (source := _source_response(item)) is not None
        ],
    )


def _quiz_response(quiz: LearningQuizRecord) -> LearningQuizResponse:
    return LearningQuizResponse(
        id=quiz.id,
        note_id=quiz.note_id,
        created_at=quiz.created_at,
        questions=[
            LearningQuizQuestionResponse(
                id=item.id,
                position=item.position,
                question=item.question,
                answer=item.answer,
                explanation=item.explanation,
                source=_source_response(item.source),
            )
            for item in quiz.questions
        ],
    )


def _flashcard_response(card: FlashcardRecord) -> LearningFlashcardResponse:
    return LearningFlashcardResponse(
        id=card.id,
        note_id=card.note_id,
        topic_name=card.topic_name,
        question=card.question,
        answer=card.answer,
        interval_days=card.interval_days,
        review_count=card.review_count,
        lapse_count=card.lapse_count,
        last_reviewed_at=card.last_reviewed_at,
        next_review_at=card.next_review_at,
        created_at=card.created_at,
        source=_source_response(card.source),
    )
