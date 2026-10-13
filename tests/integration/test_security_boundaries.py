"""密钥、日志与响应体安全边界核查。"""

from __future__ import annotations

import json
import logging

import pytest
from tests.conftest import build_test_settings
from tests.support.web_app import running_client

from learning_agent.config import Settings
from learning_agent.domain.errors import ConfigurationError
from learning_agent.main import create_app

DOC_URL = "https://liaoxuefeng.com/books/python/introduction/index.html"

_STANDARD_LOG_ATTRS = frozenset(vars(logging.makeLogRecord({})))


def record_text(record: logging.LogRecord) -> str:
    extras = {
        key: value
        for key, value in vars(record).items()
        if key not in _STANDARD_LOG_ATTRS
    }
    return (
        f"{record.getMessage()} {json.dumps(extras, ensure_ascii=False, default=str)}"
    )


def test_missing_model_configuration_refuses_to_start(tmp_path) -> None:
    settings = build_test_settings(
        model_api_key="",
        database_url=f"sqlite:///{(tmp_path / 'config.db').as_posix()}",
    )
    with pytest.raises(ConfigurationError):
        create_app(settings)


async def test_error_responses_do_not_leak_internals(tmp_path) -> None:
    app = create_app(
        build_test_settings(
            database_url=f"sqlite:///{(tmp_path / 'errors.db').as_posix()}"
        )
    )
    async with running_client(app) as client:
        not_found = await client.get("/api/v1/runs/unknown-run-id")
        invalid = await client.post(
            "/api/v1/conversations/unknown/messages", json={"content": ""}
        )

    for response in (not_found, invalid):
        body = response.text
        assert "Traceback" not in body
        assert "sk-" not in body
        assert "MODEL_API_KEY" not in body
        assert "DATABASE_URL" not in body


@pytest.mark.real_model
async def test_logs_do_not_leak_secret_or_message_content(tmp_path) -> None:
    """真实模型运行期间：日志不含 API Key，也不记录完整用户消息。"""

    settings = Settings().model_copy(
        update={"database_url": f"sqlite:///{(tmp_path / 'logs.db').as_posix()}"}
    )
    marker = "机密标记词QZX"
    app = create_app(settings)
    # create_app 会替换根日志 handler，因此在其之后再挂采集 handler
    collected: list[logging.LogRecord] = []

    class _Collector(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            collected.append(record)

    collector = _Collector()
    root_logger = logging.getLogger()
    root_logger.addHandler(collector)
    try:
        async with running_client(app, timeout_seconds=300) as client:
            conversation_id = (
                await client.post("/api/v1/conversations", json={})
            ).json()["id"]
            response = await client.post(
                f"/api/v1/conversations/{conversation_id}/messages",
                json={"content": f"{marker}：请阅读 {DOC_URL} 并说明主题。"},
            )
    finally:
        root_logger.removeHandler(collector)
    assert response.status_code == 201, response.text

    dumped = "\n".join(record_text(record) for record in collected)
    assert dumped, "应捕获到运行日志"
    assert settings.model_api_key not in dumped
    assert marker not in dumped
    assert "sk-" not in dumped
