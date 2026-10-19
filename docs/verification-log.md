# 验收与演练记录（v1.0）

记录实际执行过的验证命令与结果，作为 v1.0 验收清单的证据。

## 本地运行

| 项目 | 命令 | 结果 |
|---|---|---|
| 启动 | `uv run uvicorn --factory learning_agent.main:create_app --host 127.0.0.1 --port 8000` | `/health/live` 200；`/health/ready` 返回 `ok`（`database_ok=true`、`model_configured=true`） |
| 界面 | 浏览器打开 `http://127.0.0.1:8000/` | 对话列表/消息区/输入框/状态/来源链接可用，删除有确认 |

## 测试与静态检查

| 项目 | 命令 | 结果 |
|---|---|---|
| 静态检查 | `uv run ruff format --check .` / `uv run ruff check .` | 通过 |
| 离线测试 | `uv run pytest -m "not real_model"` | 148 passed |
| 真实模型测试 | `uv run pytest -m real_model` | 12 passed（含真实公网文档） |
| 全量测试 | `uv run pytest` | 160 passed |

## 评估

| 项目 | 命令 | 结果 |
|---|---|---|
| 评估基线 | `uv run python -m evals.run_eval --save-baseline evals/results/baseline-20261013.json` | 12/12 案例通过（真实模型，产生费用） |
| 回归比较 | `uv run python -m evals.run_eval --baseline evals/results/baseline-20261013.json` | 无回归时退出码 0；出现「基线通过、本次失败」时列出案例并返回 1 |

## 数据管理

| 项目 | 命令 | 结果 |
|---|---|---|
| 迁移（旧库） | `uv run python -m scripts.upgrade_database` | 阶段 1 旧库从 `legacy` 升级到 `0001_initial_schema`，数据保留 |
| 备份 | `uv run python -m scripts.backup_database --output backups/app.db` | 完整性校验 `ok`，输出各表行数 |
| 恢复 | `uv run python -m scripts.restore_database --backup backups/app.db --target data/restored.db` | 恢复后行数与备份一致，应用可读取恢复后的对话与来源 |

## Docker 部署

**状态：已完成**（2026-10-13，本机 Docker Desktop + 镜像站基础镜像）。

```bash
docker compose build --build-arg PYTHON_IMAGE=docker.m.daocloud.io/library/python:3.12-slim
docker compose up -d
curl http://127.0.0.1:8000/health/ready
docker compose restart
docker compose down
```

| 项目 | 结果 |
|---|---|
| 构建 | 镜像 `ai-learning-agent:local` 构建成功（766MB）；基础镜像通过 build arg 指向镜像站，Dockerfile 默认仍是官方 `python:3.12-slim` |
| 启动 | 容器 `Up (healthy)`；`/health/ready` 返回 `{"status":"ok","database_ok":true,"model_configured":true}` |
| 容器内真实问答 | 提交真实文档 URL → `status=completed`、1 个来源、回答含 `[S1]`、运行 `completed`（工具 `fetch_document/succeeded`、tokens 2106+754） |
| 重启持久化 | `docker compose restart` 后历史消息（user, assistant）与来源完整保留，说明 `./data` 卷生效 |
| 备份/恢复 | 对容器使用的数据文件执行备份→恢复演练：完整性 `ok`，行数一致（对话 3、消息 8、运行 4、工具调用 3、快照 2、来源关联 2） |

环境说明：本机 Docker 原配置的代理 `10.60.18.32:7897` 不可用（正确地址为 `127.0.0.1:7897`），
且 `auth.docker.io` 存在 DNS 污染；改用镜像站基础镜像后构建成功。在中国网络环境下建议：
代理指向 `http://127.0.0.1:7897`，或在 Docker Engine 配置 `registry-mirrors`。

## v1.0 验收清单对照（摘要）

| 验收项 | 证据 |
|---|---|
| 获取并提取文档、基于内容解释核心概念 | `tests/integration/test_runtime_real_model.py`、`tests/e2e/test_api_flow.py`（真实模型 + 真实文档） |
| 回答包含真实来源、引用经服务端校验 | `agent/citation_validator.py`、`tests/unit/test_citation_validator.py` |
| 失败如实报告 | `evals` 案例 `disallowed_domain`/`empty_body`/`fetch_timeout`/`model_failure` |
| 对话创建/切换/删除、重启恢复、对话隔离 | `tests/integration/test_conversations_api.py`、`test_restart_recovery.py`、`test_startup_recovery.py` |
| 快照复用与上下文预算 | `test_follow_up_reuses_snapshot_without_refetching`、`tests/unit/test_context_builder.py` |
| 工具校验、资源上限、故障处理 | `tests/unit/test_tool_registry.py`、`tests/integration/test_runtime_limits.py`、`test_model_failures.py` |
| 安全（白名单、SSRF、注入、密钥与日志） | `tests/unit/test_url_policy.py`、`tests/integration/test_prompt_injection.py`、`test_security_boundaries.py`、`test_document_pipeline.py` |
| 可观测性与评估 | `GET /api/v1/stats`、`tests/integration/test_run_stats.py`、`evals/` 与基线结果 |
| 迁移、备份与恢复 | `tests/integration/test_migrations.py`、`test_backup_restore.py` 与上文演练记录 |

