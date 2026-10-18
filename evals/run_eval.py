"""Agent 评估运行器：加载固定案例 → 真实调用应用 → 规则判定 → 结果与基线比较。

用法::

    uv run python -m evals.run_eval                     # 全部案例（真实模型，产生费用）
    uv run python -m evals.run_eval --case url_only     # 单个案例
    uv run python -m evals.run_eval --baseline evals/results/baseline-20261013.json
    uv run python -m evals.run_eval --save-baseline evals/results/baseline-20261013.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from tests.support.local_doc_server import (
    TEST_HOST,
    LocalDocServer,
    Route,
    stub_resolver,
)

from evals.checks import run_checks
from evals.regression import find_regressions
from learning_agent.config import Settings
from learning_agent.main import create_app

EVALS_DIR = Path(__file__).resolve().parent
CASES_FILE = EVALS_DIR / "cases" / "core.json"
DEFAULT_OUTPUT = EVALS_DIR / "results" / "last-run.json"
TIMEOUT = httpx.Timeout(300.0)


def load_cases() -> list[dict[str, Any]]:
    return json.loads(CASES_FILE.read_text(encoding="utf-8"))


def build_settings(
    case: dict[str, Any], server: LocalDocServer | None, workdir: Path
) -> Settings:
    overrides: dict[str, object] = {
        "database_url": f"sqlite:///{(workdir / (case['id'] + '.db')).as_posix()}",
        # 评估默认固定采样温度，提升回归可复现性（案例可用 settings 覆盖）
        "model_temperature": 0.0,
    }
    if server is not None:
        overrides["allowed_document_hosts"] = (TEST_HOST,)
        overrides["allowed_document_ports"] = (80, 443, server.port)
    overrides.update(case.get("settings", {}))
    return Settings().model_copy(update=overrides)


async def run_case(case: dict[str, Any], workdir: Path) -> dict[str, Any]:
    document = case.get("document", {})
    server: LocalDocServer | None = None
    if document.get("kind") == "local":
        server = LocalDocServer()
        server.add_route(
            document["path"],
            Route(
                body=document["html"].encode("utf-8"),
                delay_seconds=float(document.get("delay_seconds", 0.0)),
            ),
        )
    try:
        settings = build_settings(case, server, workdir)
        app = create_app(
            settings,
            document_transport=server.transport() if server else None,
            document_resolver=stub_resolver() if server else None,
        )
        url = server.url(document["path"]) if server else document["url"]
        result: dict[str, Any] = {"case_id": case["id"], "document_url": url}
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
                timeout=TIMEOUT,
            ) as client,
        ):
            conversation_id = (
                await client.post("/api/v1/conversations", json={})
            ).json()["id"]
            response = await client.post(
                f"/api/v1/conversations/{conversation_id}/messages",
                json={
                    "content": case["user_message"].replace("{url}", url),
                },
            )
            payload = response.json()
            result.update(
                {
                    "http_status": response.status_code,
                    "status": payload.get("status"),
                    "message": payload.get("message"),
                    "sources": payload.get("sources") or [],
                    "error": payload.get("error"),
                }
            )
            run_id = payload.get("run_id")
            if run_id:
                run = (await client.get(f"/api/v1/runs/{run_id}")).json()
                result["tool_calls"] = run.get("tool_calls") or []
            if case.get("follow_up"):
                second = await client.post(
                    f"/api/v1/conversations/{conversation_id}/messages",
                    json={"content": case["follow_up"]},
                )
                second_payload = second.json()
                result["follow_up"] = {"status": second_payload.get("status")}
        result["context"] = {
            "api_key": settings.model_api_key,
            "expected_url": url,
        }
        return result
    finally:
        if server is not None:
            server.close()


async def run_all(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    outcomes: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="eval-") as workdir_name:
        workdir = Path(workdir_name)
        for case in cases:
            result = await run_case(case, workdir)
            checks = run_checks(result, case, result["context"])
            content = str((result.get("message") or {}).get("content") or "")
            outcome = {
                "case_id": case["id"],
                "title": case.get("title", ""),
                "passed": all(item["passed"] for item in checks),
                "status": result.get("status"),
                "error": (result.get("error") or {}).get("code"),
                "checks": checks,
                "sources": [
                    {"source_id": item.get("source_id"), "url": item.get("url")}
                    for item in result.get("sources") or []
                ],
                "content_preview": content[:200],
            }
            print(
                f"[{'PASS' if outcome['passed'] else 'FAIL'}] {case['id']} "
                f"status={outcome['status']}"
            )
            for item in checks:
                if not item["passed"]:
                    print(f"    - {item['check']}: {item['detail']}")
            outcomes.append(outcome)
    return outcomes


def compare_with_baseline(
    outcomes: list[dict[str, Any]], baseline_path: Path
) -> list[str]:
    """读取基线并比较，返回回归描述（判定逻辑见 ``evals.regression``）。"""

    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    return find_regressions(outcomes, baseline)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="运行 Agent 评估集")
    parser.add_argument("--case", default=None, help="只运行指定案例 ID")
    parser.add_argument("--output", default=None, help="结果 JSON 路径")
    parser.add_argument("--baseline", default=None, help="与基线结果比较（回归检查）")
    parser.add_argument("--save-baseline", default=None, help="把本次结果保存为基线")
    args = parser.parse_args(argv)

    cases = load_cases()
    if args.case:
        cases = [case for case in cases if case["id"] == args.case]
        if not cases:
            print(f"未找到案例：{args.case}")
            return 2

    outcomes = asyncio.run(run_all(cases))
    passed = sum(1 for item in outcomes if item["passed"])
    report = {
        "run_at": datetime.now(UTC).isoformat(),
        "model": Settings().model_name,
        "total": len(outcomes),
        "passed": passed,
        "cases": outcomes,
    }

    output_path = Path(args.output) if args.output else DEFAULT_OUTPUT
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"结果写入：{output_path}（{passed}/{len(outcomes)} 通过）")

    if args.save_baseline:
        baseline_path = Path(args.save_baseline)
        baseline_path.parent.mkdir(parents=True, exist_ok=True)
        baseline_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"基线已保存：{baseline_path}")

    if args.baseline:
        regressions = compare_with_baseline(outcomes, Path(args.baseline))
        if regressions:
            print("检测到回归：")
            for item in regressions:
                print(f"  - {item}")
            return 1
        print("与基线比较：无回归")

    return 0 if passed == len(outcomes) else 1


if __name__ == "__main__":
    sys.exit(main())
