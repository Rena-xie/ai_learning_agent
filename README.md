# AI Learning Agent

本地可运行的 AI 学习助手：提交受支持的官方技术文档 URL，系统获取并解析文档，基于实际获取的内容讲解核心概念，并给出可追溯的真实来源。

需求基线：[项目说明_v1.0.md](项目说明_v1.0.md)；开发规范：[AGENTS.md](AGENTS.md)。

## 当前进度

- **阶段 1（最小可用 Agent 链路）：已完成**。真实模型（DeepSeek `deepseek-v4-flash`）+ 真实公网文档
  （`liaoxuefeng.com`）的 API 端到端测试通过。
- **阶段 2（对话与数据可靠性）：已完成**。对话删除与级联清理、重启恢复、并发提交冲突控制、
  Alembic 迁移、备份与恢复演练、最小聊天界面均已在沙箱内通过测试。
- **阶段 3（安全与故障处理加固）：已完成**。对端地址校验与长度预检、启动清理孤立运行、
  模型故障映射与重试、资源上限与循环终止、提示词注入与恶意参数防护、密钥与日志脱敏核查，
  并补充了[威胁模型](docs/threat-model.md)。
- **阶段 4-5：未实现**。Agent 评估集、Docker Compose、CI 与最终交付文档将在后续阶段完成。

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
  Alembic 管理 Schema（应用启动自动升级，旧库自动 stamp），
  数据库级「同一对话同一时间仅一个活动运行」约束，删除对话级联清理。
- **对话管理**：新建、列表、切换、删除（含确认）；运行进行中拒绝删除并返回 409。
- **最小界面**：`/` 提供对话列表、消息区、输入框、运行状态提示、来源链接与错误提示，
  不展示未实现的控件（无流式、无文件上传）。
- **API**：`GET /health/live`、`GET /health/ready`、`POST/GET /api/v1/conversations`、
  `DELETE /api/v1/conversations/{id}`、对话消息接口、`GET /api/v1/runs/{run_id}`。
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

   启动时会自动执行数据库迁移；全新安装也可以先手动执行 `uv run alembic upgrade head`。

4. 打开界面 `http://127.0.0.1:8000/`，健康检查见
   `http://127.0.0.1:8000/health/live` 与 `http://127.0.0.1:8000/health/ready`。

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

见 `docs/adr/`：模型接口、HTML 正文提取库与持久化/迁移策略的选择及理由。

## 安全说明

安全边界、已实现的缓解措施与残余风险见 [docs/threat-model.md](docs/threat-model.md)。
要点：文档域名白名单（不配置即拒绝抓取）、DNS 与对端地址双重校验、逐跳重定向校验、
工具静态注册与参数校验、来源与链接服务端校验、日志脱敏、完整资源上限，以及
「启动时清理孤立运行」的故障恢复。远程访问必须经认证的 HTTPS 反向代理或 VPN。

## 数据管理（命令均已实际执行验证）

数据库位置由 `DATABASE_URL` 决定（默认 `sqlite:///./data/app.db`）。

```bash
# 升级 Schema（与应用启动时的逻辑一致；旧库会先 stamp 再升级）
uv run python -m scripts.upgrade_database

# 备份（默认输出 backups/app-<时间戳>.db，会做完整性校验与行数统计）
uv run python -m scripts.backup_database --output backups/app.db

# 恢复到新目录
uv run python -m scripts.restore_database --backup backups/app.db --target data/restored.db

# 覆盖现有数据库（先停止应用，再执行）
uv run python -m scripts.restore_database --backup backups/app.db --target data/app.db --force
```

恢复完成后把 `DATABASE_URL` 指向恢复后的文件并启动应用，即可读取恢复的对话、消息与来源。
