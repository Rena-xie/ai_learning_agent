"""评估回归判定：纯函数，便于离线自测。

判定两类回归：
1. 逐案例：基线通过、本次失败；
2. 整体：本次通过率低于基线通过率（覆盖新增失败案例等情形）。
"""

from __future__ import annotations

from typing import Any


def find_regressions(
    outcomes: list[dict[str, Any]], baseline: dict[str, Any]
) -> list[str]:
    """返回回归描述列表；为空表示无回归。"""

    previous = {
        item["case_id"]: item.get("passed")
        for item in baseline.get("cases", [])
        if "case_id" in item
    }
    regressions = [
        str(item["case_id"])
        for item in outcomes
        if previous.get(item["case_id"]) is True and not item.get("passed")
    ]

    baseline_passed = baseline.get("passed")
    baseline_total = baseline.get("total")
    current_passed = sum(1 for item in outcomes if item.get("passed"))
    current_total = len(outcomes)
    if (
        isinstance(baseline_passed, int)
        and isinstance(baseline_total, int)
        and baseline_total > 0
        and current_total > 0
        and current_passed / current_total < baseline_passed / baseline_total
    ):
        regressions.append(
            "整体通过率下降（"
            f"{baseline_passed}/{baseline_total} → {current_passed}/{current_total}）"
        )
    return regressions
