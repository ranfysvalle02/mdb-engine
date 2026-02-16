# ============================================================================
# Multi-stage Dockerfile for SSO Multi-App
# SSO Authentication Example
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

# Install mdb_engine with Casbin support (required for authorization)
RUN echo "=== Installing mdb-engine with Casbin support ===" && \
    pip install --no-cache-dir ".[casbin]" && \
    echo "=== Installing pymongo encryption support (for CSFLE) ===" && \
    pip install --no-cache-dir "pymongo[encryption]" && \
    echo "=== Installing web dependencies ===" && \
    pip install --no-cache-dir uvicorn[standard] fastapi jinja2 python-multipart && \
    echo "=== Installing app-specific dependencies ===" && \
    pip install --no-cache-dir python-dotenv finvizfinance pandas && \
    echo "=== Installing file processing libraries ===" && \
    pip install --no-cache-dir python-docx pypdf openpyxl && \
    echo "=== Installing AI/LLM dependencies ===" && \
    pip install --no-cache-dir google-genai openai anthropic mem0ai aiohttp litellm semantic-text-splitter && \
    echo "=== Installation complete ===" && \
    pip show mdb-engine && \
    echo "=== Verifying Casbin installation ===" && \
    python -c "import casbin; print('✓ Casbin imported successfully')" && \
    python -c "from casbin_motor_adapter import Adapter; print('✓ Casbin Adapter imported successfully')" && \
    echo "=== Verifying CSFLE support ===" && \
    python -c "from pymongo.encryption_options import AutoEncryptionOpts; print('✓ PyMongo encryption support available')" && \
    echo "=== Verifying Finviz installation ===" && \
    python -c "from finvizfinance.screener.overview import Overview; print('✓ Finviz imported successfully')" || \
    (echo "WARNING: Finviz not available, price lookups will use fallback" && true) && \
    echo "=== Verifying google-genai installation ===" && \
    python -c "from google import genai; print('✓ google-genai imported successfully')" || \
    (echo "ERROR: google-genai not available" && exit 1)

# Verify installation worked in builder stage
RUN echo "=== Verifying installation in builder ===" && \
    python -c "import mdb_engine; print(f'✓ mdb_engine imported successfully')" && \
    python -c "from mdb_engine import MongoDBEngine; print('✓ MongoDBEngine imported successfully')" && \
    python -c "from mdb_engine.auth import SharedUserPool; print('✓ SharedUserPool imported successfully')" && \
    echo "=== Verification complete ==="

# Stage 2: Runtime - Minimal production image
FROM python:3.11-slim

# Set build arguments for flexibility
ARG APP_USER=appuser
ARG APP_UID=1000
ARG APP_GID=1000
ARG MONGODB_CRYPT_VERSION=8.0.0

# Create non-root user for security
RUN groupadd -r -g ${APP_GID} ${APP_USER} && \
    useradd -r -u ${APP_UID} -g ${APP_GID} -d /app -s /bin/bash ${APP_USER}

# Install runtime dependencies including curl for CSFLE library download
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    libssl-dev \
    && rm -rf /var/lib/apt/lists/*

# Download MongoDB crypt_shared library for CSFLE support
RUN mkdir -p /opt/mongodb/lib && \
    ARCH=$(dpkg --print-architecture) && \
    if [ "$ARCH" = "amd64" ]; then ARCH="x86_64"; fi && \
    if [ "$ARCH" = "arm64" ]; then ARCH="aarch64"; fi && \
    echo "Downloading crypt_shared for architecture: $ARCH" && \
    curl -L -o /tmp/mongo_crypt.tgz \
    "https://downloads.mongodb.com/linux/mongo_crypt_shared_v1-linux-${ARCH}-enterprise-ubuntu2204-${MONGODB_CRYPT_VERSION}.tgz" && \
    tar -xzf /tmp/mongo_crypt.tgz -C /tmp && \
    find /tmp -name "mongo_crypt_v1.so" -exec cp {} /opt/mongodb/lib/ \; && \
    chmod 755 /opt/mongodb/lib/mongo_crypt_v1.so && \
    rm -rf /tmp/mongo_crypt* && \
    echo "✓ MongoDB crypt_shared library installed at /opt/mongodb/lib/mongo_crypt_v1.so"

# Set working directory
WORKDIR /app

# Copy virtual environment from builder with proper ownership
COPY --from=builder --chown=${APP_USER}:${APP_USER} /opt/venv /opt/venv

# Copy example apps with proper ownership (including templates)
COPY --chown=${APP_USER}:${APP_USER} examples/advanced/sso-multi-app/apps ./apps

# Copy entrypoint script for CSFLE key management
COPY --chown=${APP_USER}:${APP_USER} examples/advanced/sso-multi-app/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh
# Copy CSFLE validation script
COPY --chown=${APP_USER}:${APP_USER} scripts/validate_csfle.py /app/scripts/validate_csfle.py
RUN chmod +x /app/scripts/validate_csfle.py

# Create directory for CSFLE key persistence (can be mounted as volume)
RUN mkdir -p /data/csfle && chown ${APP_USER}:${APP_USER} /data/csfle

# Set environment variables
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    CRYPT_SHARED_LIB_PATH=/opt/mongodb/lib/mongo_crypt_v1.so

# Verify mdb_engine is installed and importable before switching user
RUN echo "=== Verifying installation in runtime stage ===" && \
    pip show mdb-engine || (echo "ERROR: mdb-engine package not found" && exit 1) && \
    python -c "import mdb_engine; print(f'✓ mdb_engine imported successfully')" && \
    python -c "from mdb_engine import MongoDBEngine; print('✓ MongoDBEngine imported successfully')" && \
    python -c "from mdb_engine.auth import SharedUserPool; print('✓ SharedUserPool imported successfully')" && \
    echo "=== Verifying Casbin dependencies ===" && \
    python -c "import casbin; print('✓ Casbin imported successfully')" && \
    python -c "from casbin_motor_adapter import Adapter; print('✓ Casbin Adapter imported successfully')" && \
    echo "=== Verifying CSFLE support ===" && \
    python -c "from pymongo.encryption_options import AutoEncryptionOpts; print('✓ PyMongo encryption support available')" && \
    python -c "from mdb_engine.core.csfle import is_csfle_available; print(f'✓ CSFLE available: {is_csfle_available()}')" && \
    test -f /opt/mongodb/lib/mongo_crypt_v1.so && echo "✓ crypt_shared library found at /opt/mongodb/lib/mongo_crypt_v1.so" && \
    echo "=== Verifying google-genai installation ===" && \
    python -c "from google import genai; print('✓ google-genai imported successfully')" && \
    python -c "from google.genai import types; print('✓ google.genai.types imported successfully')" && \
    echo "=== Verifying file processing libraries ===" && \
    python -c "import docx; print('✓ python-docx imported successfully')" && \
    python -c "from pypdf import PdfReader; print('✓ pypdf imported successfully')" && \
    python -c "import openpyxl; print('✓ openpyxl imported successfully')" && \
    python -c "import pandas as pd; print('✓ pandas imported successfully')" && \
    echo "=== Verification complete ==="

# Switch to non-root user
USER ${APP_USER}

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD python -c "import sys; sys.exit(0)" || exit 1

# Default command (overridden by docker-compose)
CMD ["python", "apps/auth-hub/web.py"]
