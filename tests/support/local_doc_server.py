"""本地可控 HTTP 文档服务：离线验证获取、重定向、超时与错误路径。

安全策略仍按白名单域名校验，测试通过“传输层改写”把请求指向本地端口，
不使用真实网络，也不放宽 URL/SSRF 校验逻辑。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx

#: 测试用的白名单域名（解析结果由测试桩提供，连接被改写到本地端口）
TEST_HOST = "docs.example.com"
PUBLIC_IP = "93.184.216.34"


def stub_resolver(*ips: str):
    """返回固定公网解析结果的解析器。"""

    def _resolve(host: str, port: int) -> list[str]:
        return list(ips or (PUBLIC_IP,))

    return _resolve


@dataclass
class Route:
    status: int = 200
    content_type: str = "text/html; charset=utf-8"
    body: bytes = b""
    headers: dict[str, str] = field(default_factory=dict)
    delay_seconds: float = 0.0
    declared_length: int | None = None


class LocalDocServer:
    """线程化本地 HTTP 服务，按路径返回预设响应。"""

    def __init__(self) -> None:
        self.routes: dict[str, Route] = {}
        handler = self._build_handler()
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.port: int = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    # ---------- 生命周期 ----------

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    # ---------- 路由配置 ----------

    def add_html(self, path: str, html: str, *, status: int = 200) -> None:
        self.routes[path] = Route(status=status, body=html.encode("utf-8"))

    def add_route(self, path: str, route: Route) -> None:
        self.routes[path] = route

    def add_redirect(self, path: str, location: str, *, status: int = 302) -> None:
        self.routes[path] = Route(
            status=status, headers={"Location": location}, body=b""
        )

    # ---------- URL 与传输层 ----------

    def url(self, path: str) -> str:
        return f"http://{TEST_HOST}:{self.port}{path}"

    def transport(self) -> httpx.AsyncBaseTransport:
        return _RewriteTransport(port=self.port)

    # ---------- 内部实现 ----------

    def _build_handler(self) -> type[BaseHTTPRequestHandler]:
        routes = self.routes

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def handle_one_request(self) -> None:
                try:
                    super().handle_one_request()
                except (ConnectionAbortedError, BrokenPipeError, ConnectionResetError):
                    # 客户端超时断开属于预期路径（用于超时测试）
                    self.close_connection = True

            def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 约定
                route = routes.get(self.path)
                if route is None:
                    self.send_error(404, "Not Found")
                    return
                if route.delay_seconds:
                    time.sleep(route.delay_seconds)
                self.send_response(route.status)
                self.send_header("Content-Type", route.content_type)
                declared = (
                    route.declared_length
                    if route.declared_length is not None
                    else len(route.body)
                )
                self.send_header("Content-Length", str(declared))
                for key, value in route.headers.items():
                    self.send_header(key, value)
                self.end_headers()
                if route.body:
                    try:
                        self.wfile.write(route.body)
                    except (ConnectionAbortedError, BrokenPipeError):
                        # 客户端超时断开是预期路径（用于超时测试）
                        return

            def log_message(self, *_args: object) -> None:
                return

        return Handler


class _RewriteTransport(httpx.AsyncBaseTransport):
    """把白名单域名改写为本地测试服务器地址，同时保留原始 Host 头。"""

    def __init__(self, *, port: int) -> None:
        self._port = port
        self._inner = httpx.AsyncHTTPTransport()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        original_host = request.url.netloc.decode("ascii")
        rewritten_url = request.url.copy_with(host="127.0.0.1", port=self._port)
        headers = httpx.Headers(request.headers)
        headers["host"] = original_host
        rewritten = httpx.Request(
            method=request.method,
            url=rewritten_url,
            headers=headers,
            content=request.content,
            extensions=request.extensions,
        )
        return await self._inner.handle_async_request(rewritten)

    async def aclose(self) -> None:
        await self._inner.aclose()
