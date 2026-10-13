# 单阶段镜像：只包含运行时依赖，应用以非 root 用户启动
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    UV_LINK_MODE=copy

WORKDIR /app

# uv：固定版本，避免构建结果漂移
COPY --from=ghcr.io/astral-sh/uv:0.9.7 /uv /uvx /usr/local/bin/

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY alembic.ini ./
COPY migrations ./migrations
COPY src ./src
RUN uv sync --frozen --no-dev \
    && useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/data \
    && chown -R appuser:appuser /app

USER appuser
EXPOSE 8000

# 容器内监听所有接口，对外仅在 compose 中映射到回环地址
ENV APP_HOST=0.0.0.0 APP_PORT=8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=4)"

CMD ["/app/.venv/bin/python", "-m", "uvicorn", "--factory", "learning_agent.main:create_app", "--host", "0.0.0.0", "--port", "8000"]
