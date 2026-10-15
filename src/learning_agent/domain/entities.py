"""跨模块共享的领域数据对象（纯数据，不依赖框架与模型 SDK）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class Section:
    """文档章节：标题、层级与对应内容片段。"""

    title: str
    level: int
    text: str


@dataclass(frozen=True)
class DocumentContent:
    """清洗后的文档内容与来源元数据。"""

    title: str
    canonical_url: str
    fetched_at: datetime
    content_hash: str
    content_text: str
    sections: tuple[Section, ...] = ()
    truncated: bool = False
    warnings: tuple[str, ...] = ()

    @property
    def text_length(self) -> int:
        return len(self.content_text)


@dataclass(frozen=True)
class SourceReference:
    """一次运行中可被引用的来源记录（由服务端生成，模型不可伪造）。"""

    source_id: str
    snapshot_id: int
    canonical_url: str
    title: str
    fetched_at: datetime
    truncated: bool = False
    reused: bool = False


@dataclass(frozen=True)
class ToolOutcome:
    """工具执行结果：模型可见载荷 + 持久化用摘要 + 来源登记信息。"""

    ok: bool
    model_payload: dict[str, object]
    result_summary: dict[str, object]
    error_code: str | None = None
    source_id: str | None = None
    content: DocumentContent | None = None


@dataclass(frozen=True)
class UsageRecord:
    """模型用量记录；未知值保持 None，不编造。"""

    input_tokens: int | None = None
    output_tokens: int | None = None
    estimated_cost: float | None = None

    def __add__(self, other: UsageRecord) -> UsageRecord:
        return UsageRecord(
            input_tokens=_add_optional(self.input_tokens, other.input_tokens),
            output_tokens=_add_optional(self.output_tokens, other.output_tokens),
            estimated_cost=_add_optional(self.estimated_cost, other.estimated_cost),
        )


def _add_optional(
    left: int | float | None, right: int | float | None
) -> int | float | None:
    if left is None:
        return right
    if right is None:
        return left
    return left + right


def estimate_cost(
    usage: UsageRecord,
    *,
    input_price_per_1k: float,
    output_price_per_1k: float,
) -> UsageRecord:
    """按单价估算成本，返回带 ``estimated_cost`` 的新记录。

    未配置单价、或 token 用量未知时，保持 ``estimated_cost`` 为 None，不编造成本。
    """

    if input_price_per_1k <= 0 and output_price_per_1k <= 0:
        return usage
    if usage.input_tokens is None or usage.output_tokens is None:
        return usage
    cost = (
        usage.input_tokens / 1000 * input_price_per_1k
        + usage.output_tokens / 1000 * output_price_per_1k
    )
    return UsageRecord(
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        estimated_cost=round(cost, 6),
    )


@dataclass(frozen=True)
class ToolCallRecord:
    """一次工具调用的持久化摘要。"""

    id: str
    tool_name: str
    status: str
    started_at: datetime
    finished_at: datetime | None = None
    duration_ms: int | None = None
    arguments_summary: str = ""
    result_summary: dict[str, object] = field(default_factory=dict)
    error_code: str | None = None
