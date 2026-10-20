"""统一错误分类体系。

设计要点：
* 每个错误码属于一个类别，用于区分用户输入问题、外部依赖失败与内部错误。
* 错误码映射到 HTTP 状态码，供 API 层统一转换，避免各路由自行拼装。
* 用户可见信息（``message``）由抛出点给出，必须是安全文案；
  ``detail`` 只用于内部日志，不得返回给用户或模型。
"""

from __future__ import annotations

from enum import StrEnum


class ErrorCategory(StrEnum):
    """错误大类。"""

    INVALID_REQUEST = "invalid_request"
    URL_POLICY = "url_policy"
    DOCUMENT = "document"
    MODEL = "model"
    RESOURCE_LIMIT = "resource_limit"
    KNOWLEDGE_BASE = "knowledge_base"
    PERSISTENCE = "persistence"
    INTERNAL = "internal"


class ErrorCode(StrEnum):
    """结构化错误码。可按实际实现调整，但必须保持类别区分。"""

    INVALID_REQUEST = "invalid_request"
    NOT_FOUND = "not_found"
    RUN_IN_PROGRESS = "run_in_progress"

    URL_NOT_ALLOWED = "url_not_allowed"
    SSRF_TARGET_BLOCKED = "ssrf_target_blocked"

    UNSUPPORTED_CONTENT_TYPE = "unsupported_content_type"
    FETCH_TIMEOUT = "fetch_timeout"
    FETCH_HTTP_ERROR = "fetch_http_error"
    CONTENT_EXTRACTION_FAILED = "content_extraction_failed"
    DOCUMENT_TOO_LARGE = "document_too_large"

    CONTEXT_BUDGET_EXCEEDED = "context_budget_exceeded"
    TOOL_LIMIT_EXCEEDED = "tool_limit_exceeded"
    MODEL_ROUND_LIMIT_EXCEEDED = "model_round_limit_exceeded"
    RUN_TIMEOUT = "run_timeout"
    RUN_INTERRUPTED = "run_interrupted"

    KNOWLEDGE_BASE_UNAVAILABLE = "knowledge_base_unavailable"
    KNOWLEDGE_BASE_TIMEOUT = "knowledge_base_timeout"
    KNOWLEDGE_BASE_REJECTED = "knowledge_base_rejected"
    KNOWLEDGE_BASE_BUSY = "knowledge_base_busy"
    INGESTION_NOT_FOUND = "ingestion_not_found"
    DIGEST_UNAVAILABLE = "digest_unavailable"
    LEARNING_PROFILE_UNAVAILABLE = "learning_profile_unavailable"

    MODEL_TIMEOUT = "model_timeout"
    MODEL_RATE_LIMITED = "model_rate_limited"
    MODEL_ERROR = "model_error"
    MODEL_CONTEXT_EXCEEDED = "model_context_exceeded"

    PERSISTENCE_ERROR = "persistence_error"
    CONFIGURATION_ERROR = "configuration_error"
    INTERNAL_ERROR = "internal_error"


_ERROR_CATEGORY: dict[ErrorCode, ErrorCategory] = {
    ErrorCode.INVALID_REQUEST: ErrorCategory.INVALID_REQUEST,
    ErrorCode.NOT_FOUND: ErrorCategory.INVALID_REQUEST,
    ErrorCode.RUN_IN_PROGRESS: ErrorCategory.INVALID_REQUEST,
    ErrorCode.URL_NOT_ALLOWED: ErrorCategory.URL_POLICY,
    ErrorCode.SSRF_TARGET_BLOCKED: ErrorCategory.URL_POLICY,
    ErrorCode.UNSUPPORTED_CONTENT_TYPE: ErrorCategory.DOCUMENT,
    ErrorCode.FETCH_TIMEOUT: ErrorCategory.DOCUMENT,
    ErrorCode.FETCH_HTTP_ERROR: ErrorCategory.DOCUMENT,
    ErrorCode.CONTENT_EXTRACTION_FAILED: ErrorCategory.DOCUMENT,
    ErrorCode.DOCUMENT_TOO_LARGE: ErrorCategory.DOCUMENT,
    ErrorCode.CONTEXT_BUDGET_EXCEEDED: ErrorCategory.RESOURCE_LIMIT,
    ErrorCode.TOOL_LIMIT_EXCEEDED: ErrorCategory.RESOURCE_LIMIT,
    ErrorCode.MODEL_ROUND_LIMIT_EXCEEDED: ErrorCategory.RESOURCE_LIMIT,
    ErrorCode.RUN_TIMEOUT: ErrorCategory.RESOURCE_LIMIT,
    ErrorCode.RUN_INTERRUPTED: ErrorCategory.INTERNAL,
    ErrorCode.KNOWLEDGE_BASE_UNAVAILABLE: ErrorCategory.KNOWLEDGE_BASE,
    ErrorCode.KNOWLEDGE_BASE_TIMEOUT: ErrorCategory.KNOWLEDGE_BASE,
    ErrorCode.KNOWLEDGE_BASE_REJECTED: ErrorCategory.KNOWLEDGE_BASE,
    ErrorCode.KNOWLEDGE_BASE_BUSY: ErrorCategory.KNOWLEDGE_BASE,
    ErrorCode.INGESTION_NOT_FOUND: ErrorCategory.KNOWLEDGE_BASE,
    ErrorCode.DIGEST_UNAVAILABLE: ErrorCategory.INTERNAL,
    ErrorCode.LEARNING_PROFILE_UNAVAILABLE: ErrorCategory.INTERNAL,
    ErrorCode.MODEL_TIMEOUT: ErrorCategory.MODEL,
    ErrorCode.MODEL_RATE_LIMITED: ErrorCategory.MODEL,
    ErrorCode.MODEL_ERROR: ErrorCategory.MODEL,
    ErrorCode.MODEL_CONTEXT_EXCEEDED: ErrorCategory.RESOURCE_LIMIT,
    ErrorCode.PERSISTENCE_ERROR: ErrorCategory.PERSISTENCE,
    ErrorCode.CONFIGURATION_ERROR: ErrorCategory.INTERNAL,
    ErrorCode.INTERNAL_ERROR: ErrorCategory.INTERNAL,
}

