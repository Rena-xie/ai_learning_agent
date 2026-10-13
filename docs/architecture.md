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

## 关键设计决策

见 `docs/adr/`：模型接口（0001）、HTML 提取（0002）、持久化与迁移（0003）、评估策略（0004）。
安全边界与残余风险见 `docs/threat-model.md`。
