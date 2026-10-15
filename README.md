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
- **阶段 4（可观测性与评估）：已完成**。运行统计接口、12 个固定评估案例与回归比较框架，
  真实模型基线结果见 `evals/results/`。
- **阶段 5（可重复部署与交付验收）：已完成**。Dockerfile/Compose、CI 工作流、
  [架构说明](docs/architecture.md)与[验收记录](docs/verification-log.md)均已交付；
  容器构建、启动、容器内真实问答、重启持久化与备份恢复演练全部通过。
- **阶段 6（v1.1 工程优化）：代码与文档已完成，完整测试待在本机环境执行**。
  一次运行改为后台执行并新增异步提交端点（`?mode=async`，默认仍为同步）；
  数据库访问异步化并合并单次运行事务；上下文 token 估算引入安全系数并映射超上下文错误；
  文档快照按 URL 与内容哈希去重；落地运行成本估算；引用校验清理裸 URL；
  运行统计改为 SQL 聚合并新增 p95 与成本；界面渲染轻量 Markdown（保持 XSS 安全）。
  决策见 [ADR 0005](docs/adr/0005-async-run-execution.md)，验收状态见
  [verification-log](docs/verification-log.md#v11-工程优化阶段-6)。

## 已实现能力

- **模型适配器**：OpenAI 兼容协议（默认 DeepSeek），工具调用、有限重试、用量解析与统一错误映射；
  可选采样温度（`MODEL_TEMPERATURE`，不设置则用服务商默认值）。
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
  不展示未实现的控件（无流式、无文件上传）。回答按轻量 Markdown 渲染（标题/列表/粗体/
  行内代码，`[S1]` 标记可点击跳转来源），渲染只生成 DOM 节点、不使用 `innerHTML`。
- **运行执行**：默认同步返回最终结果；`?mode=async` 立即返回 202 与 `run_id`，
  运行在后台执行，调用方轮询 `GET /api/v1/runs/{run_id}`，应用关闭时等待在途运行收尾。
- **API**：`GET /health/live`、`GET /health/ready`、`POST/GET /api/v1/conversations`、
  `DELETE /api/v1/conversations/{id}`、对话消息接口（支持 `?mode=async`）、
  `GET /api/v1/runs/{run_id}`、`GET /api/v1/stats`。
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

## 观测与评估

```bash
# 基础运行统计（成功率、耗时均值/p50/p95、Token 用量、成本、工具失败与文档获取成功率）
curl http://127.0.0.1:8000/api/v1/stats

# Agent 评估集（真实模型，产生费用；结果写入 evals/results/last-run.json）
uv run python -m evals.run_eval
uv run python -m evals.run_eval --case url_only              # 单个案例
uv run python -m evals.run_eval --baseline evals/results/baseline-20261013.json
uv run python -m evals.run_eval --save-baseline evals/results/baseline-<日期>.json
```

评估标准与检查项见 `evals/criteria.md`，策略说明见
[ADR 0004](docs/adr/0004-agent-evaluation.md)。默认测试套件**不包含**评估运行（可选付费检查）。
评估运行默认固定 `MODEL_TEMPERATURE=0` 以提升可复现性；`--baseline` 除逐案例回归外，
还会在整体通过率下降时判定回归。

## Docker 部署

```bash
docker compose build          # 构建镜像（python:3.12-slim + 锁定依赖）
docker compose up -d          # 启动（端口仅绑定 127.0.0.1:8000，数据卷 ./data）
curl http://127.0.0.1:8000/health/ready
docker compose down
```

密钥通过 `.env` 注入（`env_file`），不写入镜像；数据库文件落在 `./data` 卷内，
容器重建后数据保留（已实测：重启后历史对话与来源完整保留）。
国内网络可改用镜像站基础镜像：

```bash
docker compose build --build-arg PYTHON_IMAGE=docker.m.daocloud.io/library/python:3.12-slim
```

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
