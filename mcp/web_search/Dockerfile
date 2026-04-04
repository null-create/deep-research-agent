FROM python:3.12-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
  PYTHONUNBUFFERED=1 \
  PIP_NO_CACHE_DIR=1 \
  PIP_DISABLE_PIP_VERSION_CHECK=1 \
  PYTHONPATH=/app \
  LOG_LEVEL=INFO \
  ENVIRONMENT=local \
  TIMEOUT=30 \
  RELOAD=false

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
  # Build tools
  build-essential \
  gcc \
  # Network tools for health checks
  curl \
  netcat-openbsd \
  # Process management
  procps \
  # Certificate management
  ca-certificates \
  # Clean up
  && apt-get clean \
  && rm -rf /var/lib/apt/lists/* \
  && rm -rf /tmp/* \
  && rm -rf /var/tmp/*

# Create non-root user early
RUN useradd --create-home --shell /bin/bash --uid 1000 mcpuser

# Set working directory
WORKDIR /app

# Copy requirements first for better layer caching
COPY requirements.txt /app/requirements.txt

# Install Python dependencies
RUN pip install --upgrade pip \
  && pip install -r requirements.txt

# Copy application files with proper ownership
COPY --chown=mcpuser:mcpuser .env /app
COPY --chown=mcpuser:mcpuser main.py /app
COPY --chown=mcpuser:mcpuser search_backends.py /app

# Create directories for logs and data
RUN mkdir -p /app/logs /app/data /app/tmp \
  && chown -R mcpuser:mcpuser /app \
  && chmod -R 755 /app

# Switch to non-root user
USER mcpuser

# Expose port for HTTP server mode
EXPOSE 9393

# Set entrypoint
CMD ["python", "/app/main.py"]