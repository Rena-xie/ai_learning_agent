# ADR 0005：运行异步执行与异步数据访问

- 状态：已接受
- 日期：2026-10-15

## 背景

v1.0 的一次 Agent Run 完全在 HTTP 请求内同步完成（最长 `RUN_TIMEOUT_SECONDS=120`），
且数据库访问使用同步 SQLAlchemy Session 直接在事件循环中调用。这带来两个问题：

1. 长请求容易被浏览器、反向代理或中间层超时切断，前端全程只能显示一句「正在处理」，
   没有任何进度反馈；
2. 同步阻塞调用会卡住事件循环，并发吞吐受限；一次 Run 还会产生 8 次以上独立事务提交，
   并让数据库出现「运行已 running 但用户消息尚未写入」等中间态窗口。

## 决策

1. **分离「占位」与「执行」**：`AgentRuntime.run` 拆为 `begin`（同步写入运行记录与用户消息，
   已有活动运行时立即抛 `RUN_IN_PROGRESS`）与 `resume`（后台执行模型/工具循环并写入最终结果）。
2. **新增异步提交端点**：消息接口支持 `?mode=async`，立即返回 202 与 `run_id`；
   默认 `mode=sync` 保持既有同步语义，评估脚本与端到端测试不受影响。
3. **后台任务由 `RunScheduler` 持有**：应用关闭（lifespan）时等待在途运行结束，
   避免出现「Task was destroyed but it is pending」。
4. **异步数据库门面**：新增 `AsyncRepositories`，把同步 `Repositories` 的调用统一移入
   `starlette.concurrency.run_in_threadpool`；同步 `Repositories` 保持不变，
   继续供脚本、Alembic 迁移与测试使用。一次 Run 的写入合并为 `start_run` / `complete_run`
   两个事务，减少中间态与数据库往返。
5. **SQLite 连接级 PRAGMA**：`foreign_keys=ON`、`journal_mode=WAL`、`busy_timeout=5000`，
   缓解并发写锁竞争。
6. **前端轮询**：界面改为异步提交后轮询 `GET /api/v1/runs/{run_id}` 直到终态。

## 备选方案

- **全面改用异步 ORM**（`aiosqlite` + `AsyncSession`）：真异步、连接池语义更清晰，但需要新增
  运行时依赖，并改动全部仓储方法、迁移入口与脚本，改动面大；留作后续可选升级。
- **直接把同步端点替换为异步**：会破坏评估脚本与 e2e 的既有调用方式，v1.1 不采用。
- **引入 Celery 等外部任务队列**：对单实例本地应用属于过度设计，不符合最小可靠方案原则。

## 影响

- 客户端使用异步模式时需要自行轮询运行状态；同步模式依旧可用。
- 后台运行依赖进程存活：进程被中断的运行由启动清理（`fail_stale_runs`）标记为失败，
  不会永久占用「单对话单活动运行」约束。
- `run_in_threadpool` 使用 AnyIO 线程池；SQLite 写操作仍然串行，符合单实例定位。
- 若未来需要多进程/多实例部署，需要重新评估任务调度与并发约束（当前未支持）。
