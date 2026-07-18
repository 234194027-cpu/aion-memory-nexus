# ===== Stage 1: builder =====
FROM python:3.11-slim AS builder

WORKDIR /app

# 使用阿里云 Debian 镜像 + pip 镜像加速
RUN sed -i 's|deb.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list.d/debian.sources 2>/dev/null; \
    sed -i 's|deb.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list 2>/dev/null; \
    true

# 安装编译依赖（仅构建阶段需要）
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# 先复制依赖文件，利用 Docker 缓存层
# gunicorn 已在 requirements.txt 中，无需重复安装
COPY requirements.txt .
RUN pip install --no-cache-dir -i https://mirrors.aliyun.com/pypi/simple/ -r requirements.txt

# ===== Stage 2: runtime =====
FROM python:3.11-slim

# Build metadata injected via --build-arg (CI / docker-compose)
# Allows About API to surface commit/time without leaking paths or host info
ARG BUILD_COMMIT=unknown
ARG BUILD_TIME=unknown
ENV BUILD_COMMIT=$BUILD_COMMIT
ENV BUILD_TIME=$BUILD_TIME
LABEL org.opencontainers.image.revision=$BUILD_COMMIT
LABEL org.opencontainers.image.created=$BUILD_TIME

WORKDIR /app

# 使用阿里云 Debian 镜像（运行时安装 curl / libpq5 需要 apt 源）
RUN sed -i 's|deb.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list.d/debian.sources 2>/dev/null; \
    sed -i 's|deb.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list 2>/dev/null; \
    true

# 安装运行时依赖：curl (healthcheck)、libpq5 (psycopg 运行时)
# 不包含 gcc / libpq-dev 等编译工具
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl libpq5 \
    && rm -rf /var/lib/apt/lists/*

# 从 builder 复制已安装的 Python 包和可执行入口
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# 复制应用代码
COPY src/ /app/src/
COPY migrations/ /app/migrations/
COPY scripts/rotate_secret_key.py /app/scripts/rotate_secret_key.py
COPY alembic.ini /app/alembic.ini
COPY VERSION /app/VERSION
COPY docs/releases/ /app/docs/releases/

# 复制已验证的前端静态发布物
COPY static/ /app/static/

# 创建非 root 用户，并确保 /app 及挂载点目录权限
RUN useradd -m -d /home/appuser appuser && \
    mkdir -p /app/data/media-artifacts /app/data/agent-workspaces && \
    chown -R appuser:appuser /app
USER appuser

# 创建启动脚本：迁移成功后再启动
RUN echo '#!/bin/sh\n\
set -e\n\
echo "Running database migrations..."\n\
alembic upgrade head\n\
echo "Starting Gunicorn..."\n\
exec gunicorn src.main:app \\\n\
  -w ${WEB_CONCURRENCY:-1} \\\n\
  -k uvicorn.workers.UvicornWorker \\\n\
  -b 0.0.0.0:8000 \\\n\
  --timeout 120 \\\n\
  --access-logfile - \\\n\
  --error-logfile -\n' > /app/start.sh && chmod +x /app/start.sh

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["/app/start.sh"]