_ERROR_HTTP_STATUS: dict[ErrorCode, int] = {
    ErrorCode.INVALID_REQUEST: 400,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.RUN_IN_PROGRESS: 409,
    ErrorCode.URL_NOT_ALLOWED: 400,
    ErrorCode.SSRF_TARGET_BLOCKED: 400,
    ErrorCode.UNSUPPORTED_CONTENT_TYPE: 415,
    ErrorCode.FETCH_TIMEOUT: 504,
    ErrorCode.FETCH_HTTP_ERROR: 502,
    ErrorCode.CONTENT_EXTRACTION_FAILED: 422,
    ErrorCode.DOCUMENT_TOO_LARGE: 413,
    ErrorCode.CONTEXT_BUDGET_EXCEEDED: 413,
    ErrorCode.TOOL_LIMIT_EXCEEDED: 500,
    ErrorCode.MODEL_ROUND_LIMIT_EXCEEDED: 500,
    ErrorCode.RUN_TIMEOUT: 504,
    ErrorCode.RUN_INTERRUPTED: 500,
    ErrorCode.KNOWLEDGE_BASE_UNAVAILABLE: 502,
    ErrorCode.KNOWLEDGE_BASE_TIMEOUT: 504,
    ErrorCode.KNOWLEDGE_BASE_REJECTED: 400,
    ErrorCode.KNOWLEDGE_BASE_BUSY: 429,
    ErrorCode.INGESTION_NOT_FOUND: 404,
    ErrorCode.DIGEST_UNAVAILABLE: 503,
    ErrorCode.LEARNING_PROFILE_UNAVAILABLE: 404,
    ErrorCode.MODEL_TIMEOUT: 504,
    ErrorCode.MODEL_RATE_LIMITED: 429,
    ErrorCode.MODEL_ERROR: 502,
    ErrorCode.MODEL_CONTEXT_EXCEEDED: 413,
    ErrorCode.PERSISTENCE_ERROR: 500,
    ErrorCode.CONFIGURATION_ERROR: 500,
    ErrorCode.INTERNAL_ERROR: 500,
}


class AppError(Exception):
    """应用内统一异常基类。

    ``message`` 必须是可直接展示给用户的安全文案，不含堆栈、环境变量或
    其他内部细节；内部排障信息放在 ``detail``，只写日志。
    """

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        detail: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail

    @property
    def category(self) -> ErrorCategory:
        return _ERROR_CATEGORY[self.code]

    @property
    def http_status(self) -> int:
        return _ERROR_HTTP_STATUS[self.code]

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"


def http_status_for(code: ErrorCode) -> int:
    """返回错误码对应的 HTTP 状态码。"""

    return _ERROR_HTTP_STATUS[code]


class ConfigurationError(AppError):
    """配置缺失或非法，应用应停止启动。"""

    def __init__(self, message: str, *, detail: str | None = None) -> None:
        super().__init__(ErrorCode.CONFIGURATION_ERROR, message, detail=detail)


class UrlPolicyError(AppError):
    """URL 或目标地址未通过安全策略。"""


class DocumentToolError(AppError):
    """文档工具执行失败。"""


class ModelError(AppError):
    """模型调用失败。"""


class PersistenceError(AppError):
    """持久化失败。"""

    def __init__(self, message: str, *, detail: str | None = None) -> None:
        super().__init__(ErrorCode.PERSISTENCE_ERROR, message, detail=detail)
