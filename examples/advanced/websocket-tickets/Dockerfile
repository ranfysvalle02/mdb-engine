FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
# Build context is project root, so paths are relative to that
COPY examples/advanced/websocket-tickets/requirements.txt /app/requirements.txt

# Install Python dependencies
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copy the entire mdb-engine project (needed for local development)
# Build context is project root, so we copy everything
COPY . /app/mdb-engine

# Install mdb-engine in development mode
RUN pip install --no-cache-dir -e /app/mdb-engine

# Copy example app files
COPY examples/advanced/websocket-tickets/apps /app/apps

# Set working directory to apps folder
WORKDIR /app

# Expose port
EXPOSE 8000

# Default command (can be overridden in docker-compose.yml)
CMD ["uvicorn", "apps.multi_app_main:app", "--host", "0.0.0.0", "--port", "8000"]
