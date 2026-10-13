"""测试用 Web 客户端：在 ASGI 传输上运行应用（含 lifespan）。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI


@asynccontextmanager
async def running_client(
    app: FastAPI, *, timeout_seconds: float = 30.0
) -> AsyncIterator[httpx.AsyncClient]:
    """启动应用 lifespan 并提供 httpx 客户端。"""

    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
            timeout=httpx.Timeout(timeout_seconds),
        ) as client,
    ):
        yield client
