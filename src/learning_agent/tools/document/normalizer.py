"""文本规范化、内容哈希与长度截断。"""

from __future__ import annotations

import hashlib
import re

_SPACES = re.compile(r"[ \t\u00a0\u3000]+")


def normalize_text(text: str) -> str:
    """统一空白与控制字符：合并连续空格、去除控制字符、最多保留单个空行。"""

    if not text:
        return ""
    unified = text.replace("\r\n", "\n").replace("\r", "\n").replace("\u00a0", " ")
    cleaned_chars = [ch for ch in unified if ch in "\n\t" or ord(ch) >= 0x20]
    cleaned = "".join(cleaned_chars)
    lines = [_SPACES.sub(" ", line).strip() for line in cleaned.split("\n")]
    result: list[str] = []
    for line in lines:
        if line:
            result.append(line)
        elif result and result[-1] != "":
            result.append("")
    while result and result[-1] == "":
        result.pop()
    return "\n".join(result)


def compute_content_hash(text: str) -> str:
    """计算清洗后正文的 SHA-256（用于识别内容变化）。"""

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def truncate_text(text: str, max_chars: int) -> tuple[str, bool]:
    """按字符数截断，返回 (文本, 是否被截断)。"""

    if len(text) <= max_chars:
        return text, False
    return text[:max_chars], True


def summarize(text: str, *, limit: int = 200) -> str:
    """生成用于日志与数据库摘要的短文本（不保存完整正文）。"""

    flat = " ".join(normalize_text(text).split())
    if len(flat) <= limit:
        return flat
    return flat[:limit] + "…"
