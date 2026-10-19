# 架构说明

## 分层与职责

| 层 | 目录 | 职责 | 不承担 |
|---|---|---|---|
| API | `api/` | 路由、请求/响应 Schema、HTTP 错误映射 | 模型循环、SQL |
| 应用 | `application/` | 提交消息、协调对话与运行 | HTML 解析、模型协议 |
| Agent | `agent/` | Runtime、上下文预算、Prompt、工具注册、引用校验 | 抓网页、写 SQL |
| 模型 | `models/` | OpenAI 兼容适配器、工具调用与用量解析 | 对话业务 |
| 工具 | `tools/document/` | URL 策略、HTTP 获取、正文提取与规范化 | 生成回答、写业务表 |
| 持久化 | `persistence/` | 引擎、ORM、Repository、迁移入口 | 业务编排 |
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

## 知识库联动（v2）

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

见 `docs/adr/`：模型接口（0001）、HTML 提取（0002）、持久化与迁移（0003）、评估策略（0004）。
安全边界与残余风险见 `docs/threat-model.md`。
