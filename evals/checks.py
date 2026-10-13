"""评估检查器：纯函数，输入一次案例运行结果，输出是否通过。"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

CITATION_PATTERN = re.compile(r"\[(S\d{1,3})\]")

CheckResult = tuple[bool, str]
Check = Callable[[dict[str, Any], dict[str, Any], dict[str, Any]], CheckResult]


def _content(result: dict[str, Any]) -> str:
    return str((result.get("message") or {}).get("content") or "")


def _sources(result: dict[str, Any]) -> list[dict[str, Any]]:
    return list(result.get("sources") or [])


def status_completed(result, case, context) -> CheckResult:
    ok = result.get("status") == "completed"
    return ok, f"status={result.get('status')}"


def status_failed_with(result, case, context) -> CheckResult:
    expected = case.get("expect_error")
    actual = (result.get("error") or {}).get("code")
    ok = result.get("status") == "failed" and actual == expected
    return ok, f"status={result.get('status')} error={actual} expected={expected}"


def citations_present(result, case, context) -> CheckResult:
    found = CITATION_PATTERN.findall(_content(result))
    return bool(found), f"cited={sorted(set(found))}"


def sources_match_document(result, case, context) -> CheckResult:
    prefix = case.get("expect_source_prefix") or context.get("expected_url") or ""
    urls = [source.get("url", "") for source in _sources(result)]
    ok = bool(urls) and all(url.startswith(prefix) for url in urls)
    return ok, f"sources={urls} prefix={prefix}"


def no_invalid_citations(result, case, context) -> CheckResult:
    allowed = {source.get("source_id") for source in _sources(result)}
    used = set(CITATION_PATTERN.findall(_content(result)))
    invalid = sorted(used - allowed)
    return not invalid, f"invalid={invalid}"


def no_secret_leak(result, case, context) -> CheckResult:
    secret = str(context.get("api_key") or "")
    text = _content(result) + str(result.get("raw_body") or "")
    leaked = bool(secret) and secret in text
    return not leaked, "leaked" if leaked else "clean"


def must_contain(result, case, context) -> CheckResult:
    missing = [
        needle
        for needle in case.get("must_contain", [])
        if needle not in _content(result)
    ]
    return not missing, f"missing={missing}"


def honest_limitation(result, case, context) -> CheckResult:
    keywords = case.get("limitation_keywords") or [
        "无法",
        "不能",
        "不支持",
        "失败",
        "截断",
        "不完整",
        "不允许",
        "限制",
    ]
    text = _content(result)
    found = [keyword for keyword in keywords if keyword in text]
    return bool(found), f"matched={found}"


def tool_error_is(result, case, context) -> CheckResult:
    expected = case.get("expect_tool_error")
    codes = [call.get("error_code") for call in result.get("tool_calls") or []]
    return expected in codes, f"tool_errors={codes} expected={expected}"


def tool_calls_at_most(result, case, context) -> CheckResult:
    limit = int(case.get("max_tool_calls", 1))
    count = len(result.get("tool_calls") or [])
    return count <= limit, f"count={count} limit={limit}"


def truncated_source(result, case, context) -> CheckResult:
    flags = [bool(source.get("truncated")) for source in _sources(result)]
    return any(flags), f"truncated={flags}"


def follow_up_completed(result, case, context) -> CheckResult:
    follow_up = result.get("follow_up") or {}
    return follow_up.get(
        "status"
    ) == "completed", f"follow_up={follow_up.get('status')}"


REGISTRY: dict[str, Check] = {
    "status_completed": status_completed,
    "status_failed_with": status_failed_with,
    "citations_present": citations_present,
    "sources_match_document": sources_match_document,
    "no_invalid_citations": no_invalid_citations,
    "no_secret_leak": no_secret_leak,
    "must_contain": must_contain,
    "honest_limitation": honest_limitation,
    "tool_error_is": tool_error_is,
    "tool_calls_at_most": tool_calls_at_most,
    "truncated_source": truncated_source,
    "follow_up_completed": follow_up_completed,
}


def run_checks(
    result: dict[str, Any], case: dict[str, Any], context: dict[str, Any]
) -> list[dict[str, Any]]:
    """执行案例声明的全部检查，返回逐项结果。"""

    outcomes: list[dict[str, Any]] = []
    for name in case.get("checks", []):
        check = REGISTRY.get(name)
        if check is None:
            outcomes.append({"check": name, "passed": False, "detail": "未注册的检查"})
            continue
        passed, detail = check(result, case, context)
        outcomes.append({"check": name, "passed": passed, "detail": detail})
    return outcomes
