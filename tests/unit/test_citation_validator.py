"""引用校验单元测试。"""

from __future__ import annotations

from learning_agent.agent.citation_validator import validate_citations


def test_keeps_valid_markers_in_order() -> None:
    content = "结论一 [S2]，结论二 [S1]，重复引用 [S2]。"
    result = validate_citations(content, allowed_source_ids={"S1", "S2"})
    assert result.used_source_ids == ("S2", "S1")
    assert result.invalid_source_ids == ()
    assert "[S1]" in result.content and "[S2]" in result.content


def test_removes_invalid_markers_and_records_them() -> None:
    content = "真实结论 [S1]，编造来源 [S9]。"
    result = validate_citations(content, allowed_source_ids={"S1"})
    assert result.used_source_ids == ("S1",)
    assert result.invalid_source_ids == ("S9",)
    assert "[S9]" not in result.content
    assert "编造来源" in result.content


def test_strips_markdown_links_but_keeps_text() -> None:
    content = "参考 [官方文档](https://evil.example.com/a) 的说明 [S1]。"
    result = validate_citations(content, allowed_source_ids={"S1"})
    assert "https://evil.example.com/a" not in result.content
    assert "官方文档" in result.content
    assert result.stripped_links == ("https://evil.example.com/a",)


def test_answer_without_markers_is_accepted_but_has_no_sources() -> None:
    result = validate_citations("没有引用的回答。", allowed_source_ids={"S1"})
    assert result.used_source_ids == ()
    assert result.invalid_source_ids == ()
