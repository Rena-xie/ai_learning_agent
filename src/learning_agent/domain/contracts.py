"""模块契约：工具执行上下文与来源编号分配。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@runtime_checkable
class SourceAllocator(Protocol):
    """为一次运行中的来源分配稳定编号（S1、S2 ...）。"""

    def next_source_id(self) -> str:
        """返回下一个来源编号。"""

        ...


@dataclass(frozen=True)
class ToolContext:
    """工具执行上下文，只暴露工具需要的运行信息。"""

    run_id: str
    conversation_id: str
    allocator: SourceAllocator
    user_message: str = ""
