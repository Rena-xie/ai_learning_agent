# ADR 0003：SQLite + SQLAlchemy 持久化与 Alembic 迁移策略

- 状态：已接受
- 日期：2026-10-13

## 背景

v1.0 是单用户、单实例、本地运行的应用，需要可测试、可备份、可恢复的持久化方案，
且 Schema 变更必须通过迁移管理，不能依赖手工改库。

## 决策

1. **存储**：SQLite 文件数据库（`DATABASE_URL`，默认 `sqlite:///./data/app.db`），
   通过 SQLAlchemy 2.0 ORM 访问，连接层启用 `PRAGMA foreign_keys=ON` 支持级联删除。
2. **迁移**：Alembic 管理 Schema，迁移脚本位于 `migrations/versions/`；
   `migrations/env.py` 从应用配置读取数据库地址，避免 URL 两处维护。
3. **执行时机**：应用启动时调用 `apply_migrations()` 自动升级到 head；
   `uv run alembic upgrade head` 用于全新空库，`uv run python -m scripts.upgrade_database`
   覆盖所有情况（含旧库）。
4. **旧库升级**：阶段 1 由 `create_all` 建立的库没有版本表（或版本表为空，说明迁移中断），
   这类库先 `stamp head` 再执行升级。该假设仅在旧库结构与初始迁移一致时成立，
   代码与脚本中均有明确说明。
5. **备份与恢复**：使用 SQLite 在线备份 API（`scripts/backup_database.py`）生成带校验的副本，
   `scripts/restore_database.py` 恢复前校验完整性、默认拒绝覆盖已有文件（需 `--force`）。
6. **约束**：`uq_agent_runs_active_conversation`（部分唯一索引）在数据库层保证
   「同一对话同一时间仅一个 pending/running 运行」，不依赖应用层检查。

## 备选方案

- `metadata.create_all` 直接建表：无版本历史、无法演进，已弃用（仅用于测试中模拟旧库）。
- PostgreSQL：单用户本地场景过重，且部署与备份复杂度明显上升。
- 手工 SQL 脚本：缺少版本追踪与回滚能力，容易产生环境漂移。

## 影响

- 应用启动会短暂获取数据库写锁；单实例本地运行可接受。
- `alembic.ini` 必须保持 ASCII（Alembic 以系统 locale 读取该文件）。
- 未来新增迁移时，旧库不再享受 stamp 捷径：必须先确认结构与初始迁移一致。
