"""文本规范化、内容哈希与截断单元测试。"""

from __future__ import annotations

from learning_agent.tools.document.normalizer import (
    compute_content_hash,
    normalize_text,
    summarize,
    truncate_text,
)


def test_normalize_collapses_spaces_and_control_chars() -> None:
    raw = "第一行   有空格\x07\n\n\n\n第二行\t制表符\u00a0不间断空格  \n"
    assert normalize_text(raw) == "第一行 有空格\n\n第二行 制表符 不间断空格"


def test_normalize_keeps_single_blank_line_between_paragraphs() -> None:
    assert normalize_text("a\n\n\nb") == "a\n\nb"


def test_normalize_empty_input() -> None:
    assert normalize_text("") == ""
    assert normalize_text("   \n  ") == ""


def test_content_hash_is_stable_and_sensitive() -> None:
    first = compute_content_hash("正文内容")
    assert first == compute_content_hash("正文内容")
    assert first != compute_content_hash("正文内容 ")
    assert len(first) == 64


def test_truncate_reports_flag() -> None:
    text, truncated = truncate_text("abcdef", 6)
    assert (text, truncated) == ("abcdef", False)
    text, truncated = truncate_text("abcdef", 3)
    assert (text, truncated) == ("abc", True)


def test_summarize_limits_length_without_newlines() -> None:
    summary = summarize("a" * 100 + "\n" + "b" * 100, limit=50)
    assert len(summary) <= 51
    assert "\n" not in summary
