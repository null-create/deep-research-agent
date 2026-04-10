# Research Assistant

An AI-powered deep research system built on a hierarchical multi-agent architecture. A React/TypeScript frontend communicates over WebSocket with a Python/FastAPI backend that orchestrates a five-agent pipeline (Orchestrator → Search → Analyst → Loop → ReportComposer) backed by MCP servers for web search, scraping, and file I/O.

## Architecture

**Five-agent pipeline:**

| Agent | Role |
|-------|------|
| Root (Orchestrator) | Generates and owns the research plan; injects methodology from `RESEARCH-METHODS.md` |
| SearchAgent | High-recall retrieval via MCP tools (web search + scrape) |
| AnalystAgent | Cross-source extraction, claim triangulation, credibility scoring |
| LoopAgent | Contradiction detection and classification; routes only objectively resolvable conflicts back for targeted re-investigation; injects source disagreements as analyst tensions |
| ReportComposer | Multi-pass synthesis → structured report with novel insights and actionable next steps |

**Key technical properties:**

- Parallel step execution: same-group steps dispatched concurrently; `MAX_WORKERS=10` semaphore gates concurrent execution
- In-process RAG: `SearchResultStore` with sentence-transformer embeddings (cosine ranking) scoped per session
- In-process long-term memory: `AsyncLongTermMemory` persists cross-session findings to Neo4j (graph database). Two layers: flat vector store (Neo4j vector indexes) for raw evidence + `KnowledgeGraph` (GraphRAG) layer for typed entities, directed triples with confidence scores, and LLM-generated community clusters. Relationships are deduplicated and include temporal tracking (`last_confirmed`, `confirmation_count`). Supports hierarchy (`IS_A`), contradiction (`CONTRADICTS`), and provenance (`SOURCED_FROM → Source`) edges. Graph context is recalled during planning; communities are updated post-synthesis only when enough new facts have landed (`GRAPH_COMMUNITY_MIN_MUTATIONS`); confidence is decayed over time (`CONFIDENCE_DECAY_HALF_LIFE`). A `prune()` method archives stale low-confidence relationships and orphaned entities.
- Self-optimization mode: agent reads session logs and the knowledge graph, then updates its own research playbook (`backend/instructions/RESEARCH-METHODS.md`)
- MCP servers: UA rotation, per-domain rate limiting (2s), concurrency cap (3), 429 retry, 50KB output cap

## Quick Start

Requires Docker and Docker Compose.

```bash
make run-all      # full stack: backend + frontend + all MCP servers
make run          # backend + MCP servers only (no frontend)
make bench        # Run benchmark script
```

- Backend API: `http://localhost:9999`
- Frontend UI: `http://localhost:4000`

**Other useful `make` commands:**

| Command | Description |
|---------|-------------|
| `make stop` | Stop backend + MCP servers |
| `make stop-all` | Stop all services |
| `make restart` | Stop + restart backend + MCP servers |
| `make restart-all` | Stop + restart everything |
| `make init` | Install backend venv and frontend node_modules |
| `make clean` | Remove `.venv` and `node_modules` |

**Local development (without Docker):**

```bash
# Backend
cd backend && ./setup.sh && source venv/bin/activate && python api_server.py

# Frontend (hot-reload dev server)
cd src && npm install && npm run dev

# Frontend only via make
make run-fe
```

## Configuration

Create `backend/.env`:

```env
# ── Agent mode ────────────────────────────────────────────────
AGENT_MODE=research                  # research | chat | self-optimization

# ── Model backend ─────────────────────────────────────────────
MODEL_BACKEND=ollama                 # openai | ollama | bedrock | azure | gcp | huggingface

# Ollama
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=nemotron-3-nano

# OpenAI (or any OpenAI-compatible endpoint)
# OPENAI_API_KEY=sk-...
# OPENAI_MODEL=gpt-4o
# OPENAI_BASE_URL=https://api.openai.com/v1

# AWS Bedrock (uses default boto3 credential chain if AWS_API_KEY is unset)
# AWS_MODEL=global.anthropic.claude-sonnet-4-5-20250929-v1:0
# AWS_API_KEY=...
# AWS_BASE_URL=...

# Azure OpenAI
# AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com
# AZURE_OPENAI_API_KEY=...
# AZURE_OPENAI_DEPLOYMENT=your-deployment-name
# AZURE_API_VERSION=2024-02-15-preview

# GCP Vertex AI
# GCP_ENDPOINT=https://us-central1-aiplatform.googleapis.com/v1
# GCP_MODEL=gemini-2.5-pro
# GCP_API_KEY=...

# HuggingFace (local TGI endpoint)
# HUGGINGFACE_BASE_URL=http://localhost:8080
# HUGGINGFACE_MODEL=...

# ── MCP server URLs ───────────────────────────────────────────
SEARCH_SERVER_URL=http://localhost:9393/mcp
SCRAPER_SERVER_URL=http://localhost:9292/mcp
FILE_SERVER_URL=http://localhost:9191/mcp

# ── Search backend (used by web_search MCP server) ────────────
SEARCH_BACKEND=duckduckgo            # duckduckgo | brave | serper | google
# BRAVE_API_KEY=...
# SERPER_API_KEY=...
# GOOGLE_CSE_ID=...
# GOOGLE_API_KEY=...
# GITHUB_TOKEN=...                   # avoids 60 req/hr unauthenticated cap on search_github
# S2_API_KEY=...                      # optional; raises Semantic Scholar unauthenticated rate limits

# ── Long-term memory (Neo4j) ─────────────────────────────────
# NEO4J_URI=bolt://localhost:7687
# NEO4J_USER=neo4j
# NEO4J_PASSWORD=research_pass
# NEO4J_DATABASE=neo4j
# CONFIDENCE_DECAY_HALF_LIFE=30      # days; controls exponential confidence decay on graph edges
# GRAPH_COMMUNITY_MIN_MUTATIONS=3   # min new entities+rels before community re-detection runs

# ── Tuning (optional — defaults shown) ───────────────────────
# MAX_ITERATIONS=3
# MAX_SOURCES_PER_QUERY=5
# MAX_WORKERS=10
# MAX_CONCURRENT_SESSIONS=10         # max concurrent research sessions (0 = unlimited)
# RESEARCH_DEPTH=shallow              # shallow | moderate | deep
# ENABLE_PARALLEL_EXECUTION=true
# EMBEDDINGS_ENABLED=true
# EMBEDDINGS_MODEL=all-MiniLM-L6-v2
# LOG_LEVEL=20                       # 10=DEBUG 20=INFO 30=WARNING
```

