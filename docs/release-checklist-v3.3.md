# AI Learning Agent V3.3 发布检查清单

> 版本：3.3.1
> 更新日期：2026-10-20
> 范围：V3.3.1 安全与发布质量修订

## 1. 发布范围

V3.3.1 不新增业务能力，重点解除安全否决项并恢复发布可信度：

- RSS/Atom 文章链接只允许 HTTP(S)，历史非法条目不进入可见链路。
- Web UI 响应增加 CSP、MIME 嗅探、Referrer 与点击劫持防护。
- 来源添加、来源健康和 Digest 失败只暴露安全分类信息。
- CI 覆盖锁文件、全仓格式、Lint、前端脚本与安全单测。
- 版本号统一为 `3.3.1`。
- 清理未接线的 `DIGEST_MAX_AGE_DAYS` 和公开 `auto_ingest` 字段，未实现能力不再列为当前 v3 任务。

## 2. 自动化检查

发布前执行：

```powershell
uv run pytest -m "not real_model" -q
uv run ruff check src tests migrations
uv run ruff format --check .
uv lock --check
node --check src/learning_agent/web/safe-url.js
node --check src/learning_agent/web/app.js
node --check src/learning_agent/web/study.js
node --test tests/frontend/test_safe_url.js
git diff --check
```

验收标准：

- 251 个离线测试通过；总数中的 12 个真实模型测试单独按需执行。
- Ruff Lint 与格式检查通过。
- 锁文件与 `pyproject.toml` 一致。
- 前端脚本语法与安全 URL 单测通过。
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
- [ ] 恶意或失陷 RSS 中的 `javascript:` 条目不会出现在工作台。
- [ ] `/study.html` 响应包含限制性 CSP，浏览器控制台无 CSP 违规。

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
- RSS 条件请求、robots.txt、全局紧急停止、发送前预览、反馈排序和批量模型摘要不在当前 v3 范围。
- 长期记忆治理、画像自动注入和 LangGraph/LangChain 迁移不在当前 v3 范围。
