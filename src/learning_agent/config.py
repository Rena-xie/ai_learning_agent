"""集中式配置：从环境变量与 ``.env`` 读取并做类型/范围校验。

设计要点：
* 所有资源限制集中在此处，避免同一限制值散落在多个文件。
* 启动时校验必需配置，缺失时抛出 ``ConfigurationError`` 并拒绝不完整启动。
* 环境变量原样值不返回给 API 用户；日志只输出配置项的布尔/数值状态。
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from learning_agent.domain.errors import ConfigurationError

_DEFAULT_ALLOWED_PORTS = (80, 443)


class Settings(BaseSettings):
    """应用配置。字段名即环境变量名（大小写不敏感）。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        protected_namespaces=(),
    )

    # ---------- 应用 ----------
    app_env: str = "local"
    app_host: str = "127.0.0.1"
    app_port: int = Field(default=8000, ge=1, le=65535)
    log_level: str = "INFO"

    # ---------- 数据 ----------
    database_url: str = "sqlite:///./data/app.db"

    # ---------- 模型 ----------
    model_api_key: str = ""
    model_name: str = ""
    model_base_url: str = ""
    model_timeout_seconds: float = Field(default=60.0, gt=0, le=600)
    model_max_retries: int = Field(default=2, ge=0, le=5)
    model_max_output_tokens: int = Field(default=2000, ge=64, le=32000)
    # 采样温度；None 表示不发送该参数，使用服务商默认值
    model_temperature: float | None = Field(default=None, ge=0, le=2)
    # 成本估算单价（元/千 token）；默认 0 表示不计算成本
    model_price_input_per_1k: float = Field(default=0.0, ge=0)
    model_price_output_per_1k: float = Field(default=0.0, ge=0)

    # ---------- 文档白名单与获取 ----------
    allowed_document_hosts: Annotated[tuple[str, ...], NoDecode] = ()
    allowed_document_ports: Annotated[tuple[int, ...], NoDecode] = (
        _DEFAULT_ALLOWED_PORTS
    )
    fetch_timeout_seconds: float = Field(default=15.0, gt=0, le=120)
    max_redirects: int = Field(default=5, ge=0, le=10)
    max_document_bytes: int = Field(default=2_000_000, ge=1_000, le=20_000_000)
    max_document_chars: int = Field(default=60_000, ge=1_000, le=1_000_000)

    # ---------- 知识库（v2 M2） ----------
    knowledge_base_enabled: bool = True
    knowledge_base_url: str = "http://127.0.0.1:8001"
    knowledge_base_connect_timeout_seconds: float = Field(default=3.0, gt=0, le=30)
    knowledge_base_read_timeout_seconds: float = Field(default=10.0, gt=0, le=120)
    knowledge_base_max_urls: int = Field(default=20, ge=1, le=100)
    ingestion_poll_interval_s: float = Field(default=2.0, gt=0, le=60)
    ingestion_poll_max_interval_s: float = Field(default=12.0, gt=0, le=300)
    ingestion_watch_timeout_s: float = Field(default=1800.0, gt=0, le=86_400)
    ingestion_max_active_watches: int = Field(default=20, ge=1, le=200)

    # ---------- 单次 Run 限制 ----------
    run_timeout_seconds: float = Field(default=120.0, gt=0, le=1800)
    max_model_rounds: int = Field(default=6, ge=1, le=20)
    max_tool_calls: int = Field(default=5, ge=1, le=10)
    max_tool_result_chars: int = Field(default=20_000, ge=1_000, le=200_000)

    # ---------- 上下文与输入 ----------
    context_max_tokens: int = Field(default=24_000, ge=1_000, le=200_000)
    #: 上下文 token 估算的安全系数：估算值乘以该系数，缓解低估导致超出模型上下文
    context_token_safety_factor: float = Field(default=1.15, ge=1.0, le=2.0)
    max_history_messages: int = Field(default=20, ge=0, le=200)
    max_input_chars: int = Field(default=4_000, ge=1, le=100_000)

    @field_validator("allowed_document_hosts", "allowed_document_ports", mode="before")
    @classmethod
    def _split_list(cls, value: object) -> object:
        """允许以逗号分隔的字符串形式提供列表型配置。"""

        if value is None or value == "":
            return ()
        if isinstance(value, str):
            return tuple(item.strip() for item in value.split(",") if item.strip())
        return value

    @field_validator("allowed_document_hosts")
    @classmethod
    def _normalize_hosts(cls, hosts: tuple[str, ...]) -> tuple[str, ...]:
        normalized: list[str] = []
        for host in hosts:
            candidate = host.strip().lower().rstrip(".")
            if not candidate:
                continue
            if any(ch in candidate for ch in "*/:@? ,"):
                raise ValueError(f"非法主机名，不接受通配符/端口/协议：{host}")
            normalized.append(candidate)
        return tuple(dict.fromkeys(normalized))

    @field_validator(
        "model_api_key",
        "model_name",
        "model_base_url",
        "knowledge_base_url",
        "log_level",
    )
    @classmethod
    def _strip_text(cls, value: str) -> str:
        return value.strip()

    @property
    def allowed_hosts_set(self) -> frozenset[str]:
        return frozenset(self.allowed_document_hosts)

    @property
    def allowed_ports_set(self) -> frozenset[int]:
        return frozenset(self.allowed_document_ports)

    def require_model_credentials(self) -> None:
        """校验模型配置完整性；缺失时停止启动。"""

        missing = [
            name
            for name, value in (
                ("MODEL_API_KEY", self.model_api_key),
                ("MODEL_NAME", self.model_name),
                ("MODEL_BASE_URL", self.model_base_url),
            )
            if not value
        ]
        if missing:
            raise ConfigurationError(
                "模型配置缺失，应用无法启动：" + ", ".join(missing),
                detail="missing_settings=" + ",".join(missing),
            )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """返回进程内缓存的配置对象。"""

    return Settings()
