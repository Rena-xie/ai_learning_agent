"""文档获取与解析集成测试：通过本地可控 HTTP 服务验证各条限制路径。"""

from __future__ import annotations

import pytest
from tests.support.local_doc_server import (
    TEST_HOST,
    LocalDocServer,
    Route,
    stub_resolver,
)

from learning_agent.domain.contracts import ToolContext
from learning_agent.domain.errors import AppError, ErrorCode
from learning_agent.tools.document.extractor import extract_document
from learning_agent.tools.document.fetcher import DocumentFetcher
from learning_agent.tools.document.normalizer import normalize_text
from learning_agent.tools.document.tool import FetchDocumentTool
from learning_agent.tools.document.url_policy import UrlPolicy

SAMPLE_HTML = (
    "<html><head><title>示例指南</title></head><body><article>"
    "<h1>核心概念</h1><p>这是文档的第一段正文内容，用于验证提取链路。</p>"
    "</article></body></html>"
)


def make_fetcher(server: LocalDocServer, **overrides: object) -> DocumentFetcher:
    options: dict[str, object] = {
        "timeout_seconds": 5.0,
        "max_bytes": 200_000,
        "max_redirects": 3,
        # 本地可控服务器位于 127.0.0.1；对端地址校验由单独用例验证
        "verify_peer_address": False,
    }
    options.update(overrides)
    policy = UrlPolicy(
        allowed_hosts=frozenset({TEST_HOST}),
        allowed_ports=frozenset({80, 443, server.port}),
        resolver=stub_resolver(),
    )
    return DocumentFetcher(policy, transport=server.transport(), **options)


async def expect_error(code: ErrorCode, fetcher: DocumentFetcher, url: str) -> AppError:
    with pytest.raises(AppError) as excinfo:
        await fetcher.fetch(url)
    assert excinfo.value.code is code
    return excinfo.value


async def test_fetch_and_extract_contract(local_server: LocalDocServer) -> None:
    local_server.add_html("/guide", SAMPLE_HTML)
    result = await make_fetcher(local_server).fetch(local_server.url("/guide"))
    assert result.canonical_url == local_server.url("/guide")
    assert result.content_type.startswith("text/html")
    assert result.byte_size == len(SAMPLE_HTML.encode("utf-8"))
    extracted = extract_document(result.html, result.canonical_url)
    # trafilatura 优先采用正文主标题（h1），其次是 <title>
    assert extracted.title and "核心概念" in extracted.title
    assert "这是文档的第一段正文内容" in normalize_text(extracted.text)


async def test_follows_redirect_within_allowlist(local_server: LocalDocServer) -> None:
    local_server.add_html("/final", SAMPLE_HTML)
    local_server.add_redirect("/start", local_server.url("/final"))
    result = await make_fetcher(local_server).fetch(local_server.url("/start"))
    assert result.canonical_url == local_server.url("/final")


async def test_redirect_to_disallowed_host_is_rejected(
    local_server: LocalDocServer,
) -> None:
    local_server.add_redirect("/jump", "http://evil.example.net/other")
    await expect_error(
        ErrorCode.URL_NOT_ALLOWED, make_fetcher(local_server), local_server.url("/jump")
    )


async def test_redirect_to_private_target_is_rejected(
    local_server: LocalDocServer,
) -> None:
    local_server.add_redirect("/private", "http://docs.example.com/next")
    policy = UrlPolicy(
        allowed_hosts=frozenset({TEST_HOST}),
        allowed_ports=frozenset({80, 443, local_server.port}),
        resolver=stub_resolver("10.0.0.7"),
    )
    fetcher = DocumentFetcher(
        policy,
        timeout_seconds=5.0,
        max_bytes=10_000,
        max_redirects=3,
        transport=local_server.transport(),
        verify_peer_address=False,
    )
    await expect_error(
        ErrorCode.SSRF_TARGET_BLOCKED, fetcher, local_server.url("/private")
    )


