"""错误分类体系单元测试。"""

from __future__ import annotations

import pytest

from learning_agent.domain.errors import (
    AppError,
    ConfigurationError,
    ErrorCategory,
    ErrorCode,
    UrlPolicyError,
)


def test_every_code_has_category_and_http_status() -> None:
    for code in ErrorCode:
        error = AppError(code, "安全文案")
        assert isinstance(error.category, ErrorCategory)
        assert 400 <= error.http_status <= 599


@pytest.mark.parametrize(
    ("code", "category", "status"),
    [
        (ErrorCode.URL_NOT_ALLOWED, ErrorCategory.URL_POLICY, 400),
        (ErrorCode.SSRF_TARGET_BLOCKED, ErrorCategory.URL_POLICY, 400),
        (ErrorCode.FETCH_TIMEOUT, ErrorCategory.DOCUMENT, 504),
        (ErrorCode.MODEL_RATE_LIMITED, ErrorCategory.MODEL, 429),
        (ErrorCode.TOOL_LIMIT_EXCEEDED, ErrorCategory.RESOURCE_LIMIT, 500),
        (ErrorCode.RUN_IN_PROGRESS, ErrorCategory.INVALID_REQUEST, 409),
        (ErrorCode.PERSISTENCE_ERROR, ErrorCategory.PERSISTENCE, 500),
    ],
)
def test_representative_mappings(
    code: ErrorCode, category: ErrorCategory, status: int
) -> None:
    error = AppError(code, "安全文案")
    assert error.category is category
    assert error.http_status == status


def test_detail_is_not_part_of_user_message() -> None:
    error = UrlPolicyError(
        ErrorCode.SSRF_TARGET_BLOCKED, "目标地址不被允许", detail="blocked_ip=10.0.0.5"
    )
    assert "10.0.0.5" not in error.message
    assert "10.0.0.5" in (error.detail or "")


def test_configuration_error_uses_internal_category() -> None:
    error = ConfigurationError("模型配置缺失")
    assert error.code is ErrorCode.CONFIGURATION_ERROR
    assert error.category is ErrorCategory.INTERNAL
