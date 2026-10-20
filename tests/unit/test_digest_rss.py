"""V3 RSS/Atom 解析测试。"""

from datetime import UTC, datetime

from learning_agent.digest.rss import parse_feed


def test_parses_rss_items() -> None:
    xml = """<?xml version="1.0"?>
    <rss version="2.0"><channel>
      <item>
        <title>RAG 新进展</title>
        <link>/posts/rag</link>
        <description><![CDATA[<p>RAG 的检索质量提升。</p>]]></description>
        <pubDate>Tue, 20 Oct 2026 09:00:00 GMT</pubDate>
      </item>
    </channel></rss>"""

    entries = parse_feed(
        xml, feed_url="https://example.com/feed.xml", source_topic="RAG"
    )

    assert len(entries) == 1
    assert entries[0].title == "RAG 新进展"
    assert entries[0].url == "https://example.com/posts/rag"
    assert entries[0].summary == "RAG 的检索质量提升。"
    assert entries[0].published_at == datetime(2026, 10, 20, 9, tzinfo=UTC)
    assert entries[0].topic == "RAG"


def test_parses_atom_entries_and_skips_invalid() -> None:
    xml = """<feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <title>Agent 工具调用</title>
        <link href="https://example.com/agent" />
        <summary>工具循环和引用。</summary>
        <updated>2026-10-20T10:00:00Z</updated>
      </entry>
      <entry><title>缺少链接</title></entry>
    </feed>"""

    entries = parse_feed(xml, feed_url="https://example.com/feed")

    assert len(entries) == 1
    assert entries[0].url == "https://example.com/agent"
    assert entries[0].published_at == datetime(2026, 10, 20, 10, tzinfo=UTC)


def test_invalid_xml_returns_empty_list() -> None:
    assert parse_feed("<not-xml", feed_url="https://example.com/feed") == []
