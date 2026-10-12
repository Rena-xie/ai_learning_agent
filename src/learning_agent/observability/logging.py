"""结构化日志：JSON 输出、关联 ID 传播与敏感信息脱敏。

约定：
* 默认只记录标识符、阶段、状态、耗时与错误类别，不记录完整用户消息、
  完整文档正文或任何密钥。
* ``redact_url`` / ``redact_mapping`` 用于在写日志前清理敏感字段。
"""

from __future__ import annotations

import json
import logging
import sys
from collections.abc import Mapping
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_SENSITIVE_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "token",
        "access_token",
        "refresh_token",
        "secret",
        "password",
        "passwd",
        "signature",
        "sig",
        "credential",
        "authorization",
    }
)

_MASK = "***"

run_id_var: ContextVar[str | None] = ContextVar("run_id", default=None)
conversation_id_var: ContextVar[str | None] = ContextVar(
    "conversation_id", default=None
)


def _is_sensitive_key(key: str) -> bool:
    return key.strip().lower() in _SENSITIVE_KEYS


def redact_url(url: str) -> str:
    """脱敏 URL：移除用户凭据，并遮蔽敏感查询参数的值。"""

    try:
        parts = urlsplit(url)
    except ValueError:
        return "<invalid-url>"
    netloc = parts.netloc
    if "@" in netloc:
        host = parts.hostname or ""
        netloc = f"{_MASK}@{host}"
        if parts.port is not None:
            netloc = f"{netloc}:{parts.port}"
    query = urlencode(
        [
            (key, _MASK if _is_sensitive_key(key) else value)
            for key, value in parse_qsl(parts.query, keep_blank_values=True)
        ]
    )
    return urlunsplit((parts.scheme, netloc, parts.path, query, ""))


def redact_mapping(payload: Mapping[str, Any]) -> dict[str, Any]:
    """递归脱敏映射：敏感键置为 ``***``，嵌套结构保持形状。"""

    redacted: dict[str, Any] = {}
    for key, value in payload.items():
        name = str(key)
        if _is_sensitive_key(name):
            redacted[name] = _MASK
        elif isinstance(value, Mapping):
            redacted[name] = redact_mapping(value)
        elif isinstance(value, (list, tuple)):
            redacted[name] = [
                redact_mapping(item) if isinstance(item, Mapping) else item
                for item in value
            ]
        else:
            redacted[name] = value
    return redacted


class JsonFormatter(logging.Formatter):
    """把日志记录格式化为单行 JSON，便于后续稳定解析。"""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        run_id = getattr(record, "run_id", None) or run_id_var.get()
        if run_id:
            payload["run_id"] = run_id
        conversation_id = (
            getattr(record, "conversation_id", None) or conversation_id_var.get()
        )
        if conversation_id:
            payload["conversation_id"] = conversation_id
        extra_fields = getattr(record, "extra_fields", None)
        if isinstance(extra_fields, Mapping):
            payload.update(redact_mapping(extra_fields))
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(level: str = "INFO", *, stream: Any | None = None) -> None:
    """配置根日志器：JSON 输出到标准错误，可重复调用。"""

    resolved_level = getattr(logging, level.strip().upper(), None)
    if not isinstance(resolved_level, int):
        resolved_level = logging.INFO
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(resolved_level)


def get_logger(name: str) -> logging.Logger:
    """获取带命名空间的日志器。"""

    return logging.getLogger(name)
