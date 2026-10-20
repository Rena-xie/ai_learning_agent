"""知识库真实链路冒烟：对运行中的 Agent 提问并校验知识库引用。

用法::

    uv run python -m scripts.smoke_knowledge --base-url http://127.0.0.1:8000
    uv run python -m scripts.smoke_knowledge --output evals/results/knowledge-smoke.json

要求 Agent 已启动且知识库可用；脚本本身不启动服务、不写入知识库。
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import httpx

DEFAULT_QUESTIONS = (
    "RAG 的核心定义是什么？",
    "文本分块有哪些常见策略？",
    "如何评估一个 RAG 系统？",
)


def evaluate_response(status_code: int, body: dict[str, object]) -> dict[str, object]:
    """校验单题回答：201、包含 [S1]、来源非空且全部来自知识库。"""

    message = body.get("message")
    content = str(message.get("content") or "") if isinstance(message, dict) else ""
    sources = body.get("sources")
    source_items = sources if isinstance(sources, list) else []
    providers = sorted(
        {
            str(item.get("provider"))
            for item in source_items
            if isinstance(item, dict) and item.get("provider")
        }
    )
    passed = (
        status_code == 201
        and bool(source_items)
        and providers == ["knowledge_base"]
        and "[S1]" in content
    )
    return {
        "status": status_code,
        "passed": passed,
        "citation_present": "[S1]" in content,
        "sources": len(source_items),
        "providers": providers,
        "run_id": body.get("run_id"),
    }


def run_smoke(
    client: httpx.Client, questions: Sequence[str]
) -> tuple[list[dict[str, object]], bool]:
    results: list[dict[str, object]] = []
    all_passed = True
    for question in questions:
        conversation = client.post("/api/v1/conversations", json={})
        conversation.raise_for_status()
        conversation_id = conversation.json()["id"]
        response = client.post(
            f"/api/v1/conversations/{conversation_id}/messages",
            json={"content": f"根据知识库回答：{question}"},
        )
        body = response.json() if response.content else {}
        result = evaluate_response(response.status_code, body)
        result["question"] = question
        results.append(result)
        all_passed = all_passed and bool(result["passed"])
    return results, all_passed


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="smoke_knowledge")
    parser.add_argument(
        "--base-url", default="http://127.0.0.1:8000", help="运行中的 Agent 地址"
    )
    parser.add_argument(
        "--question",
        action="append",
        dest="questions",
        help="自定义问题，可重复；省略则使用内置 3 题",
    )
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--output", type=Path, default=None, help="结果 JSON 输出路径")
    args = parser.parse_args(argv)
    questions = tuple(args.questions or DEFAULT_QUESTIONS)

    with httpx.Client(base_url=args.base_url, timeout=args.timeout) as client:
        ready = client.get("/health/ready")
        ready_body = ready.json() if ready.content else {}
        if ready.status_code != 200 or ready_body.get("knowledge_base") != "ok":
            print(
                json.dumps(
                    {"error": "知识库未就绪", "ready": ready_body},
                    ensure_ascii=False,
                ),
                file=sys.stderr,
            )
            return 2
        results, all_passed = run_smoke(client, questions)

    summary = {
        "created_at": datetime.now(UTC).isoformat(),
        "base_url": args.base_url,
        "questions": len(questions),
        "passed": sum(1 for item in results if item["passed"]),
        "all_passed": all_passed,
        "results": results,
    }
    text = json.dumps(summary, ensure_ascii=False, indent=2)
    print(text)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    return 0 if all_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
