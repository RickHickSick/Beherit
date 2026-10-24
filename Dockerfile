# syntax=docker/dockerfile:1
# Official Multi-Architecture Dockerfile for Beherit (linux/amd64 & linux/arm64)
FROM python:3.12-slim-bookworm AS builder

# Install uv from official image
COPY --from=ghcr.io/astral-sh/uv:0.6.14 /uv /bin/uv

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# Copy dependency specifications
COPY pyproject.toml uv.lock ./

# Install project dependencies into virtual environment
RUN uv sync --frozen --no-dev --no-install-project

# Copy source code and build project
COPY src/ ./src/
COPY README.md LICENSE ./
RUN uv sync --frozen --no-dev

# Final production runtime stage
FROM python:3.12-slim-bookworm AS runner

ENV PYTHONUNBUFFERED=1 \
    BEHERIT_HOST=0.0.0.0 \
    BEHERIT_PORT=8650 \
    BEHERIT_DATA_DIR=/app/data \
    BEHERIT_DB_PATH=/app/data/beherit.db \
    PATH="/app/.venv/bin:$PATH"

# Install curl for container health checks and bash for sandboxed tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    bash \
    git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy virtual environment and application from builder
COPY --from=builder /app /app

# Ensure data directory exists
RUN mkdir -p /app/data && chmod 700 /app/data

VOLUME ["/app/data"]

EXPOSE 8650

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8650/api/health || exit 1

ENTRYPOINT ["beherit"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8650"]
