"""HTML 正文提取与章节解析单元测试（离线）。"""

from __future__ import annotations

import pytest

from learning_agent.domain.errors import AppError, ErrorCode
from learning_agent.tools.document.extractor import extract_document
from learning_agent.tools.document.normalizer import normalize_text

SAMPLE_HTML = """<!doctype html>
<html>
  <head><title>示例框架 · 快速开始</title></head>
  <body>
    <nav>首页 | 文档 | 博客</nav>
    <article>
      <h1>快速开始</h1>
      <p>示例框架是一个用于构建 Web 服务的现代 Python 框架，</p>
      <p>基于 Starlette 与 Pydantic。</p>
      <h2>依赖注入</h2>
      <p>框架通过 Depends 提供依赖注入能力。</p>
      <h2>重要限制</h2>
      <p>默认只为单实例本地部署设计。</p>
    </article>
    <footer>版权所有</footer>
  </body>
</html>
"""


def test_extracts_text_title_and_sections() -> None:
    result = extract_document(SAMPLE_HTML, "https://docs.example.com/guide")
    text = normalize_text(result.text)
    assert "示例框架是一个用于构建 Web 服务的现代 Python 框架" in text
    assert "依赖注入" in text
    assert "重要限制" in text
    # trafilatura 优先采用正文主标题（h1），这里只断言核心标题存在
    assert result.title and "快速开始" in result.title
    titles = [section.title for section in result.sections]
    assert "快速开始" in titles
    assert "依赖注入" in titles
    assert any(section.text.strip() for section in result.sections)


def test_title_falls_back_to_h1_when_missing() -> None:
    html = (
        "<html><body><article><h1>只有 H1 标题</h1>"
        "<p>正文段落。</p></article></body></html>"
    )
    result = extract_document(html, "https://docs.example.com/x")
    assert result.title == "只有 H1 标题"


@pytest.mark.parametrize(
    "html",
    [
        "<html><body><script>var a = 1;</script></body></html>",
        "<html><body><nav>首页</nav></body></html>",
        "<html><body></body></html>",
    ],
)
def test_raises_when_no_meaningful_text(html: str) -> None:
    with pytest.raises(AppError) as excinfo:
        extract_document(html, "https://docs.example.com/empty")
    assert excinfo.value.code is ErrorCode.CONTENT_EXTRACTION_FAILED
