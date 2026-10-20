"""最小界面冒烟测试：静态资源可访问且不影响 API 路由。"""

from __future__ import annotations

from tests.conftest import build_test_settings
from tests.support.web_app import running_client

from learning_agent.main import create_app

REQUIRED_ELEMENT_IDS = (
    "conversation-list",
    "new-conversation",
    "message-list",
    "message-input",
    "send-button",
    "delete-conversation",
    "status-bar",
)

STUDY_ELEMENT_IDS = (
    "progress-stats",
    "digest-items",
    "note-list",
    "note-content",
    "generate-quiz",
    "generate-cards",
    "due-reviews",
    "preferences-form",
    "source-options",
    "source-health-list",
    "source-add-form",
    "toggle-profile",
    "profile-topics",
    "reset-profile",
    "study-status",
)


def make_settings(tmp_path):
    return build_test_settings(
        database_url=f"sqlite:///{(tmp_path / 'ui.db').as_posix()}"
    )


async def test_static_ui_is_served(tmp_path) -> None:
    app = create_app(make_settings(tmp_path))
    async with running_client(app) as client:
        index = await client.get("/")
        script = await client.get("/app.js")
        styles = await client.get("/styles.css")
        study = await client.get("/study.html")
        study_script = await client.get("/study.js")
        study_styles = await client.get("/study.css")
        safe_url_script = await client.get("/safe-url.js")

    assert index.status_code == 200
    assert index.headers["content-type"].startswith("text/html")
    for element_id in REQUIRED_ELEMENT_IDS:
        assert f'id="{element_id}"' in index.text
    assert script.status_code == 200
    assert styles.status_code == 200
    assert safe_url_script.status_code == 200
    assert 'href="/study.html"' in index.text
    assert 'src="/safe-url.js"' in index.text
    assert study.status_code == 200
    for element_id in STUDY_ELEMENT_IDS:
        assert f'id="{element_id}"' in study.text
    assert study_script.status_code == 200
    assert study_styles.status_code == 200
    assert 'src="/safe-url.js"' in study.text
    assert "script-src 'self'" in index.headers["content-security-policy"]
    assert index.headers["x-content-type-options"] == "nosniff"
    assert index.headers["referrer-policy"] == "no-referrer"


async def test_api_and_health_routes_still_work_with_static_mount(tmp_path) -> None:
    app = create_app(make_settings(tmp_path))
    async with running_client(app) as client:
        health = await client.get("/health/live")
        conversations = await client.post("/api/v1/conversations", json={})
        unknown = await client.get("/api/v1/runs/does-not-exist")

    assert health.status_code == 200
    assert conversations.status_code == 201
    assert unknown.status_code == 404
    assert unknown.json()["error"]["code"] == "not_found"
