# AI Learning Agent V3.3.2 发布检查清单

> 版本：3.3.2
> 更新日期：2026-10-27
> 范围：真实环境验收、飞书签名修复、安全边界加固和 Docker 收口

## 1. 发布范围

- 飞书自定义机器人签名算法修正；
- 外部文档降为非 system 的不可信数据；
- 知识库写工具增加当前用户意图校验；
- 入库 URL 复用文档 URL 安全策略；
- 工具参数递归脱敏；
- 最终结果落库失败时正确结束 Run；
- 每日任务脚本兼容 `DIGEST_TRIGGER=manual`；
- 完成真实模型、RSS、Feishu、RAG、Docker、重启和备份恢复验收。

## 2. 自动化检查

```powershell
uv run pytest -m "not real_model" -q
uv run ruff check .
uv run ruff format --check .
uv lock --check
node --check src/learning_agent/web/safe-url.js
node --check src/learning_agent/web/app.js
node --check src/learning_agent/web/study.js
node --test tests/frontend/test_safe_url.js
git diff --check
```

验收标准：

- 266 个离线测试通过；12 个真实模型测试按需执行。
- Ruff Lint 与格式检查通过。
- 锁文件与 `pyproject.toml` 一致。
- 前端脚本语法和安全 URL 单测通过。
- Git diff 无空白错误和临时调试内容。

## 3. 真实环境验收

- [x] 真实模型读取 FastAPI 官方文档并返回可校验引用。
- [x] 真实 RAG 异步入库、完成通知、检索和引用。
- [x] 真实 RSS 生成 Digest。
- [x] 真实飞书接收测试消息和 Digest。
- [x] 飞书签名校验成功，投递状态为 `delivered`。
- [x] Digest 重复执行保持同一 ID。
- [x] Agent 重启后对话和 Digest 保持。
- [x] SQLite 备份恢复完整且行数一致。
- [x] Docker 镜像构建和 `/health/ready` 通过。
- [x] Docker Agent 可访问宿主 RAG。
- [x] Docker 容器重启后 SQLite 数据保留。
- [x] 每日任务脚本生成 Digest、等待完成并关闭 Compose。

详细结果见 [验收与演练记录](verification-log.md)。

## 4. Docker 发布

```powershell
docker compose build
docker compose up -d
curl.exe http://127.0.0.1:8000/health/ready
docker compose down
```

要求：

- Compose 仅把 `8000` 映射到回环地址；
- 同机 RAG 默认通过 `host.docker.internal:8001` 访问；
- 容器以非 root 用户运行；
- 数据保存在 `./data`，停止容器不得删除数据卷；
- 每日任务脚本结束后关闭本项目 Compose 服务。

## 5. 数据库与回滚

- 升级前备份：

```powershell
uv run python -m scripts.backup_database `
  --output backups/app-v3.3.2-before.db
```

- 迁移版本：`0006_v32_learning_loop`
- 已验证从空数据库和旧数据库执行迁移。
- 回滚前停止服务并备份当前数据库，再使用升级前备份恢复。
- 不执行不可逆清库操作；画像清空和主题删除只能由用户主动触发。

## 6. 已知边界

- RAG 仍是单总库，`default` collection 不带主题隔离。
- Digest 使用 RSS/Atom 自带摘要，不调用模型批量摘要。
- 笔记、自测题和闪卡使用确定性规则生成。
- 学习画像只保存和展示，不注入模型上下文。
- 应用与 RAG 没有应用级认证，只允许回环地址或受信网络。
- 后台调度只支持单实例，不使用多 worker。