## v1.1 工程优化（阶段 6）

**状态：已完成；2026-10-18 在本机补跑离线测试与静态检查。**

改造初期在「无外网、且项目 `.venv` 为 Windows 构建（Linux 沙箱不可用）」的环境中完成，
无法执行 `uv run pytest` 与 `uv run ruff`。为此采用了如下**替代验证**（均已实际执行）：

| 项目 | 手段 | 结果 |
|---|---|---|
| 语法 | `python -m compileall src tests evals` | 通过 |
| 异步调用一致性 | 自建 AST 静态检查：异步路径中的仓储调用必须被 `await` | 21 处仓储调用全部已 await，无遗漏 |
| 上下文预算 | 离线脚本直接运行 `context_builder` | 安全系数生效、中文比例更保守、既有裁剪行为不变 |
| 引用校验 | 离线脚本直接运行 `citation_validator` | 有效/无效标记、Markdown 链接与裸 URL 清理均符合预期 |
| 成本估算 | 离线脚本直接运行 `domain.entities.estimate_cost` | 定价计算与「缺省为空」均符合预期 |
| 评估回归判定 | 离线脚本直接运行 `evals.regression.find_regressions` | 逐案例回归与整体通过率下降均能检出，且无误报 |
| 前端脚本 | `node --check app.js` + 行内标记分词脚本 | 语法与分词逻辑通过 |

### 本机补跑（2026-10-18）

项目目录改名后旧 `.venv` 的基础解释器路径失效，按约定删除并重新执行 `uv sync` 重建环境。

| 项目 | 命令 | 结果 |
|---|---|---|
| 格式检查 | `uv run ruff format --check .` | 通过（顺带格式化 7 个历史文件） |
| 静态检查 | `uv run ruff check .` | 通过（修复 2 处历史问题：`SIM105`、`E501`） |
| 离线测试 | `uv run pytest -m "not real_model"` | 174 passed，12 deselected |

补跑中发现并修复一处回归：上下文估算的中文 token 比例由 0.6 提升到 0.9 后，
文档截断仍按固定 1.6 字符/token 计算，导致本可截断容纳的文档被整体丢弃。
现已按文本字符构成与安全系数反推字符上限，并补充回归测试
`test_document_truncation_accounts_for_safety_factor`。

**未重新执行**：真实模型测试（12 个）与评估回归（需要外部 API 与费用）。
v1.0 的真实模型与评估结果保留在本文上方的记录与 `evals/results/baseline-20261013.json`。

### 新增测试（共 26 个测试函数）

新增文件：`tests/integration/test_async_run.py`、`tests/integration/test_repositories_async.py`、
`tests/unit/test_run_scheduler.py`、`tests/unit/test_cost.py`、`tests/unit/test_eval_regression.py`；
补充用例：`test_context_builder.py`、`test_citation_validator.py`、`test_model_failures.py`、
`test_run_stats.py`、`test_conversations_api.py`、`test_runtime_limits.py`。
上述测试已随离线测试套件执行（174 passed）。

## v2 知识库联动（2026-10-19）

**状态：M1–M4 已完成；M5 发布收口进行中。**

| 项目 | 命令 / 来源 | 结果 |
|---|---|---|
| 离线测试 | `uv run pytest -m "not real_model"` | 214 passed，12 deselected |
| 静态检查 | `uv run ruff format --check .` / `uv run ruff check .` | 通过 |
| 真实单链接联调 | README 既有记录 | 已通过（本次未复跑） |
| RAG 侧评测 | README 既有记录 | 108 题：Recall@5 0.8387、引用有效率 1.0、无答案识别率 1.0 |
| 完整链路（入库 → 通知 → 检索 → 引用） | — | 待执行 |
| Docker 双服务 | — | 待执行 |

本轮 M5 代码收口新增：

- 入库通知消息不再进入模型上下文（`tests/integration/test_history_filtering.py`）。
- 知识库 GET / 检索请求有限重试；入库仅在带幂等键时重试。
- 已通知 / 失败的 watch 默认保留 30 天后清理。
- `/health/ready` 返回 `knowledge_base` 状态（ok / unavailable / disabled），
  探活失败不改变整体 ready 判定。
- 版本号 2.0.0 全口径对齐。
- Compose 通过 `host.docker.internal` 访问宿主机 RAG，兼容 Linux `host-gateway`。

**未执行**：12 个真实模型测试、完整 Agent→RAG 链路、容器构建与两套数据备份恢复演练。
