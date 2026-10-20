"""知识库冒烟脚本的回答校验。"""

from __future__ import annotations

from scripts.smoke_knowledge import evaluate_response


def payload(
    *, content: str = "回答 [S1]", providers: tuple[str, ...] = ("knowledge_base",)
) -> dict[str, object]:
    return {
        "message": {"content": content},
        "sources": [
            {"source_id": f"S{index}", "provider": provider}
            for index, provider in enumerate(providers, start=1)
        ],
        "run_id": "run-1",
    }


def test_accepts_knowledge_answer_with_citation() -> None:
    result = evaluate_response(201, payload())

    assert result["passed"] is True
    assert result["citation_present"] is True
    assert result["providers"] == ["knowledge_base"]


def test_rejects_missing_citation() -> None:
    result = evaluate_response(201, payload(content="没有引用"))

    assert result["passed"] is False


def test_rejects_non_knowledge_source() -> None:
    result = evaluate_response(201, payload(providers=("fetch",)))

    assert result["passed"] is False


def test_rejects_non_201_status() -> None:
    assert evaluate_response(500, payload())["passed"] is False
