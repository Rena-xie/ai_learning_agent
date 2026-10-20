# AI Learning Agent V3.3 发布检查清单

> 版本：3.3.0
> 更新日期：2026-10-20
> 范围：V3 日用品与发布收口

## 1. 发布范围

V3.3 不新增核心业务链路，重点完成 V3 的日常可用性和发布收口：

- Web 工作台补齐资讯偏好、Digest 反馈和收藏到 RAG。
- Web 工作台展示来源健康状态，并支持来源启停和新增。
- Web 工作台支持学习画像开关、主题状态、概念掌握度和清空操作。
- 版本号统一为 `3.3.0`。
- README、项目说明和发布检查清单同步当前实现。

## 2. 自动化检查

发布前执行：

```powershell
uv run pytest -q
uv run ruff check src tests migrations
uv run ruff format --check .
uv lock --check
node --check src/learning_agent/web/app.js
node --check src/learning_agent/web/study.js
git diff --check
```

验收标准：

- 239 个全量离线测试通过；12 个真实模型测试单独按需执行。
- Ruff Lint 与格式检查通过。
- 锁文件与 `pyproject.toml` 一致。
- 两个前端脚本语法检查通过。
- Git diff 无空白错误和临时调试内容。

## 3. 本机功能冒烟

以下项目需要在配置好模型、RAG 和飞书后手工确认：

- [ ] 启动应用后 `/health/ready` 返回正常。
- [ ] `/` 对话页面可加载并完成一次普通文档问答。
- [ ] `/study.html` 可加载进度、Digest、笔记和复习区域。
- [ ] 保存资讯主题、来源、每日条数和补发天数后，设置不会回滚。
- [ ] 来源列表显示最近检查时间和最近错误；停用来源后不再参与后续 Digest。
- [ ] 手动运行 Digest 后，工作台可显示今日资讯。
- [ ] Digest 条目可以标记“有用/无用”，刷新后状态仍保留。
- [ ] Digest 条目可以提交到 RAG，页面能显示排队、处理中和完成状态。
- [ ] Digest 条目可以生成带来源笔记、自测题和闪卡。
- [ ] 到期闪卡可以提交复习结果，并更新下一次复习时间。
- [ ] 学习画像可以开关、调整主题状态、校正概念掌握度、删除主题和清空。
- [ ] 重启应用后，偏好、Digest、笔记、闪卡、复习记录和画像仍可读取。

## 4. 数据库与回滚

- 升级前备份：`uv run python -m scripts.backup_database --output backups/app-v3.3-before.db`
- V3.3 新增迁移版本：`0006_v32_learning_loop`
- 已验证从空数据库和旧数据库执行迁移。
- 回滚代码前先停止服务并备份数据库；恢复时使用升级前备份。
- 不执行不可逆的清库操作；学习画像清空和主题删除只能由用户主动触发。

## 5. 已知边界

- RAG 仍是单总库，`default` collection 不带主题过滤。
- 笔记、自测和闪卡由确定性规则生成，尚未调用模型改写。
- 真实飞书、真实 RAG 入库和外部 RSS 的发布验收必须在用户环境中执行。
- V4 的记忆生命周期治理、上下文注入和 LangGraph/LangChain 迁移不在本版本范围。
