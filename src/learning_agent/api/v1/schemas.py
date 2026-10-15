"""API 请求与响应 Schema（明确的 Pydantic 模型）。"""

from __future__ import annotations

from datetime import datetime

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


class MessageResponse(BaseModel):
    id: int
    role: str
    content: str
    run_id: str | None = None
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


class RunStatsResponse(BaseModel):
    runs_total: int
    runs_completed: int
    runs_failed: int
    success_rate: float | None = None
    failure_rate: float | None = None
    duration_ms_avg: int | None = None
    duration_ms_p50: int | None = None
    input_tokens_total: int
    output_tokens_total: int
    estimated_cost_total: float | None = None
    tool_calls_total: int
    tool_calls_failed: int
    document_fetches_total: int
    document_fetch_success_rate: float | None = None
    error_codes: dict[str, int] = Field(default_factory=dict)
