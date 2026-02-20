# ============================================================================
# Dockerfile for Vector Hacking API
# ============================================================================

# Stage 1: Builder
FROM python:3.11-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml setup.py MANIFEST.in ./

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

RUN pip install --no-cache-dir --upgrade pip setuptools wheel

COPY mdb_engine ./mdb_engine

RUN pip install --no-cache-dir . && \
    pip install --no-cache-dir uvicorn[standard] python-dotenv && \
    pip install --no-cache-dir numpy instructor pydantic pydantic-settings tenacity

# Stage 2: Runtime
FROM python:3.11-slim

ARG APP_USER=appuser
ARG APP_UID=1000
ARG APP_GID=1000

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd -r -g ${APP_GID} ${APP_USER} && \
    useradd -r -u ${APP_UID} -g ${APP_GID} -d /app -s /bin/bash ${APP_USER}

WORKDIR /app

COPY --from=builder --chown=${APP_USER}:${APP_USER} /opt/venv /opt/venv

# Copy API files
COPY --chown=${APP_USER}:${APP_USER} examples/basic/vector_hacking/api/*.py ./
COPY --chown=${APP_USER}:${APP_USER} examples/basic/vector_hacking/api/manifest.json ./

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

EXPOSE 8000

USER ${APP_USER}

HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
