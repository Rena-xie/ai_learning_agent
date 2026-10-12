# ADR 0002：HTML 正文提取使用 trafilatura

- 状态：已接受
- 日期：2026-10-13

## 背景

文档工具需要从官方技术文档页面提取正文与章节结构，且不能依赖脆弱的正则表达式。
要求选型记录理由，并保证正文为空或只有导航/脚本时返回明确的失败状态。

## 决策

使用 `trafilatura` 做正文提取，输出 XML 结构后用 `lxml` 解析出章节标题与内容片段：

- trafilatura 对技术文档、博客与百科类页面表现稳定，维护活跃；
- XML 输出保留了标题层级（`<head rend="h1">`），可据此生成 `sections`；
- 提取为空时返回 `content_extraction_failed`，不伪造成功结果；
- 额外用 `extract_metadata` + `<title>`/`<h1>` 回退获取标题（缺失时用主机名回退值）。

## 备选方案

- `readability-lxml`：实现简单，但章节结构信息少、对复杂页面正文召回不稳定。
- 自研正则提取：脆弱且不符合规范要求，直接排除。

## 影响

- 新增依赖 `trafilatura`（含 `lxml`、`justext` 等）；
- 提取质量与库版本相关，升级需回归 `tests/unit/test_extractor.py` 与
  `tests/integration/test_document_pipeline.py`；
- 标题优先采用正文主标题（h1），与 `<title>` 不一致时以正文为准。