Create `src/.env`:

```env
VITE_API_URL=http://localhost:9999
VITE_WS_URL=ws://localhost:9999
```

See [docs/API_SERVER.md](docs/API_SERVER.md) for the full environment variable reference.

## MCP Servers

| Server | Port | Tools |
|--------|------|-------|
| `mcp/web_search/` | 9393 | `web_search`, `image_search`, `video_search`, `search_wikipedia`, `search_github`, `search_arxiv`, `search_semantic_scholar` |
| `mcp/web_scrape/` | 9292 | `scrape_url` |
| `mcp/file_handler/` | 9191 | `list_files`, `read_file`, `streaming_read_file`, `write_file`, `upload_file`, `download_file`, `run_command` |

Long-term memory is handled in-process by `backend/long_term_memory.py`, backed by Neo4j. It has two layers:

- **Flat store** (`:Memory` nodes with vector index): raw evidence and claims, recalled via cosine similarity over Neo4j vector indexes.
- **Knowledge graph**: typed nodes (`:Entity`, `:Community`, `:Source`) and edges (`:RELATES_TO`, `:IS_A`, `:CONTRADICTS`, `:SOURCED_FROM`, `:MEMBER_OF`). Features: relationship deduplication with merge-on-conflict, temporal tracking, hierarchy traversal, contradiction detection, URL-level provenance, cross-session path finding, confidence decay, and graph pruning. Nine REST endpoints under `/graph/` expose the graph for external inspection and maintenance.

There is no separate memory MCP server.

## Infrastructure

Terraform IaC for Azure Container Apps is under `infra/azure/`. It provisions 5 Container Apps (backend + 3 MCP servers + frontend), ACR with managed identity, Azure File Shares for file handler data, and Log Analytics. The backend requires a Neo4j instance (provisioned via the `neo4j` Docker Compose service locally). See [infra/azure/README.md](infra/azure/README.md) for deployment instructions.

## AI Coding Assistant

This repo ships with a custom **Agent Developer Assistant** for GitHub Copilot. It is a senior-engineer-level coding agent with deep knowledge of this codebase — architecture, conventions, concurrency model, known bugs, and historical decisions. Use it for implementing features, debugging, refactoring, or answering questions about how the system works.

**To use it:** open GitHub Copilot Chat in VS Code and select the `Agent Developer Assistant` agent.

At the start of each session the agent automatically:

1. Loads its persistent memory from `.github/agents/Agent-Developer-Assistant/memory/MEMORY.md`
2. Loads the living architecture map from `.github/agents/Agent-Developer-Assistant/memory/PROJECT-KNOWLEDGE.md`
3. Checks recent `git log` to orient itself on what has changed

Both memory files are updated at the end of sessions when something worth preserving is learned. The agent definition lives at `.github/agents/Agent-Developer-Assistant.md`.

## Testing

```bash
cd backend && pytest
```

For a full E2E flow test (mock MCP registry + real LLM, no API server needed):

```bash
cd backend && python test_e2e.py
```

## Documentation

| Doc | Contents |
|-----|----------|
| [docs/ORCHESTRATOR.md](docs/ORCHESTRATOR.md) | Five-agent pipeline, RAG, concurrency model, multi-pass synthesis |
| [docs/API_SERVER.md](docs/API_SERVER.md) | WebSocket protocol, REST endpoints, all env vars |
| [docs/RESEARCH_AGENT.md](docs/RESEARCH_AGENT.md) | Agent base class, plan generation, step execution |
| [docs/MCP_SERVERS.md](docs/MCP_SERVERS.md) | MCP server setup, tools, and ports |
| [docs/CLI.md](docs/CLI.md) | CLI and TUI usage |
| [docs/RAG_NOTES.md](docs/RAG_NOTES.md) | SearchResultStore, embeddings, multi-pass synthesis |
| [docs/BENCHMARKING.md](docs/BENCHMARKING.md) | Benchmark runner, DeepResearch Bench, scoring |
