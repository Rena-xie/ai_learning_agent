"""飞书自定义机器人 Webhook 客户端。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
from dataclasses import dataclass

import httpx


class FeishuError(RuntimeError):
    """飞书 Webhook 调用失败。"""


@dataclass(frozen=True)
class FeishuResult:
    status: str
    attempts: int
    error: str | None = None


class FeishuNotifier:
    """发送纯文本 Digest；有 secret 时附加飞书签名。"""

    def __init__(
        self,
        *,
        webhook_url: str,
        secret: str = "",
        timeout_s: float = 15.0,
        max_retries: int = 2,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._webhook_url = webhook_url.strip()
        self._secret = secret.strip()
        self._max_retries = max(0, max_retries)
        self._client = httpx.AsyncClient(
            timeout=timeout_s,
            transport=transport,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def send(self, text: str) -> FeishuResult:
        if not self._webhook_url:
            return FeishuResult(status="disabled", attempts=0)
        payload: dict[str, object] = {
            "msg_type": "text",
            "content": {"text": text},
        }
        if self._secret:
            timestamp = str(int(time.time()))
            payload["timestamp"] = timestamp
            payload["sign"] = _signature(timestamp, self._secret)

        last_error = "飞书推送失败"
        for attempt in range(self._max_retries + 1):
            try:
                response = await self._client.post(self._webhook_url, json=payload)
                if 200 <= response.status_code < 300:
                    try:
                        data = response.json()
                    except ValueError:
                        data = None
                    if not isinstance(data, dict) or data.get("code", 0) == 0:
                        return FeishuResult(status="delivered", attempts=attempt + 1)
                    last_error = f"飞书返回业务错误: {data}"
                else:
                    last_error = f"HTTP {response.status_code}: {response.text[:200]}"
            except httpx.HTTPError as exc:
                last_error = f"{type(exc).__name__}: {exc}"
            if attempt < self._max_retries:
                import asyncio

                await asyncio.sleep(0.2 * (2**attempt))
        return FeishuResult(
            status="failed",
            attempts=self._max_retries + 1,
            error=last_error,
        )


def _signature(timestamp: str, secret: str) -> str:
    string_to_sign = f"{timestamp}\n{secret}".encode()
    digest = hmac.new(secret.encode(), string_to_sign, hashlib.sha256).digest()
    return base64.b64encode(digest).decode()
