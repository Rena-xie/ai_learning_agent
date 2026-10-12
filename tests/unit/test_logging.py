"""结构化日志与脱敏单元测试。"""

from __future__ import annotations

import io
import json
import logging

from learning_agent.observability.logging import (
    configure_logging,
    get_logger,
    redact_mapping,
    redact_url,
    run_id_var,
)


def test_redact_url_masks_sensitive_query_values() -> None:
    redacted = redact_url(
        "https://docs.example.com/a?token=abc123&lang=zh&access_token=xyz"
    )
    assert "abc123" not in redacted
    assert "xyz" not in redacted
    assert "lang=zh" in redacted


def test_redact_url_strips_credentials() -> None:
    redacted = redact_url("https://user:pw@docs.example.com/a")
    assert "pw" not in redacted
    assert "docs.example.com" in redacted


def test_redact_mapping_masks_sensitive_keys_recursively() -> None:
    payload = {
        "api_key": "sk-secret",
        "headers": {"Authorization": "Bearer secret-token"},
        "items": [{"token": "t-1"}, {"path": "/a"}],
    }
    redacted = redact_mapping(payload)
    assert redacted["api_key"] == "***"
    assert redacted["headers"]["Authorization"] == "***"
    assert redacted["items"][0]["token"] == "***"
    assert redacted["items"][1]["path"] == "/a"


def test_json_formatter_emits_structured_event_with_run_id() -> None:
    stream = io.StringIO()
    configure_logging("INFO", stream=stream)
    token = run_id_var.set("run-123")
    try:
        get_logger("learning_agent.test").info(
            "tool_call_started",
            extra={"extra_fields": {"tool_name": "fetch_document", "api_key": "sk-x"}},
        )
    finally:
        run_id_var.reset(token)
        logging.getLogger().handlers.clear()

    record = json.loads(stream.getvalue().strip())
    assert record["event"] == "tool_call_started"
    assert record["run_id"] == "run-123"
    assert record["tool_name"] == "fetch_document"
    assert record["api_key"] == "***"
    assert "sk-x" not in stream.getvalue()


def test_configure_logging_replaces_handlers_and_falls_back_level() -> None:
    stream = io.StringIO()
    configure_logging("not-a-level", stream=stream)
    configure_logging("WARNING", stream=stream)
    assert len(logging.getLogger().handlers) == 1
    assert logging.getLogger().level == logging.WARNING
    logging.getLogger().handlers.clear()
