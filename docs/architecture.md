# 架构说明（v3.3.1）

当前系统是单用户、本机优先的模块化单体，在 v2 文档阅读/知识库链路上新增每日 Digest、
学习画像和学习闭环。所有 Web UI 与 API 由同一个 FastAPI 进程提供；RAG 仍是可选 HTTP 依赖。

## 分层与职责

| 层 | 目录 | 职责 | 不承担 |
|---|---|---|---|
| API | `api/` | 路由、请求/响应 Schema、HTTP 错误映射 | 模型循环、SQL |
| 应用 | `application/` | 提交消息、协调对话与运行 | HTML 解析、模型协议 |
| Agent | `agent/` | Runtime、上下文预算、Prompt、工具注册、引用校验 | 抓网页、写 SQL |
| 模型 | `models/` | OpenAI 兼容适配器、工具调用与用量解析 | 对话业务 |
| 工具 | `tools/document/` | URL 策略、HTTP 获取、正文提取与规范化 | 生成回答、写业务表 |
| Digest | `digest/` | RSS/Atom 解析、Feed 抓取、来源默认值、飞书投递 | 学习进度、模型上下文 |
| V3 应用 | `application/digest_service.py`、`learning_profile.py`、`learning_workspace.py` | 日报、画像、笔记、自测、闪卡与复习编排 | 直接拼 SQL、渲染 HTML |
| 持久化 | `persistence/` | 引擎、ORM、Repository、迁移入口 | 业务编排 |
| Web | `web/` | 对话页、学习工作台与安全外链渲染 | 业务规则与数据库访问 |
| 观测 | `observability/` | 结构化日志、关联 ID、脱敏 | 判定业务成败 |

## 一次请求的数据流

1. `POST /api/v1/conversations/{id}/messages` → `ChatService` 校验输入与长度。
2. `AgentRuntime.run` 创建运行记录（数据库级「单对话单活动运行」约束）并保存用户消息。
3. `ContextBuilder` 组装系统提示、最近历史与已获取文档（Token 预算内，截断有标记）。
4. 模型请求工具 → `ToolRegistry` 校验注册与参数 → `FetchDocumentTool` 执行：
   `UrlPolicy`（白名单/协议/端口/DNS 公网校验）→ `DocumentFetcher`（逐跳重定向、对端地址校验、
   体积与超时限制）→ `extractor`/`normalizer`（正文、章节、哈希、截断）。
5. 结果写入 `document_snapshots` 与 `run_sources`，工具调用摘要写入 `tool_calls`。
6. 最终回答经 `CitationValidator` 校验引用与链接，保存助手消息并结束运行。

失败路径：任何阶段失败都会写入 `error_code`/`error_category`，运行状态置为 `failed`，
API 返回安全错误结构与 `run_id`。

## 每日 Digest 数据流

1. `DigestScheduler` 在启动或手动触发时调用 `DailyDigestService`。
2. 启用来源经 `FeedFetcher` 按来源域名执行 URL 策略和抓取限制。
3. `parse_feed` 解析 RSS/Atom，文章链接必须通过
   `domain/urls.normalize_external_url`，仅允许 HTTP(S)。
4. `push_items` 按 canonical URL 去重；历史非法链接在启动时标记为 `quarantined`。
5. `PushDigest` 和 `PushDigestItem` 保存当天排序结果与摘要。
6. `FeishuNotifier` 投递，`push_deliveries` 保存成功或安全失败分类。

当前 V3.3.1 使用 Feed 自带摘要，不调用模型生成摘要。批量模型摘要、条件请求、
robots.txt 和紧急停止属于 V3.4。

## 学习画像与闭环

- `LearningProfileService` 从成功完成的用户消息中提取主题、概念和薄弱信号。
- `learning_topics`、`learning_concepts`、`learning_events` 保存结构化证据；
  用户校正优先于自动规则。
- `LearningWorkspaceService` 从 Digest 生成带来源笔记，并生成确定性自测题和闪卡。
- `learning_reviews` 保存复习历史；`next_review_at` 驱动到期复习队列。
- `GET /api/v1/learning/progress` 聚合笔记、闪卡、留存率和薄弱主题。

画像自动注入 Prompt、记忆冲突治理和上下文压缩属于 V4。

## Web 安全边界

- 所有 RSS/Atom 文章 URL 在解析、持久化和 API 输出时校验。
- `safe-url.js` 在浏览器创建 `href` 前再次限制为 HTTP(S)。
- `api/security_headers.py` 为应用响应设置 CSP、`nosniff`、`no-referrer`、
  `frame-ancestors 'none'` 与权限策略。
- Swagger/ReDoc 路径单独排除严格 CSP，避免破坏框架自带文档资源。

## 知识库联动（v2 起）

| 模块 | 职责 | 不承担 |
|---|---|---|
| `tools/knowledge/client.py` | 调用 RAG 的入库、任务查询、来源与检索接口；超时、有限重试与错误映射 | 抓取网页、写入 RAG 数据库 |
| `tools/knowledge/tool.py` | 四个工具的 Schema、参数校验、结果压缩与来源登记 | 生成回答、持久化通知 |
| `application/ingestion_watcher.py` | watch 登记、退避轮询、终态通知、旧 watch 清理 | 调用 LLM、修改知识库 |
| `persistence/*` | `ingestion_watches`、消息 `kind`、来源 `provider` | 判断业务成败 |

数据流：

1. **入库**：用户消息 → 模型调用 `ingest_urls` → RAG 创建 job → 登记 watch →
   返回 `job_id`。链接不落 Agent 侧正文库。
2. **通知**：watcher 轮询 job → 终态写入 `kind=ingestion_notice` 的 assistant 消息 →
   标记 `notified_at`。通知不进入模型上下文。
3. **检索**：模型调用 `search_knowledge` → RAG 返回证据片段 → 相似度门槛 →
   登记 `provider=knowledge_base` 的来源 → 引用校验 → 持久化回答。

边界：RAG 是可选外部依赖，通过 HTTP 契约集成；两套数据库不做分布式事务，
入库用幂等键、通知用唯一 `job_id` 保证重复安全。

## 关键设计决策

见 `docs/adr/`：模型接口（0001）、HTML 提取（0002）、持久化与迁移（0003）、评估策略（0004）、
异步运行（0005）、知识库集成（0006）。
安全边界与残余风险见 `docs/threat-model.md`；实际验证结果见
`docs/verification-log.md`。
