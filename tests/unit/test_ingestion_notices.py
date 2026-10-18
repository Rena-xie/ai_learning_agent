"""入库通知文案（v2 M3）。"""

from __future__ import annotations

from learning_agent.application.ingestion_watcher import build_completion_notice


def completed_payload() -> dict[str, object]:
    return {
        "job_id": "job_1",
        "status": "completed",
        "total": 2,
        "succeeded": 2,
        "failed": 0,
        "skipped": 0,
        "items": [
            {
                "url": "https://example.com/a",
                "status": "completed",
                "title": "文章 A",
            },
            {
                "url": "https://example.com/b",
                "status": "completed",
                "title": "文章 B",
            },
        ],
    }


def test_completed_notice_contains_titles_and_next_step() -> None:
    text = build_completion_notice(completed_payload())

    assert "已完成入库" in text
    assert "成功 2 条" in text
    assert "文章 A" in text
    assert "现在可以基于这些内容提问" in text
    assert len(text) <= 300


def test_all_skipped_notice_is_explicit() -> None:
    text = build_completion_notice(
        {
            "status": "completed",
            "total": 1,
            "succeeded": 0,
            "failed": 0,
            "skipped": 1,
            "items": [],
        }
    )

    assert "均已存在" in text
    assert "无需重复入库" in text


def test_partial_notice_includes_failure_reason() -> None:
    text = build_completion_notice(
        {
            "status": "partial",
            "total": 2,
            "succeeded": 1,
            "failed": 1,
            "skipped": 0,
            "items": [
                {"url": "https://example.com/ok", "status": "completed"},
                {
                    "url": "https://example.com/bad",
                    "status": "failed",
                    "error_code": "ROBOTS_DISALLOWED",
                    "error_message": "robots.txt 禁止抓取",
                },
            ],
        }
    )

    assert "入库部分完成" in text
    assert "https://example.com/bad" in text
    assert "ROBOTS_DISALLOWED" in text
    assert len(text) <= 300


def test_failed_notice_is_bounded() -> None:
    text = build_completion_notice(
        {
            "status": "failed",
            "total": 1,
            "succeeded": 0,
            "failed": 1,
            "skipped": 0,
            "items": [
                {
                    "url": "https://example.com/" + "x" * 500,
                    "status": "failed",
                    "error_code": "FETCH_TIMEOUT",
                    "error_message": "抓取超时",
                }
            ],
        }
    )

    assert "入库失败" in text
    assert "FETCH_TIMEOUT" in text
    assert len(text) <= 300
