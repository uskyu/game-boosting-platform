# =============================================================================
# Backend Dockerfile - Game Boosting Platform
# Base: python:3.10-slim
# =============================================================================

FROM python:3.10-slim AS builder

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --user -r requirements.txt


# =============================================================================
# Production Stage
# =============================================================================

FROM python:3.10-slim AS production

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PATH="/root/.local/bin:$PATH"

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /root/.local /root/.local

# 数据库容灾备份需要 mysqldump；gzip 也显式安装，避免依赖基础镜像的偶然预装。
# 放在 COPY 代码之前，代码改动不会让这一层缓存失效、重复下载 apt 包。
# Debian 的 default-mysql-client 装的是 MariaDB 客户端；显式补一个
# mysqldump 命令名，避免基础镜像/包版本差异导致备份时找不到命令。
RUN apt-get update \
    && apt-get install -y --no-install-recommends default-mysql-client gzip \
    && ln -sf /usr/bin/mariadb-dump /usr/bin/mysqldump \
    && rm -rf /var/lib/apt/lists/*

# Copy application code
COPY . .

# Fix line endings (Windows CRLF → LF) and ensure executable
RUN sed -i 's/\r$//' /app/docker-entrypoint.sh && chmod +x /app/docker-entrypoint.sh

# Create non-root user for security
RUN groupadd -r appgroup && useradd -r -g appgroup appuser \
    && chown -R appuser:appgroup /app

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=5)"

# Run migrations, then start the application
CMD ["/app/docker-entrypoint.sh"]
