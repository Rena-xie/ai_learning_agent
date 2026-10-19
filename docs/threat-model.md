# 威胁模型（v1.0 阶段 3）

适用范围：AI Learning Agent v1.0（单用户、本地运行、单 Agent Runtime）。
默认只绑定 `127.0.0.1`；远程访问必须经过认证的 HTTPS 反向代理或 VPN。

## 资产

- 模型访问密钥（`MODEL_API_KEY`）与模型调用额度。
- 对话消息、文档快照与运行记录（SQLite 数据库）。
- 应用所在主机及其可访问的内网/云元数据端点。

## 威胁与缓解（均可对应到代码或测试）

| 威胁 | 缓解措施 | 证据 |
|---|---|---|
| SSRF：抓取内网、云元数据或本机地址 | 精确域名白名单（空配置即拒绝）、仅 http/https、拒绝 URL 凭据、端口限制、DNS 解析结果必须全为公网地址 | `tools/document/url_policy.py`；`tests/unit/test_url_policy.py` |
| 重定向把请求带到内网或未允许域名 | 关闭自动重定向，逐跳重新执行完整策略校验，并限制跳数 | `tools/document/fetcher.py`；`tests/integration/test_document_pipeline.py` |
| DNS rebinding：解析与建连之间目标被替换 | 连接建立后读取真实对端地址，非公网或无法确认时按 `ssrf_target_blocked` 拒绝 | 同上（`test_peer_address_check_blocks_local_endpoint`） |
| 提示词注入：文档内嵌指令索取密钥、要求执行命令或越权访问 | 文档一律标注为不可信参考内容；系统提示明确忽略文档内指令；工具静态注册、参数 Schema 校验；来源编号与链接由服务端校验 | `agent/prompts.py`、`agent/tool_registry.py`、`agent/citation_validator.py`；`tests/integration/test_prompt_injection.py` |
| 知识库证据注入：已入库文档包含恶意指令 | 知识库证据与网页正文一样按不可信参考内容处理；来源由服务端登记，模型无法伪造 | `agent/prompts.py`、`tools/knowledge/*`；专项注入测试待补 |
| 恶意工具参数（`file://`、localhost、异常端口、超长参数） | 参数先过 Pydantic Schema，再到 URL 策略；未知工具与非法参数不执行任何副作用 | `tests/unit/test_tool_registry.py`、`tests/integration/test_prompt_injection.py` |
| 密钥泄露到源码、日志或响应 | 密钥仅从环境变量注入；`.env` 被忽略；日志脱敏（URL 查询参数、敏感字段）；错误响应只含安全文案 | `observability/logging.py`、`api/errors.py`；`tests/integration/test_security_boundaries.py` |
| 资源耗尽：超大响应、无限循环、超长上下文 | 响应体积上限（含 Content-Length 预检）、获取/模型/运行超时、最大模型轮数与工具调用次数、上下文 Token 预算与截断标记 | `tools/document/fetcher.py`、`agent/runtime.py`、`agent/context_builder.py`；`tests/integration/test_runtime_limits.py`、`tests/integration/test_model_failures.py` |
| 进程中断导致对话被永久锁定 | 启动时把遗留的 pending/running 运行标记为 `run_interrupted` | `persistence/repositories.py`；`tests/integration/test_startup_recovery.py` |
| 数据越权：猜测对话 ID 读取他人数据 | v1.0 为单用户本地应用，接口只按对话 ID 返回该对话数据；远程访问必须经认证代理/VPN | `api/v1/routes/*`；`tests/integration/test_conversations_api.py` |
| 数据库损坏或误删 | Alembic 管理 Schema；备份/恢复脚本带完整性校验与行数比对 | `scripts/backup_database.py`、`scripts/restore_database.py`；`tests/integration/test_backup_restore.py` |
| 依赖供应链风险 | 依赖版本由 `uv.lock` 锁定；新增依赖需评估维护状态与必要性 | `pyproject.toml`、`uv.lock` |

## 残余风险与部署建议

1. **出口网络限制**：代码校验之外，建议在部署层限制应用只能访问允许的文档域名与模型端点
   （例如 Docker 网络策略或主机防火墙），作为 SSRF 的纵深防御。
2. **远程访问**：不得将无认证服务直接暴露到公网；必须通过认证的 HTTPS 反向代理或 VPN，
   并在代理层限制请求体大小与速率。
3. **模型输出质量**：引用有效性由服务端保证，但「结论是否被来源支持」只能由评估集抽样检查；
   评估通过不等于每条主张都被来源支持，不能把输出当作权威结论。
4. **跨服务鉴权**：RAG 当前没有应用级鉴权；只允许同机或受信内网部署，跨主机时必须增加
   共享 token 或网络隔离。
5. **本地数据库**：备份文件与 `.env` 含敏感信息，需按敏感数据处理，不要提交到版本库。
