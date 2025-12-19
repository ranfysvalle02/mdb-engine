# ============================================================================
# Multi-stage Dockerfile for MDB_RUNTIME Vector Hacking Example
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
COPY mdb_runtime ./mdb_runtime

# Verify package structure before installation
RUN echo "=== Package structure ===" && \
    ls -la && \
    echo "=== mdb_runtime directory ===" && \
    ls -la mdb_runtime/ && \
    echo "=== Checking for __init__.py ===" && \
    test -f mdb_runtime/__init__.py && echo "✓ __init__.py found" || echo "✗ __init__.py missing"

# Install mdb_runtime with all dependencies
# Using regular install (not editable) since we're copying venv to runtime stage
RUN echo "=== Installing mdb-runtime ===" && \
    pip install --no-cache-dir . && \
    echo "=== Installing web dependencies ===" && \
        pip install --no-cache-dir uvicorn[standard] jinja2 python-multipart websockets && \
    echo "=== Installation complete ===" && \
    pip show mdb-runtime

# Verify installation worked in builder stage
RUN echo "=== Verifying installation in builder ===" && \
    python -c "import mdb_runtime; print(f'✓ mdb_runtime {mdb_runtime.__version__} imported successfully')" && \
    python -c "from mdb_runtime import RuntimeEngine; print('✓ RuntimeEngine imported successfully')" && \
    echo "=== Checking installed packages ===" && \
    pip list | grep -i mdb

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
COPY --chown=${APP_USER}:${APP_USER} examples/vector_hacking/manifest.json ./
COPY --chown=${APP_USER}:${APP_USER} examples/vector_hacking/main.py ./
COPY --chown=${APP_USER}:${APP_USER} examples/vector_hacking/web.py ./
    COPY --chown=${APP_USER}:${APP_USER} examples/vector_hacking/vector_hacking.py ./
COPY --chown=${APP_USER}:${APP_USER} examples/vector_hacking/templates ./templates

# Set environment variables
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Verify mdb_runtime is installed and importable before switching user
# (run as root so we can debug if needed)
RUN echo "=== Verifying installation in runtime stage ===" && \
    echo "Python path: $(which python)" && \
    echo "Python version: $(python --version)" && \
    echo "=== Checking if mdb-runtime is installed ===" && \
    pip show mdb-runtime || (echo "ERROR: mdb-runtime package not found" && echo "=== Installed packages ===" && pip list && exit 1) && \
    echo "=== Testing import ===" && \
    python -c "import mdb_runtime; print(f'✓ mdb_runtime {mdb_runtime.__version__} imported successfully')" && \
    python -c "from mdb_runtime import RuntimeEngine; print('✓ RuntimeEngine imported successfully')" && \
    echo "=== Verification complete ==="

# Install vector hacking dependencies
RUN echo "=== Installing vector hacking dependencies ===" && \
    pip install --no-cache-dir numpy litellm instructor pydantic pydantic-settings tenacity && \
    echo "=== Dependencies installed ==="

# Switch to non-root user
USER ${APP_USER}

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD python -c "import sys; sys.exit(0)" || exit 1

# Default command - run web server with WebSocket support
CMD ["uvicorn", "web:app", "--host", "0.0.0.0", "--port", "8000", "--ws", "auto"]

