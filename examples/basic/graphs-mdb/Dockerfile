# ============================================================================
# Multi-stage Dockerfile for MDB_ENGINE Graphs MDB Example
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
COPY pyproject.toml MANIFEST.in ./

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
    pip install --no-cache-dir uvicorn[standard] python-multipart jinja2 python-dotenv && \
    echo "=== Installation complete ===" && \
    pip show mdb-engine

# Stage 2: Runtime - Minimal production image
FROM python:3.11-slim

# Install curl for healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
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
COPY --chown=${APP_USER}:${APP_USER} examples/basic/graphs-mdb/manifest.json ./
COPY --chown=${APP_USER}:${APP_USER} examples/basic/graphs-mdb/web.py ./
COPY --chown=${APP_USER}:${APP_USER} examples/basic/graphs-mdb/templates ./templates

# Set environment variables
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Switch to non-root user
USER ${APP_USER}

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Default command - run web server
CMD ["uvicorn", "web:app", "--host", "0.0.0.0", "--port", "8000"]
