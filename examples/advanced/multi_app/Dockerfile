# ============================================================================
# Multi-stage Dockerfile for MDB_ENGINE Multi-App Example
# Enterprise-grade best practices: security, caching, minimal image size
# ============================================================================

# Stage 1: Builder - Install dependencies and build the package
FROM python:3.11-slim as builder

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
COPY mdb_engine ./mdb_engine

# Install mdb_engine
RUN echo "=== Installing mdb-engine ===" && \
    pip install --no-cache-dir . && \
    echo "=== Installing web dependencies ===" && \
    pip install --no-cache-dir uvicorn[standard] fastapi jinja2 && \
    echo "=== Installation complete ===" && \
    pip show mdb-engine

# Verify installation worked in builder stage
RUN echo "=== Verifying installation in builder ===" && \
    python -c "import mdb_engine; print(f'✓ mdb_engine imported successfully')" && \
    python -c "from mdb_engine import MongoDBEngine; print('✓ MongoDBEngine imported successfully')" && \
    echo "=== Verification complete ==="

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

# Copy example apps with proper ownership (including templates)
COPY --chown=${APP_USER}:${APP_USER} examples/multi_app/apps ./apps

# Set environment variables
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Verify mdb_engine is installed and importable before switching user
RUN echo "=== Verifying installation in runtime stage ===" && \
    pip show mdb-engine || (echo "ERROR: mdb-engine package not found" && exit 1) && \
    python -c "import mdb_engine; print(f'✓ mdb_engine imported successfully')" && \
    python -c "from mdb_engine import MongoDBEngine; print('✓ MongoDBEngine imported successfully')" && \
    echo "=== Verification complete ==="

# Switch to non-root user
USER ${APP_USER}

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD python -c "import sys; sys.exit(0)" || exit 1

# Default command (overridden by docker-compose)
CMD ["python", "apps/click_tracker/web.py"]

