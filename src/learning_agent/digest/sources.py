"""V3 默认可信 AI 资讯来源。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DefaultSource:
    id: str
    name: str
    feed_url: str
    domain: str
    category: str
    trust_level: str = "trusted"


DEFAULT_SOURCES: tuple[DefaultSource, ...] = (
    DefaultSource(
        id="huggingface-blog",
        name="Hugging Face Blog",
        feed_url="https://huggingface.co/blog/feed.xml",
        domain="huggingface.co",
        category="开源工具",
    ),
    DefaultSource(
        id="google-ai-blog",
        name="Google AI Blog",
        feed_url="https://blog.google/technology/ai/rss/",
        domain="blog.google",
        category="模型",
    ),
    DefaultSource(
        id="microsoft-ai-blog",
        name="Microsoft AI Blog",
        feed_url="https://blogs.microsoft.com/ai/feed/",
        domain="blogs.microsoft.com",
        category="模型",
    ),
    DefaultSource(
        id="aws-ml-blog",
        name="AWS Machine Learning Blog",
        feed_url="https://aws.amazon.com/blogs/machine-learning/feed/",
        domain="aws.amazon.com",
        category="RAG",
    ),
    DefaultSource(
        id="langchain-blog",
        name="LangChain Blog",
        feed_url="https://blog.langchain.dev/rss/",
        domain="blog.langchain.dev",
        category="Agent",
    ),
    DefaultSource(
        id="arxiv-cs-ai",
        name="arXiv cs.AI",
        feed_url="https://export.arxiv.org/rss/cs.AI",
        domain="export.arxiv.org",
        category="评测",
        trust_level="verified",
    ),
)
