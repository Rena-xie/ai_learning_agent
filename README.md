# AI Learning Agent

本地可运行的 AI 学习助手：提交受支持的官方技术文档 URL，系统获取并解析文档，基于实际获取的内容讲解核心概念，并给出可追溯的真实来源。

需求基线：[项目说明_v1.0.md](项目说明_v1.0.md)；开发规范：[AGENTS.md](AGENTS.md)。

## 当前进度

- **阶段 1（最小可用 Agent 链路）：已完成**。真实模型（DeepSeek `deepseek-v4-flash`）+ 真实公网文档
  （`liaoxuefeng.com`）的 API 端到端测试通过。
- **阶段 2-5：未实现**。对话删除接口与前端界面、Alembic 迁移、备份与恢复、
  Docker Compose、Agent 评估集、CI 将在后续阶段交付。

## 已实现能力

- **模型适配器**：OpenAI 兼容协议（默认 DeepSeek），工具调用、有限重试、用量解析与统一错误映射。
- **文档工具 `fetch_document`**：精确域名白名单、仅 http/https、拒绝 URL 凭据、端口限制、
  DNS 结果必须为公网地址、重定向逐跳重新校验、超时/响应体积/正文长度限制、
  trafilatura + lxml 正文与章节提取、内容哈希与截断标记。
- **Agent Runtime**：模型 ↔ 工具循环、最大模型轮数/工具调用次数/总时限、
  上下文 Token 预算与截断标注、`run_id` 全程关联。
- **来源引用**：来源编号（`S1`…）由服务端分配并校验；无效引用被移除并记录；
  用户看到的链接与标题来自服务端来源记录，模型无法伪造。
- **持久化**：SQLite + SQLAlchemy（对话、消息、运行、工具调用、文档快照、来源关联），
  数据库级「同一对话同一时间仅一个活动运行」约束，删除对话级联清理。
- **API**：`GET /health/live`、`GET /health/ready`、`POST/GET /api/v1/conversations`、
  对话消息接口、`GET /api/v1/runs/{run_id}`。
- **安全与可观测**：安全边界在真实执行处生效（URL 策略、工具注册表、引用校验），
  结构化 JSON 日志 + 敏感信息脱敏，错误分类区分用户输入/外部依赖/内部错误。

## 快速开始（命令均已实际执行验证）

1. 安装依赖：

   ```bash
   uv sync
   ```

2. 复制 `.env.example` 为 `.env`，填写 `MODEL_API_KEY`（模型名、Base URL、
   允许的文档域名与各项资源限制见样例说明）。

3. 启动服务：

   ```bash
   uv run uvicorn --factory learning_agent.main:create_app --host 127.0.0.1 --port 8000
   ```

4. 健康检查：`http://127.0.0.1:8000/health/live`、`http://127.0.0.1:8000/health/ready`。

5. 运行测试（默认包含真实模型测试，会产生外部请求与费用）：

   ```bash
   uv run pytest                      # 全部测试
   uv run pytest -m "not real_model"  # 仅离线测试
   ```

6. 静态检查：

   ```bash
   uv run ruff format --check .
   uv run ruff check .
   ```

## 设计决策

见 `docs/adr/`：模型接口与 HTML 正文提取库的选择及理由。
