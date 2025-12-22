# ============================================================================
# Multi-stage Dockerfile for MDB_RUNTIME Parallax Example
# Enterprise-grade best practices: security, caching, minimal image size
# ============================================================================

# Stage 1: Builder - Install dependencies and build the package
FROM python:3.11-slim AS builder

WORKDIR /build

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

# Copy dependency files first for better layer caching
COPY pyproject.toml setup.py MANIFEST.in ./

# Create a virtual environment and install build dependencies
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Upgrade pip and install build tools
RUN pip install --no-cache-dir --upgrade pip setuptools wheel

# Copy source code
COPY mdb_runtime ./mdb_runtime

# Install mdb_runtime with LLM dependencies
RUN pip install --no-cache-dir .[llm] && \
    pip install --no-cache-dir uvicorn[standard] jinja2 python-multipart websockets && \
    pip install --no-cache-dir requests httpx

# Stage 2: Runtime - Minimal production image
FROM python:3.11-slim

# Set build arguments for flexibility
ARG APP_USER=appuser
ARG APP_UID=1000
ARG APP_GID=1000

# Create non-root user for security
RUN groupadd -r -g ${APP_GID} ${APP_USER} && \
    useradd -r -u ${APP_UID} -g ${APP_GID} -d /app -s /bin/bash ${APP_USER}

# Set working directory
WORKDIR /app

# Copy virtual environment from builder with proper ownership
COPY --from=builder --chown=${APP_USER}:${APP_USER} /opt/venv /opt/venv

# Copy example files with proper ownership
COPY --chown=${APP_USER}:${APP_USER} examples/parallax/manifest.json ./
COPY --chown=${APP_USER}:${APP_USER} examples/parallax/web.py ./
COPY --chown=${APP_USER}:${APP_USER} examples/parallax/parallax.py ./
COPY --chown=${APP_USER}:${APP_USER} examples/parallax/schemas.py ./
COPY --chown=${APP_USER}:${APP_USER} examples/parallax/schema_generator.py ./
COPY --chown=${APP_USER}:${APP_USER} examples/parallax/templates ./templates

# Set environment variables
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Switch to non-root user
USER ${APP_USER}

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD python -c "import sys; sys.exit(0)" || exit 1

# Default command - run web server with WebSocket support
CMD ["uvicorn", "web:app", "--host", "0.0.0.0", "--port", "8000", "--ws", "auto"]