async def test_redirect_loop_exceeding_limit_is_rejected(
    local_server: LocalDocServer,
) -> None:
    local_server.add_redirect("/loop", local_server.url("/loop"))
    await expect_error(
        ErrorCode.FETCH_HTTP_ERROR,
        make_fetcher(local_server, max_redirects=2),
        local_server.url("/loop"),
    )


async def test_non_html_content_is_unsupported(local_server: LocalDocServer) -> None:
    local_server.add_route(
        "/manual.pdf", Route(content_type="application/pdf", body=b"%PDF-1.4 pdf")
    )
    await expect_error(
        ErrorCode.UNSUPPORTED_CONTENT_TYPE,
        make_fetcher(local_server),
        local_server.url("/manual.pdf"),
    )


async def test_oversized_document_is_rejected(local_server: LocalDocServer) -> None:
    local_server.add_html("/big", "<html><body>" + "x" * 5_000 + "</body></html>")
    await expect_error(
        ErrorCode.DOCUMENT_TOO_LARGE,
        make_fetcher(local_server, max_bytes=1_000),
        local_server.url("/big"),
    )


async def test_timeout_is_reported(local_server: LocalDocServer) -> None:
    local_server.add_route(
        "/slow", Route(body=SAMPLE_HTML.encode("utf-8"), delay_seconds=2.0)
    )
    await expect_error(
        ErrorCode.FETCH_TIMEOUT,
        make_fetcher(local_server, timeout_seconds=0.3),
        local_server.url("/slow"),
    )


async def test_http_error_status_is_reported(local_server: LocalDocServer) -> None:
    local_server.add_route("/missing", Route(status=404, body=b"not found"))
    await expect_error(
        ErrorCode.FETCH_HTTP_ERROR,
        make_fetcher(local_server),
        local_server.url("/missing"),
    )


async def test_peer_address_check_blocks_local_endpoint(
    local_server: LocalDocServer,
) -> None:
    """对端地址校验：真实连接落在非公网地址时拒绝（DNS rebinding 缓解）。"""

    local_server.add_html("/guide", SAMPLE_HTML)
    fetcher = make_fetcher(local_server, verify_peer_address=True)
    error = await expect_error(
        ErrorCode.SSRF_TARGET_BLOCKED, fetcher, local_server.url("/guide")
    )
    assert "公网" in error.message


async def test_declared_content_length_is_prechecked(
    local_server: LocalDocServer,
) -> None:
    """Content-Length 超过上限时在读取正文前拒绝。"""

    local_server.add_route(
        "/huge",
        Route(body=SAMPLE_HTML.encode("utf-8"), declared_length=5_000_000),
    )
    await expect_error(
        ErrorCode.DOCUMENT_TOO_LARGE,
        make_fetcher(local_server, max_bytes=1_000),
        local_server.url("/huge"),
    )


class _Allocator:
    def __init__(self) -> None:
        self.counter = 0

    def next_source_id(self) -> str:
        self.counter += 1
        return f"S{self.counter}"


async def test_document_tool_returns_typed_payload(
    local_server: LocalDocServer,
) -> None:
    """回归：工具结果必须返回字符串正文与布尔截断标记。"""

    local_server.add_html("/guide", SAMPLE_HTML)
    tool = FetchDocumentTool(make_fetcher(local_server), max_document_chars=60_000)
    context = ToolContext(
        run_id="run-1", conversation_id="conv-1", allocator=_Allocator()
    )
    outcome = await tool(tool.args_model(url=local_server.url("/guide")), context)

    assert outcome.ok is True
    assert outcome.source_id == "S1"
    assert outcome.content is not None
    assert isinstance(outcome.content.content_text, str)
    assert isinstance(outcome.content.truncated, bool)
    as_text = outcome.model_payload["content_text"]
    assert isinstance(as_text, str) and "第一段正文内容" in as_text
    assert outcome.model_payload["truncated"] is False
    assert outcome.result_summary["chars"] == len(outcome.content.content_text)
