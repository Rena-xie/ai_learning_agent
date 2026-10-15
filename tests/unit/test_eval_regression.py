"""评估回归判定单元测试（纯函数，可离线运行）。"""

from __future__ import annotations

from evals.regression import find_regressions


def make_outcome(case_id: str, passed: bool) -> dict:
    return {"case_id": case_id, "passed": passed}


def test_detects_case_regression() -> None:
    baseline = {
        "passed": 2,
        "total": 2,
        "cases": [
            {"case_id": "a", "passed": True},
            {"case_id": "b", "passed": True},
        ],
    }
    outcomes = [make_outcome("a", True), make_outcome("b", False)]
    regressions = find_regressions(outcomes, baseline)
    assert "b" in regressions


def test_detects_overall_pass_rate_drop() -> None:
    baseline = {
        "passed": 2,
        "total": 2,
        "cases": [
            {"case_id": "a", "passed": True},
            {"case_id": "b", "passed": True},
        ],
    }
    # 基线案例仍通过，但新增案例失败 → 整体通过率下降
    outcomes = [
        make_outcome("a", True),
        make_outcome("b", True),
        make_outcome("c", False),
    ]
    regressions = find_regressions(outcomes, baseline)
    assert any("整体通过率" in item for item in regressions)


def test_no_regression_when_result_matches_baseline() -> None:
    baseline = {
        "passed": 1,
        "total": 2,
        "cases": [
            {"case_id": "a", "passed": True},
            {"case_id": "b", "passed": False},
        ],
    }
    outcomes = [make_outcome("a", True), make_outcome("b", False)]
    assert find_regressions(outcomes, baseline) == []


def test_improving_pass_rate_is_not_regression() -> None:
    baseline = {
        "passed": 1,
        "total": 2,
        "cases": [
            {"case_id": "a", "passed": True},
            {"case_id": "b", "passed": False},
        ],
    }
    outcomes = [make_outcome("a", True), make_outcome("b", True)]
    assert find_regressions(outcomes, baseline) == []
