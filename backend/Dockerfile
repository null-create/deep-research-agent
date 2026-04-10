FROM python:3.12-slim

WORKDIR /app

# Set environment variables for Python
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Default log directory for the observability layer.  Overridable via
# the LOG_DIR environment variable in docker-compose or .env.
ENV LOG_DIR=/app/logs

# Tell HuggingFace / sentence-transformers where to cache models inside the
# image so the pre-download step below is reused on rebuild.
ENV TRANSFORMERS_CACHE=/app/.cache/huggingface \
    HF_HOME=/app/.cache/huggingface \
    SENTENCE_TRANSFORMERS_HOME=/app/.cache/torch/sentence_transformers

# Install system dependencies (single RUN layer to keep image smaller)
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    net-tools \
    curl \
    iputils-ping \
    && rm -rf /var/lib/apt/lists/*

# ── PyTorch (CPU-only) ────────────────────────────────────────────────────────
# Install CPU-only PyTorch BEFORE requirements.txt so the much larger CUDA
# build (~1.7 GB) is never pulled.  torch is excluded from requirements.txt
# and installed here instead.
RUN pip install --no-cache-dir \
    torch==2.10.0 \
    --index-url https://download.pytorch.org/whl/cpu

# Copy requirements
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Pre-download embedding model ──────────────────────────────────────────────
# Bake the sentence-transformers model into the image so containers never
# download it from HuggingFace at runtime (avoids cold-start latency and
# unauthenticated HF rate-limit throttling).  Model is ~90 MB.
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"

# Copy application code
COPY backend/ .
# Bundle docs into the image so /project-docs works in the container app.
# Locally the ./docs bind-mount shadows this at runtime.
COPY docs /app/docs

# Create log directories — these are bind-mounted at runtime so dumps
# written inside the container are immediately visible on the host.
RUN mkdir -p /app/logs/sessions


# Expose API port
EXPOSE 9999

# Run the API server
CMD ["uvicorn", "api_server:app", "--host", "0.0.0.0", "--port", "9999"]

