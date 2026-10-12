"""HTML 正文提取与章节解析（基于 trafilatura + lxml）。"""

from __future__ import annotations

from dataclasses import dataclass

from lxml import etree
from lxml import html as lxml_html
from trafilatura import extract as trafilatura_extract
from trafilatura import extract_metadata

from learning_agent.domain.entities import Section
from learning_agent.domain.errors import DocumentToolError, ErrorCode

_BLOCK_TAGS = frozenset({"p", "item", "quote", "head", "table", "fw", "cell"})


@dataclass(frozen=True)
class ExtractedDocument:
    """正文提取结果。"""

    title: str | None
    text: str
    sections: tuple[Section, ...] = ()
    warnings: tuple[str, ...] = ()


def extract_document(html_text: str, url: str) -> ExtractedDocument:
    """从 HTML 中提取正文与章节信息。

    正文为空、只有导航或脚本内容时抛出 ``content_extraction_failed``，
    绝不返回伪造的成功结果。
    """

    extracted_xml = trafilatura_extract(
        html_text,
        url=url,
        output_format="xml",
        include_tables=True,
        include_comments=False,
        include_formatting=False,
        favor_precision=True,
    )
    if not extracted_xml:
        raise DocumentToolError(
            ErrorCode.CONTENT_EXTRACTION_FAILED,
            "无法从该页面提取正文内容（可能是导航页、脚本渲染页或不支持的页面结构）",
        )

    text, sections, warnings = _parse_xml(extracted_xml)
    if not text.strip():
        raise DocumentToolError(
            ErrorCode.CONTENT_EXTRACTION_FAILED, "该页面正文为空，无法作为回答依据"
        )
    return ExtractedDocument(
        title=_extract_title(html_text),
        text=text,
        sections=tuple(sections),
        warnings=tuple(warnings),
    )


def _parse_xml(xml_text: str) -> tuple[str, list[Section], list[str]]:
    parser = etree.XMLParser(recover=True, resolve_entities=False, no_network=True)
    root = etree.fromstring(xml_text.encode("utf-8"), parser=parser)
    if root is None:
        raise DocumentToolError(ErrorCode.CONTENT_EXTRACTION_FAILED, "正文结构解析失败")

    blocks: list[str] = []
    sections: list[Section] = []
    warnings: list[str] = []
    current_title: str | None = None
    current_level = 0
    current_parts: list[str] = []

    def flush() -> None:
        if current_title is None:
            return
        sections.append(
            Section(
                title=current_title,
                level=current_level,
                text="\n".join(current_parts).strip(),
            )
        )

    for element in root.iter():
        tag = etree.QName(element).localname
        if tag not in _BLOCK_TAGS:
            continue
        content = " ".join(part.strip() for part in element.itertext() if part.strip())
        if not content:
            continue
        if tag == "head":
            flush()
            current_title = content
            current_level = _parse_level(element.get("rend"))
            current_parts = []
            blocks.append(content)
        else:
            blocks.append(content)
            if current_title is not None:
                current_parts.append(content)
    flush()

    if not sections and blocks:
        warnings.append("未识别到章节标题，仅返回连续正文")
    return "\n".join(blocks), sections, warnings


def _parse_level(rend: str | None) -> int:
    if not rend:
        return 0
    digits = "".join(ch for ch in rend if ch.isdigit())
    if not digits:
        return 0
    return max(1, min(6, int(digits)))


def _extract_title(html_text: str) -> str | None:
    try:
        metadata = extract_metadata(html_text)
    except Exception:  # noqa: BLE001 - 元数据提取失败不应影响正文
        metadata = None
    title = getattr(metadata, "title", None) if metadata else None
    if title and title.strip():
        return title.strip()
    try:
        document = lxml_html.fromstring(html_text)
    except (etree.ParserError, ValueError):
        return None
    if document is None:
        return None
    for tag_name in ("title", "h1"):
        elements = document.xpath(f"//{tag_name}")
        if elements:
            text = " ".join(elements[0].itertext()).strip()
            if text:
                return text
    return None
