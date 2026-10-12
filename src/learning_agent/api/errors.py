"""统一的 HTTP 错误映射：不泄露堆栈、环境变量或其他对话数据。"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from learning_agent.domain.errors import AppError, ErrorCategory, ErrorCode

logger = logging.getLogger(__name__)


def error_payload(code: str, category: str, message: str) -> dict[str, object]:
    return {"error": {"code": code, "category": category, "message": message}}


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _handle_app_error(_request: Request, exc: AppError) -> JSONResponse:
        logger.warning(
            "api_error",
            extra={
                "extra_fields": {
                    "error_code": str(exc.code),
                    "error_category": str(exc.category),
                }
            },
        )
        return JSONResponse(
            status_code=exc.http_status,
            content=error_payload(str(exc.code), str(exc.category), exc.message),
        )

    @app.exception_handler(RequestValidationError)
    async def _handle_validation_error(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        logger.info(
            "request_validation_failed",
            extra={"extra_fields": {"error_count": len(exc.errors())}},
        )
        return JSONResponse(
            status_code=422,
            content=error_payload(
                str(ErrorCode.INVALID_REQUEST),
                str(ErrorCategory.INVALID_REQUEST),
                "请求参数不合法，请检查后重试",
            ),
        )

    @app.exception_handler(Exception)
    async def _handle_unexpected_error(
        _request: Request, exc: Exception
    ) -> JSONResponse:
        logger.exception("unhandled_exception", exc_info=exc)
        return JSONResponse(
            status_code=500,
            content=error_payload(
                str(ErrorCode.INTERNAL_ERROR),
                str(ErrorCategory.INTERNAL),
                "服务内部错误，请稍后重试",
            ),
        )
