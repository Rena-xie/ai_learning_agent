# ADR 0006：通过 HTTP 契约集成外部 RAG 知识库

- 状态：接受
- 日期：2026-10-20
- 关联：v2.0 知识库联动（M1–M5）

## 背景

Learning Agent 已经能阅读单篇文档并给出可核验来源，但缺少“长期收藏、之后基于已入库内容提问”的能力。
仓库同机已有独立的 RAG 服务（抓取、切分、向量化、混合检索与引用校验），需要在不复制其实现的前提下接入。

## 决策

1. **只通过 HTTP 契约集成**：Agent 调用 RAG 的
   `POST /api/v1/ingestions`、`GET /api/v1/ingestions/{job_id}`、
   `GET /api/v1/sources`、`POST /api/v1/search`；不直接访问 RAG 的 SQLite 或 Chroma。
2. **入库异步、通知确定性**：`ingest_urls` 只提交任务并登记 `ingestion_watches`；
   watcher 退避轮询终态，写一条不调用 LLM 的 `ingestion_notice` 消息。
3. **重复安全**：入库使用 `Idempotency-Key`（run_id + URL 集合哈希），
   watch 以 `job_id` 唯一；通知通过状态与 `notified_at` 原子去重。
4. **知识库证据按不可信片段处理**：`search_knowledge` 返回的证据登记为
   `provider=knowledge_base` 的来源，与网页抓取共用引用校验；
   证据不作为完整文档在后续对话中复用。
5. **两套数据不做分布式事务**：允许最终一致；分别备份，恢复顺序为 RAG → Agent。

## 备选方案

- **直接读取 RAG 数据库/Chroma**：耦合内部 Schema，绕过其安全与检索逻辑，放弃。
- **同步入库**：长任务阻塞对话，超时语义复杂，且与 RAG 的异步队列设计冲突，放弃。
- **Agent 内复制一套检索栈**：重复实现、依赖膨胀，放弃。

## 后果

- RAG 成为可选外部依赖：关闭或不可用时 v1 文档阅读链路保持可用。
- 需要处理跨服务超时、重试、错误映射与 watch 积压；已实现有限重试与统计。
- 知识库证据只保证“引用指向真实来源”，不保证每条主张都被来源支持；由评估集抽样检查。
- 跨主机部署时 RAG 缺少应用级鉴权，必须置于受信网络或补共享 token。
