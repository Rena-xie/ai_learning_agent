"""外部链接协议和格式校验。"""

from __future__ import annotations

import pytest

from learning_agent.domain.urls import normalize_external_url


@pytest.mark.parametrize(
    "value",
    [
        "javascript:alert(document.domain)",
        "data:text/html,<script>alert(1)</script>",
        "file:///etc/passwd",
        "vbscript:msgbox(1)",
        "https://user:password@example.com/path",
        "https://example.com/a b",
        "https://",
    ],
)
def test_rejects_unsafe_external_urls(value: str) -> None:
    with pytest.raises(ValueError):
        normalize_external_url(value)


def test_normalizes_external_http_url() -> None:
    assert (
        normalize_external_url(" HTTPS://Example.COM:443/path?q=1#section ")
        == "https://example.com:443/path?q=1"
    )
