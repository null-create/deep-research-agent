#!/bin/bash

set -e

echo "Setting up Research Agent..."

# Resolve the directory containing this script so paths work regardless of cwd
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ── Virtual environment ───────────────────────────────────────────────────────
python3 -m venv venv
source venv/bin/activate

# ── Dependencies ──────────────────────────────────────────────────────────────
pip install --upgrade pip

# Install CPU-only PyTorch before requirements.txt to avoid pulling the
# 1.7 GB CUDA build that pip would otherwise resolve.
echo "Installing PyTorch (CPU-only)..."
pip install --no-cache-dir torch==2.10.0 --index-url https://download.pytorch.org/whl/cpu

echo "Installing remaining dependencies..."
pip install --no-cache-dir -r requirements.txt

# ── Directories ───────────────────────────────────────────────────────────────
mkdir -p logs/sessions
mkdir -p data
mkdir -p instructions

# ── Pre-download embedding model ──────────────────────────────────────────────
# Avoids cold-start latency and HuggingFace rate-limit throttling at runtime.
echo "Pre-downloading sentence-transformers model (all-MiniLM-L6-v2)..."
python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"

# ── .env file ─────────────────────────────────────────────────────────────────
if [ ! -f .env ]; then
    echo "Creating .env file..."
    cat > .env << 'EOF'
# ── Agent mode: research | chat | self-optimization
AGENT_MODE=research

# ── Model backend: openai | azure | bedrock | ollama | huggingface | gcp
MODEL_BACKEND=ollama

# ── Logging (20=INFO, 10=DEBUG)
LOG_LEVEL=20

# ── OpenAI
OPENAI_API_KEY=your-openai-api-key
OPENAI_MODEL=gpt-5.2
OPENAI_BASE_URL=https://api.openai.com/v1

# ── Azure OpenAI
AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com
AZURE_OPENAI_API_KEY=your-azure-api-key
AZURE_OPENAI_DEPLOYMENT=your-deployment-name
AZURE_API_VERSION=2024-02-15-preview

# ── AWS Bedrock
AWS_MODEL=global.anthropic.claude-sonnet-4-5-20250929-v1:0
AWS_BASE_URL=
AWS_API_KEY=your-aws-api-key

# ── GCP Vertex AI
GCP_ENDPOINT=https://us-central1-aiplatform.googleapis.com/v1
GCP_MODEL=gemini-2.5-pro
GCP_API_KEY=your-gcp-api-key

# ── Ollama
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=nemotron-3-nano

# ── HuggingFace TGI
HUGGINGFACE_BASE_URL=http://localhost:8080
HUGGINGFACE_MODEL=

# ── MCP Server URLs (use host.docker.internal when running inside Docker)
MEMORY_SERVER_URL=http://localhost:9494/mcp
SEARCH_SERVER_URL=http://localhost:9393/mcp
SCRAPER_SERVER_URL=http://localhost:9292/mcp
FILE_SERVER_URL=http://localhost:9191/mcp

# ── Agent / LLM tuning
MAX_ITERATIONS=3
DEFAULT_TEMPERATURE=0.7
TOP_P=0.9
MAX_TOKENS=4096
MAX_WORKERS=10
MAX_SOURCES_PER_QUERY=5
MAX_DEPTH=2
ENABLE_PARALLEL_EXECUTION=true
MAX_QA_RETRIES=2

# ── RAG / context management
ANALYST_TOP_K=8
MAX_TOOL_RESULT_CHARS_IN_MESSAGE=5000
MAX_SEARCH_HISTORY_MESSAGES=8
MAX_ANALYST_FALLBACK_CHARS=8000
DISTILL_MAX_CHARS=2000
STEP_SUMMARY_MAX_CHARS=800
SECTION_DRAFT_TOP_K=6

# ── Embeddings
EMBEDDINGS_ENABLED=true
EMBEDDINGS_MODEL=all-MiniLM-L6-v2
MAX_EMBEDDING_WORKERS=2
EOF
    echo ".env file created. Please edit it with your configuration."
fi

echo ""
echo "Setup complete!"
echo "Next steps:"
echo "  1. Edit backend/.env with your configuration"
echo "  2. Start MCP servers: docker compose -f docker-compose-mcp.yml up -d"
echo "  3. Run the API server: uvicorn api_server:app --host 0.0.0.0 --port 9999"