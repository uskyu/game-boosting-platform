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
