"""API 请求与响应 Schema（明确的 Pydantic 模型）。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class ConversationCreateRequest(BaseModel):
    title: str | None = Field(default=None, max_length=120)


class ConversationResponse(BaseModel):
    id: str
    title: str
    created_at: datetime
    updated_at: datetime


class SourceResponse(BaseModel):
    source_id: str
    title: str
    url: str
    fetched_at: datetime
    truncated: bool = False
    provider: str = "fetch"


class MessageResponse(BaseModel):
    id: int
    role: str
    content: str
    run_id: str | None = None
    kind: str = "chat"
    created_at: datetime


class MessageWithSourcesResponse(MessageResponse):
    sources: list[SourceResponse] = Field(default_factory=list)


class MessageCreateRequest(BaseModel):
    content: str = Field(min_length=1, max_length=100_000)


class ErrorDetail(BaseModel):
    code: str
    category: str
    message: str


class SendMessageResponse(BaseModel):
    """提交消息后返回的运行结果（成功或失败都如实返回）。"""

    run_id: str
    status: str
    message: MessageWithSourcesResponse | None = None
    sources: list[SourceResponse] = Field(default_factory=list)
    error: ErrorDetail | None = None
    notes: list[str] = Field(default_factory=list)


class RunAcceptedResponse(BaseModel):
    """异步提交后的受理结果：运行已在后台开始。"""

    run_id: str
    status: str


class ToolCallResponse(BaseModel):
    id: str
    tool_name: str
    status: str
    duration_ms: int | None = None
    error_code: str | None = None
    arguments_summary: str = ""
    result_summary: str = ""


class RunResponse(BaseModel):
    run_id: str
    conversation_id: str
    status: str
    model_name: str | None = None
    prompt_version: str | None = None
    started_at: datetime
    finished_at: datetime | None = None
    duration_ms: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    error_code: str | None = None
    error_category: str | None = None
    tool_calls: list[ToolCallResponse] = Field(default_factory=list)
    sources: list[SourceResponse] = Field(default_factory=list)


class HealthResponse(BaseModel):
    status: str
    database_ok: bool | None = None
    model_configured: bool | None = None
    knowledge_base: str | None = None


class RunStatsResponse(BaseModel):
    runs_total: int
    runs_completed: int
    runs_failed: int
    success_rate: float | None = None
    failure_rate: float | None = None
    duration_ms_avg: int | None = None
    duration_ms_p50: int | None = None
    duration_ms_p95: int | None = None
    input_tokens_total: int
    output_tokens_total: int
    estimated_cost_total: float | None = None
    tool_calls_total: int
    tool_calls_failed: int
    document_fetches_total: int
    document_fetch_success_rate: float | None = None
    knowledge_tool_calls: int = 0
    knowledge_tool_failed: int = 0
    knowledge_tool_failure_rate: float | None = None
    knowledge_tool_timeout: int = 0
    knowledge_tool_timeout_rate: float | None = None
    knowledge_tool_avg_ms: int | None = None
    ingestion_watch_backlog: int = 0
    error_codes: dict[str, int] = Field(default_factory=dict)


class PushSourceResponse(BaseModel):
    id: str
    name: str
    feed_url: str
    domain: str
    category: str
    trust_level: str
    enabled: bool


class PushSourceCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    feed_url: str = Field(min_length=1, max_length=2048)
    domain: str = Field(min_length=1, max_length=255)
    category: str = Field(default="AI", max_length=64)


class PushPreferencesResponse(BaseModel):
    topics: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)
    top_n: int
    catch_up_days: int


class PushPreferencesUpdateRequest(BaseModel):
    topics: list[str] | None = Field(default=None, max_length=20)
    source_ids: list[str] | None = Field(default=None, max_length=100)
    top_n: int | None = Field(default=None, ge=1, le=30)
    catch_up_days: int | None = Field(default=None, ge=0, le=7)


class DigestItemResponse(BaseModel):
    item_id: int
    title: str
    url: str
    summary: str
    topic: str
    source_name: str
    published_at: datetime | None = None
    rank: int
    feedback_action: str | None = None
    ingestion_job_id: str | None = None
    ingestion_status: str | None = None


class DigestResponse(BaseModel):
    digest_date: str
    status: str
    item_count: int
    content: str
    error_message: str | None = None
    delivery_status: str | None = None
    items: list[DigestItemResponse] = Field(default_factory=list)


class DigestFeedbackRequest(BaseModel):
    item_id: int = Field(gt=0)
    action: Literal["useful", "not_useful"]


class DigestFeedbackResponse(BaseModel):
    item_id: int
    action: str


class DigestIngestionResponse(BaseModel):
    item_id: int
    url: str
    job_id: str
    status: str
    error_message: str | None = None


class LearningConceptResponse(BaseModel):
    id: int
    name: str
    mastery: str
    confidence: float
    evidence_count: int
    user_corrected: bool


class LearningTopicResponse(BaseModel):
    id: int
    name: str
    status: str
    last_seen_at: datetime
    concepts: list[LearningConceptResponse] = Field(default_factory=list)


class LearningProfileResponse(BaseModel):
    enabled: bool
    topics: list[LearningTopicResponse] = Field(default_factory=list)


class LearningProfileSettingsRequest(BaseModel):
    enabled: bool


class LearningProfileSettingsResponse(BaseModel):
    enabled: bool


class LearningTopicUpdateRequest(BaseModel):
    status: Literal["learning", "paused", "completed"]


class LearningConceptUpdateRequest(BaseModel):
    mastery: Literal["weak", "learning", "strong"]
