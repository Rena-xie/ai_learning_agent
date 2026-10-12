"""领域枚举：运行状态、消息角色、工具调用状态与文档解析状态。"""

from __future__ import annotations

from enum import StrEnum


class RunStatus(StrEnum):
    """Agent Run 状态。v1.0 不实现取消，但保留 cancelled 作为状态契约。"""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class MessageRole(StrEnum):
    """对话消息角色。"""

    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class ToolCallStatus(StrEnum):
    """单次工具调用的结果状态。"""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    REJECTED = "rejected"
    TIMEOUT = "timeout"


class DocumentParseStatus(StrEnum):
    """文档解析结果状态。"""

    OK = "ok"
    EMPTY = "empty"
    FAILED = "failed"
    UNSUPPORTED = "unsupported"


def is_terminal_run_status(status: RunStatus) -> bool:
    """判断运行状态是否已终结（不再变化）。"""

    return status in {
        RunStatus.COMPLETED,
        RunStatus.FAILED,
        RunStatus.CANCELLED,
    }
