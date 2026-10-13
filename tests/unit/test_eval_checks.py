"""评估检查器单元测试：用确定性夹具验证能否检出问题。"""

from __future__ import annotations

from evals.checks import REGISTRY, run_checks


def make_result(**overrides) -> dict:
    result = {
        "status": "completed",
        "message": {"content": "结论如下 [S1]。"},
        "sources": [{"source_id": "S1", "url": "https://docs.example.com/a"}],
        "tool_calls": [],
        "error": None,
    }
    result.update(overrides)
    return result


def test_passing_case_passes_all_checks() -> None:
    case = {
        "checks": [
            "status_completed",
            "citations_present",
            "sources_match_document",
            "no_invalid_citations",
            "no_secret_leak",
        ]
    }
    context = {"api_key": "sk-secret", "expected_url": "https://docs.example.com/a"}
    outcomes = run_checks(make_result(), case, context)
    assert all(item["passed"] for item in outcomes)


def test_detects_invalid_citation() -> None:
    result = make_result(message={"content": "结论 [S1]，另有 [S9]。"})
    case = {"checks": ["no_invalid_citations"]}
    outcomes = run_checks(result, case, {})
    assert outcomes[0]["passed"] is False
    assert "S9" in outcomes[0]["detail"]


def test_detects_secret_leak() -> None:
    result = make_result(message={"content": "这是密钥 sk-secret 的内容"})
    case = {"checks": ["no_secret_leak"]}
    outcomes = run_checks(result, case, {"api_key": "sk-secret"})
    assert outcomes[0]["passed"] is False


def test_detects_伪装成功_failure() -> None:
    """失败案例却返回 completed 时必须被检出。"""

    result = make_result(status="completed", error=None)
    case = {"checks": ["status_failed_with"], "expect_error": "model_error"}
    outcomes = run_checks(result, case, {})
    assert outcomes[0]["passed"] is False


def test_detects_missing_citation_and_missing_source() -> None:
    result = make_result(message={"content": "没有引用的回答"}, sources=[])
    case = {"checks": ["citations_present", "sources_match_document"]}
    outcomes = run_checks(result, case, {"expected_url": "https://docs.example.com/a"})
    assert all(item["passed"] is False for item in outcomes)


def test_detects_undisclosed_limitation() -> None:
    result = make_result(message={"content": "文档内容非常完整，无需补充。"})
    case = {"checks": ["honest_limitation"]}
    outcomes = run_checks(result, case, {})
    assert outcomes[0]["passed"] is False


def test_all_declared_checks_are_registered() -> None:
    from evals.run_eval import load_cases

    for case in load_cases():
        unknown = [name for name in case["checks"] if name not in REGISTRY]
        assert not unknown, f"{case['id']} 使用了未注册检查：{unknown}"
