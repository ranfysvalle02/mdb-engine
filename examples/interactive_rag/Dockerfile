# ============================================================================
# Multi-stage Dockerfile for MDB_RUNTIME Interactive RAG Example
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

# Install mdb_runtime with all dependencies
RUN     echo "=== Installing mdb-runtime ===" && \
    pip install --no-cache-dir .[llm] && \
    echo "=== Installing web dependencies ===" && \
    pip install --no-cache-dir uvicorn[standard] jinja2 python-multipart websockets && \
    echo "=== Installing LLM dependencies ===" && \
    pip install --no-cache-dir instructor pydantic pydantic-settings tenacity semantic-text-splitter && \
    echo "=== Installing RAG dependencies ===" && \
    pip install --no-cache-dir ddgs docling requests && \
    echo "=== Installation complete ===" && \
    pip show mdb-runtime

# Verify installation worked in builder stage
RUN echo "=== Verifying installation in builder ===" && \
    python -c "import mdb_runtime; print(f'✓ mdb_runtime {mdb_runtime.__version__} imported successfully')" && \
    python -c "from mdb_runtime import RuntimeEngine; print('✓ RuntimeEngine imported successfully')" && \
    python -c "from mdb_runtime.embeddings import EmbeddingService; print('✓ EmbeddingService imported successfully')" && \
    echo "=== Verification complete ==="

# Stage 2: Runtime - Minimal production image
FROM python:3.11-slim

# Install system dependencies for docling/OpenCV
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

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
COPY --chown=${APP_USER}:${APP_USER} examples/interactive_rag/manifest.json ./
COPY --chown=${APP_USER}:${APP_USER} examples/interactive_rag/web.py ./
COPY --chown=${APP_USER}:${APP_USER} examples/interactive_rag/templates ./templates
COPY --chown=${APP_USER}:${APP_USER} examples/interactive_rag/static ./static

# Create cache directories with proper permissions before switching user
RUN mkdir -p /app/.cache /app/cache && \
    chown -R ${APP_USER}:${APP_USER} /app/.cache /app/cache

# Set environment variables
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HF_HOME=/app/.cache/huggingface \
    TRANSFORMERS_CACHE=/app/.cache/huggingface \
    HF_DATASETS_CACHE=/app/.cache/huggingface

# Verify mdb_runtime is installed and importable before switching user
RUN echo "=== Verifying installation in runtime stage ===" && \
    pip show mdb-runtime || (echo "ERROR: mdb-runtime package not found" && exit 1) && \
    python -c "import mdb_runtime; print(f'✓ mdb_runtime {mdb_runtime.__version__} imported successfully')" && \
    python -c "from mdb_runtime import RuntimeEngine; print('✓ RuntimeEngine imported successfully')" && \
    python -c "from mdb_runtime.embeddings import EmbeddingService; print('✓ EmbeddingService imported successfully')" && \
    echo "=== Verification complete ==="

# Switch to non-root user
USER ${APP_USER}

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD python -c "import sys; sys.exit(0)" || exit 1

# Default command - run web server
CMD ["uvicorn", "web:app", "--host", "0.0.0.0", "--port", "5001", "--ws", "auto"]

