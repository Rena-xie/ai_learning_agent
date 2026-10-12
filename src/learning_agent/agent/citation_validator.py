"""来源引用校验：只允许引用真实登记过的来源编号。"""

from __future__ import annotations

import re
from dataclasses import dataclass

CITATION_PATTERN = re.compile(r"\[(S\d{1,3})\]")
_MARKDOWN_LINK_PATTERN = re.compile(r"\[([^\]\[]+)\]\((https?://[^)\s]+)\)")


@dataclass(frozen=True)
class CitationValidationResult:
    """引用校验结果。"""

    content: str
    used_source_ids: tuple[str, ...]
    invalid_source_ids: tuple[str, ...]
    stripped_links: tuple[str, ...] = ()


def validate_citations(
    content: str, *, allowed_source_ids: set[str]
) -> CitationValidationResult:
    """校验并清理回答中的来源标记与链接。

    - 无效来源编号会被移除并记录，不会变成可点击链接；
    - 模型自带的 Markdown 链接会被移除链接目标（保留可见文本），
      来源链接由服务端根据真实记录生成。
    """

    found = CITATION_PATTERN.findall(content)
    used: list[str] = []
    invalid: list[str] = []
    for source_id in found:
        if source_id in allowed_source_ids:
            if source_id not in used:
                used.append(source_id)
        elif source_id not in invalid:
            invalid.append(source_id)

    cleaned = content
    for source_id in invalid:
        cleaned = cleaned.replace(f"[{source_id}]", "")

    stripped: list[str] = []

    def _strip_link(match: re.Match[str]) -> str:
        text, url = match.group(1), match.group(2)
        stripped.append(url)
        return text

    cleaned = _MARKDOWN_LINK_PATTERN.sub(_strip_link, cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    return CitationValidationResult(
        content=cleaned.strip(),
        used_source_ids=tuple(used),
        invalid_source_ids=tuple(invalid),
        stripped_links=tuple(stripped),
    )
