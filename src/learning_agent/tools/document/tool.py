"""``fetch_document`` 工具：获取、提取并规范化一份 HTML 文档。"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from pydantic import BaseModel, Field

from learning_agent.domain.contracts import ToolContext
from learning_agent.domain.entities import DocumentContent, Section, ToolOutcome
from learning_agent.domain.errors import AppError, ErrorCode
from learning_agent.tools.document.extractor import extract_document
from learning_agent.tools.document.fetcher import DocumentFetcher
from learning_agent.tools.document.normalizer import (
    compute_content_hash,
    normalize_text,
    truncate_text,
)

logger = logging.getLogger(__name__)

TOOL_NAME = "fetch_document"
TOOL_DESCRIPTION = (
    "获取一个已允许的官方技术文档 URL 的正文内容。"
    "返回文档标题、规范化 URL、获取时间、内容哈希、清洗后的正文与章节信息。"
)
_MAX_SECTIONS = 30
_SECTION_EXCERPT_CHARS = 400


class FetchDocumentArgs(BaseModel):
    """fetch_document 的参数 Schema。"""

    url: str = Field(description="要获取的文档 URL，必须位于允许的文档域名内")


@dataclass(frozen=True)
class DocumentToolResult:
    """文档工具执行成功后的结构化结果。"""

    source_id: str
    content: DocumentContent
    warnings: tuple[str, ...] = ()

    def to_model_payload(self, *, max_chars: int) -> dict[str, object]:
        """构造发给模型的工具结果（长度受限）。"""

        body, body_truncated = truncate_text(self.content.content_text, max_chars)
        sections = [
            {
                "title": section.title,
                "level": section.level,
                "excerpt": truncate_text(section.text, _SECTION_EXCERPT_CHARS)[0],
            }
            for section in self.content.sections[:_MAX_SECTIONS]
        ]
        warnings = list(self.warnings)
        if body_truncated:
            warnings.append("正文超出单次工具结果上限，已截断")
        if len(self.content.sections) > _MAX_SECTIONS:
            warnings.append("章节数量超出上限，仅返回前若干章节")
        return {
            "status": "ok",
            "source_id": self.source_id,
            "title": self.content.title,
            "canonical_url": self.content.canonical_url,
            "fetched_at": self.content.fetched_at.isoformat(),
            "content_hash": self.content.content_hash,
            "content_text": body,
            "sections": sections,
            "truncated": self.content.truncated or body_truncated,
            "warnings": warnings,
        }


class FetchDocumentTool:
    """受控的文档获取工具实现。"""

    name = TOOL_NAME
    description = TOOL_DESCRIPTION
    args_model = FetchDocumentArgs
    parameters_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "要获取的文档 URL，必须位于允许的文档域名内",
            }
        },
        "required": ["url"],
        "additionalProperties": False,
    }

    def __init__(
        self,
        fetcher: DocumentFetcher,
        *,
        max_document_chars: int,
    ) -> None:
        self._fetcher = fetcher
        self._max_document_chars = max_document_chars

    async def __call__(
        self, args: FetchDocumentArgs, context: ToolContext
    ) -> ToolOutcome:
        source_id = context.allocator.next_source_id()
        try:
            fetch_result = await self._fetcher.fetch(args.url)
            extracted = extract_document(fetch_result.html, fetch_result.canonical_url)
        except AppError as exc:
            logger.info(
                "document_fetch_failed",
                extra={
                    "extra_fields": {
                        "error_code": str(exc.code),
                        "url": _safe_url(args.url),
                    }
                },
            )
            return ToolOutcome(
                ok=False,
                error_code=str(exc.code),
                model_payload={
                    "status": "error",
                    "error_code": str(exc.code),
                    "message": exc.message,
                    "url": _safe_url(args.url),
                },
                result_summary={
                    "status": "error",
                    "error_code": str(exc.code),
                    "url": _safe_url(args.url),
                },
            )

        normalized = normalize_text(extracted.text)
        if not normalized:
            return ToolOutcome(
                ok=False,
                error_code=str(ErrorCode.CONTENT_EXTRACTION_FAILED),
                model_payload={
                    "status": "error",
                    "error_code": str(ErrorCode.CONTENT_EXTRACTION_FAILED),
                    "message": "该页面没有可用的正文内容",
                    "url": _safe_url(args.url),
                },
                result_summary={
                    "status": "error",
                    "error_code": str(ErrorCode.CONTENT_EXTRACTION_FAILED),
                    "url": _safe_url(args.url),
                },
            )

        content_hash = compute_content_hash(normalized)
        text, truncated = truncate_text(normalized, self._max_document_chars)
        sections = tuple(
            Section(
                title=section.title,
                level=section.level,
                text=truncate_text(
                    normalize_text(section.text), self._max_document_chars
                )[0],
            )
            for section in extracted.sections[: _MAX_SECTIONS * 2]
        )
        title = normalize_text(extracted.title or "") or _fallback_title(
            fetch_result.canonical_url
        )
        warnings = list(extracted.warnings)
        if truncated:
            warnings.append(f"文档正文超过 {self._max_document_chars} 字符上限，已截断")
        content = DocumentContent(
            title=title,
            canonical_url=fetch_result.canonical_url,
            fetched_at=fetch_result.fetched_at,
            content_hash=content_hash,
            content_text=text,
            sections=sections,
            truncated=truncated,
            warnings=tuple(warnings),
        )
        logger.info(
            "document_fetched",
            extra={
                "extra_fields": {
                    "source_id": source_id,
                    "url": _safe_url(content.canonical_url),
                    "chars": content.text_length,
                    "truncated": truncated,
                    "sections": len(sections),
                }
            },
        )
        result = DocumentToolResult(
            source_id=source_id, content=content, warnings=tuple(warnings)
        )
        return ToolOutcome(
            ok=True,
            source_id=source_id,
            content=content,
            model_payload=result.to_model_payload(max_chars=self._max_document_chars),
            result_summary={
                "status": "ok",
                "source_id": source_id,
                "url": _safe_url(content.canonical_url),
                "title": content.title,
                "chars": content.text_length,
                "truncated": content.truncated,
                "content_hash": content.content_hash,
            },
        )


def _fallback_title(url: str) -> str:
    from urllib.parse import urlsplit

    host = urlsplit(url).hostname or url
    return f"{host} 文档"


def _safe_url(url: str) -> str:
    from learning_agent.observability.logging import redact_url

    return redact_url(url)
