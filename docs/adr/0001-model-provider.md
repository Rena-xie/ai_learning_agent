# ADR 0001：模型供应接口采用 OpenAI 兼容适配器（DeepSeek）

- 状态：已接受
- 日期：2026-10-13

## 背景

v1.0 需要单一模型供应商、支持工具调用（function calling），并记录用量与错误类别；
项目要求不实现多供应商自动路由，避免为尚未需要的场景增加抽象。

## 决策

`models/provider_adapter.py` 实现一个 OpenAI 兼容协议的适配器，通过
`MODEL_BASE_URL` / `MODEL_NAME` 指向 DeepSeek 的 OpenAI 兼容端点。适配器内部：

- 把 SDK 消息、工具调用请求、用量与错误统一映射为内部结构（`ChatMessage`、
  `ModelResponse`、`UsageRecord`、`ModelError`）；
- 自行实现有限重试与指数退避，只重试超时、连接失败、限流与 5xx；
- 不向业务层暴露供应商 SDK 类型，便于后续替换或新增供应商。

## 备选方案

- 直接使用供应商私有 SDK：会污染业务层，且难以替换。
- 多供应商路由框架：超出 v1.0 范围，增加依赖与不确定性。

## 影响

- 更换供应商只需实现一个符合 `ModelClient` 协议的适配器；
- 工具调用协议差异（例如并行工具调用、参数流式返回）需要在适配器内集中处理；
- 单元测试不依赖真实 API，真实调用集中在标记为 `real_model` 的测试中。
