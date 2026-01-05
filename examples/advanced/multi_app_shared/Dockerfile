# ============================================================================
# Multi-stage Dockerfile for MDB_ENGINE Multi-App Shared Auth Example
# ============================================================================

FROM python:3.11-slim as builder

WORKDIR /build

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

# Copy dependency files
COPY pyproject.toml setup.py MANIFEST.in ./

# Create virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Upgrade pip and install build tools
RUN pip install --no-cache-dir --upgrade pip setuptools wheel

# Copy and install mdb_engine
COPY mdb_engine ./mdb_engine
RUN pip install --no-cache-dir ".[casbin]" && \
    pip install --no-cache-dir uvicorn[standard] fastapi jinja2 pydantic[email] bcrypt PyJWT python-multipart casbin casbin-motor-adapter

# Verify installation (including dependencies)
RUN python -c "from mdb_engine import MongoDBEngine; print('✓ MongoDBEngine')" && \
    python -c "from mdb_engine.auth import SharedUserPool; print('✓ SharedUserPool')" && \
    python -c "import bcrypt; print('✓ bcrypt')" && \
    python -c "import jwt; print('✓ PyJWT')" && \
    python -c "import casbin; print('✓ Casbin')"

# Runtime stage
FROM python:3.11-slim

ARG APP_USER=appuser
ARG APP_UID=1000
ARG APP_GID=1000

RUN groupadd -r -g ${APP_GID} ${APP_USER} && \
    useradd -r -u ${APP_UID} -g ${APP_GID} -d /app -s /bin/bash ${APP_USER}

WORKDIR /app

COPY --from=builder --chown=${APP_USER}:${APP_USER} /opt/venv /opt/venv

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER ${APP_USER}

HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD python -c "import sys; sys.exit(0)" || exit 1

CMD ["uvicorn", "web:app", "--host", "0.0.0.0", "--port", "8000"]

