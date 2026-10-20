"""外部可点击 URL 的统一校验与规范化。"""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

_ALLOWED_SCHEMES = frozenset({"http", "https"})
_MAX_URL_LENGTH = 2048


def normalize_external_url(value: str) -> str:
    """返回安全的 HTTP(S) URL；非法输入抛出 ``ValueError``。"""

    text = value.strip()
    if not text or len(text) > _MAX_URL_LENGTH:
        raise ValueError("外部 URL 为空或过长")
    if any(ord(char) < 32 or char.isspace() for char in text):
        raise ValueError("外部 URL 包含空白或控制字符")

    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError as exc:
        raise ValueError("外部 URL 格式不合法") from exc

    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower().rstrip(".")
    if scheme not in _ALLOWED_SCHEMES or not host:
        raise ValueError("外部 URL 只允许 HTTP(S)")
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("外部 URL 端口不合法")
    if parts.username is not None or parts.password is not None:
        raise ValueError("外部 URL 不允许包含用户凭据")

    netloc = f"[{host}]" if ":" in host else host
    if port is not None:
        netloc = f"{netloc}:{port}"
    return urlunsplit((scheme, netloc, parts.path or "/", parts.query, ""))
