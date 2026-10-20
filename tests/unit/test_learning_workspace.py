"""V3.2 学习内容生成与复习间隔的纯函数测试。"""

from __future__ import annotations

from learning_agent.application.learning_workspace import _next_interval, _plain_text


def test_review_intervals_are_stable_and_replayable() -> None:
    assert _next_interval(previous_days=1, review_count=0, rating="good") == 3
    assert _next_interval(previous_days=3, review_count=1, rating="good") == 7
    assert _next_interval(previous_days=7, review_count=2, rating="hard") == 8
    assert _next_interval(previous_days=7, review_count=2, rating="easy") == 21
    assert _next_interval(previous_days=21, review_count=3, rating="again") == 1


def test_summary_text_is_cleaned_before_note_generation() -> None:
    assert _plain_text("<p>Agent&nbsp;memory</p>\n<p>lifecycle</p>") == (
        "Agent memory lifecycle"
    )
