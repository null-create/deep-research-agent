# MCP Servers — Architecture & Reference

> **Directory:** `mcp/`  
> **Protocol:** [Model Context Protocol](https://modelcontextprotocol.io/) via [FastMCP](https://github.com/jlowin/fastmcp)  
> **Transport:** `streamable-http` (HTTP/SSE)  
> **Compose file:** `docker-compose-mcp.yml`

---

## Table of Contents

1. [Overview](#overview)
2. [Shared Conventions](#shared-conventions)
   - [Transport & Endpoint Shape](#transport--endpoint-shape)
   - [Health Checks](#health-checks)
   - [Docker Conventions](#docker-conventions)
   - [Network](#network)
3. [Memory Server](#memory-server)
   - [Purpose](#purpose-1)
   - [Storage Architecture](#storage-architecture)
   - [Tools](#tools-memory)
   - [Memory Record Shape](#memory-record-shape)
   - [Relation Graph](#relation-graph)
   - [Memory Scoring & Ranking](#memory-scoring--ranking)
   - [Memory Decay](#memory-decay)
   - [Deduplication](#deduplication)
   - [Persistence](#persistence)
   - [Configuration](#configuration-memory)
4. [Web Search Server](#web-search-server)
   - [Purpose](#purpose-2)
   - [Search Backends](#search-backends)
   - [Tools](#tools-web-search)
   - [Result Shapes](#result-shapes)
   - [Configuration](#configuration-web-search)
5. [Web Scraper Server](#web-scraper-server)
   - [Purpose](#purpose-3)
   - [Scraping Pipeline](#scraping-pipeline)
   - [Tools](#tools-web-scraper)
   - [Configuration](#configuration-web-scraper)
6. [File Handler Server](#file-handler-server)
   - [Purpose](#purpose-4)
   - [Tool Modules](#tool-modules)
   - [Tools](#tools-file-handler)
   - [Atomic Writes](#atomic-writes)
   - [Embedding Support](#embedding-support)
   - [Persistence](#persistence-1)
   - [Configuration](#configuration-file-handler)
7. [Server Summary](#server-summary)
8. [How the Research Agent Uses Each Server](#how-the-research-agent-uses-each-server)
9. [Running the Servers](#running-the-servers)
10. [Adding a New MCP Server](#adding-a-new-mcp-server)

---

## Overview

The research assistant uses four independent MCP servers, each running in its own Docker container and communicating with the backend over a shared Docker network. Each server exposes a set of **tools** — callable functions that the agent's LLM can invoke during the tool-calling loop in `_execute_step`.

```
┌──────────────────────────────────────────────────────────┐
│                  Backend (api_server.py)                 │
│                                                          │
│  MCPServerRegistry                                       │
│  ├── "web_search"    → http://mcp-web-search-server:9393 │
│  ├── "web_scraper"   → http://mcp-web-scraping-server:9292│
│  └── "file_handler"  → http://mcp-file-handler-server:9191│
│                                                          │
│  AsyncLongTermMemory → Neo4j (bolt://neo4j:7687)         │
└──────────────────────────────────────────────────────────┘
          │               │               │              │
          ▼               ▼               ▼              ▼
    ┌──────────┐  ┌─────────────┐  ┌──────────┐  ┌──────────────┐
    │ Neo4j    │  │ Web Search  │  │ Web      │  │ File Handler │
    │ Graph DB │  │ Server      │  │ Scraper  │  │ Server       │
    │ :7687    │  │ :9393       │  │ :9292    │  │ :9191        │
    └──────────┘  └─────────────┘  └──────────┘  └──────┬───────┘
                                                         │
                                                    /app/data
                                               ./file_handler_data
```

The backend connects to all three MCP servers at startup via `create_mcp_registry()`. If a server is unreachable, the registry logs a warning and continues without it — research sessions will proceed with degraded capability rather than failing entirely. Long-term memory connects directly to Neo4j via the `neo4j` async driver (not an MCP server).

---

## Shared Conventions

### Transport & Endpoint Shape

All servers use FastMCP with the `streamable-http` transport. The MCP session endpoint is available at:

```
http://<host>:<port>/mcp
```

Tool schemas are listed via the MCP `list_tools` call. The backend fetches these at registration time and caches them in `MCPServerRegistry.tool_specs` for routing.

### Health Checks

Every server exposes a `GET /health` endpoint that returns:

```json
{ "status": "healthy", "service": "<service-name>" }
```

This is used by Docker for container health monitoring.

### Docker Conventions

All four servers share the same Dockerfile pattern:

| Convention | Value |
|---|---|
| Base image | `python:3.12-slim` |
| Working directory | `/app` |
| Runtime user | `mcpuser` (UID 1000, non-root) |
| Entrypoint | `python /app/main.py` |
| Log directory | `/app/logs` |
| Temp directory | `/app/tmp` |

Environment variables available in all containers:

| Variable | Default | Description |
|---|---|---|
| `HOST_ADDRESS` / `HOST_ADDR` | `0.0.0.0` | Bind address |
| `HOST_PORT` | *(per server)* | Bind port |
| `LOG_LEVEL` | `INFO` | Python logging level string |
| `ENVIRONMENT` | `local` | Deployment environment label |

### Network

All services — including the backend — are attached to the `research-network` bridge network defined in `docker-compose-mcp.yml`. Containers address each other by Docker service name (e.g. `mcp-memory-server`).

---

## Long-Term Memory (In-Process, Neo4j-Backed)

> **Module:** `backend/long_term_memory.py`  
> **Database:** Neo4j (service name `neo4j`, port `7687`)

### Purpose

Provides **persistent, semantic long-term memory** for the research agent. Memories survive container restarts and accumulate across research sessions, allowing the agent to build up a growing knowledge base that informs future research planning. This is **not** an MCP server — it connects directly to Neo4j from within the backend process.

### Storage Architecture

```
AsyncLongTermMemory  (backend/long_term_memory.py)
       │
       ├── :Memory nodes + memory_embedding_idx (vector index)
       │     Stores: memory content, embeddings, metadata
       │     Similarity: cosine via Neo4j vector index
       │
       ├── :Entity nodes + entity_embedding_idx
       │     Stores: named entities with type, descriptions
       │
       ├── :RELATES_TO edges
       │     Stores: directed triples between entities
       │
       ├── :Community nodes + community_embedding_idx
       │     Stores: LLM-generated cluster summaries
       │
       └── :MEMBER_OF edges
             Links: entities → communities
```

Embeddings are generated using the `all-MiniLM-L6-v2` sentence transformer model (loaded at backend startup via `warm_up_embeddings()`). All embedding calls are offloaded to a `ThreadPoolExecutor`.

### Tools (Memory)

#### `store_memory`

Stores a new memory with automatic embedding generation and near-duplicate detection.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `content` | `str` | *required* | The text content to remember |
| `category` | `str` | `"general"` | Logical grouping label |
| `importance` | `int` | `5` | Priority weight 1–10; higher = retrieved first |
| `tags` | `List[str]` | `[]` | Free-form labels for filtering |
| `metadata` | `Dict` | `{}` | Arbitrary key-value pairs (stored with `custom_` prefix) |

**Returns:**

```json
{
  "success": true,
  "memory_id": "<uuid>",
  "message": "Memory stored successfully"
}
```

If a memory with > 0.95 cosine similarity already exists, the call is rejected and the existing `memory_id` is returned instead, preventing duplicate accumulation.

---

#### `recall_memories`

Retrieves memories using **semantic vector search** plus optional metadata filters.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `query` | `str` | `None` | Semantic search query — finds by *meaning*, not keywords |
| `category` | `str` | `None` | Filter to a specific category |
| `tags` | `List[str]` | `None` | Return memories with any of these tags |
| `min_importance` | `int` | `None` | Only return memories at or above this importance level |
| `limit` | `int` | `10` | Maximum number of memories to return |
| `similarity_threshold` | `float` | `0.0` | Minimum cosine similarity to include (0.0–1.0) |

If `query` is `None`, returns all memories matching the metadata filters sorted by importance. Otherwise, performs a vector search and sorts results by `importance × similarity`.

Each call increments the `access_count` and updates `last_accessed` on every returned memory.

**Returns:** JSON array of [Memory Record](#memory-record-shape) objects.

---

#### `retrieve_all_memories`

Returns every stored memory with no filtering. Intended for the `SelfOptimizingAgent`'s introspection cycle. Use with caution on large memory stores.

**Returns:** JSON array of all [Memory Record](#memory-record-shape) objects.

---

#### `find_similar_memories`

Convenience wrapper around `recall_memories` with a stricter default similarity threshold. Useful for associative recall — finding memories that are *semantically close* to a given concept.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `query` | `str` | *required* | Text to find similar memories for |
| `limit` | `int` | `5` | Maximum results |
| `min_similarity` | `float` | `0.7` | Minimum cosine similarity threshold |

**Returns:** JSON array of [Memory Record](#memory-record-shape) objects.

---

#### `update_memory`

Updates an existing memory by ID. Updating `content` deletes and re-inserts the record to regenerate its embedding. Updating only metadata fields is done in-place without touching the embedding.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `memory_id` | `str` | *required* | UUID of the memory to update |
| `content` | `str` | `None` | New content (triggers embedding regeneration) |
| `category` | `str` | `None` | New category |
| `importance` | `int` | `None` | New importance level |
| `tags` | `List[str]` | `None` | Replacement tag list |
| `metadata` | `Dict` | `None` | Metadata to merge into existing custom fields |

---

#### `delete_memory`

Deletes a memory and all relations that reference it (both incoming and outgoing edges in the relation graph).

| Parameter | Type | Description |
|---|---|---|
| `memory_id` | `str` | UUID of the memory to delete |

---

#### `create_relation`

Creates a directed, typed edge between two memories in the relation graph.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `from_memory_id` | `str` | *required* | Source memory UUID |
| `to_memory_id` | `str` | *required* | Target memory UUID |
| `relation_type` | `str` | `"related_to"` | Semantic label for the edge (e.g. `"causes"`, `"part_of"`, `"contradicts"`) |
| `strength` | `float` | `1.0` | Edge weight 0.0–1.0 |

Relations are themselves stored as embeddings in the `agent_memories_relations` collection, meaning they can also be searched semantically in future extensions.

---

#### `get_related_memories`

Traverses the relation graph outward from a given memory and returns all directly connected memories.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `memory_id` | `str` | *required* | UUID to find relations for |
| `relation_type` | `str` | `None` | Filter to a specific relation type |
| `min_strength` | `float` | `0.0` | Minimum edge weight |

Each result includes `relation_type`, `relation_strength`, and `relation_direction` (`"incoming"` or `"outgoing"`).

---

#### `get_statistics`

Returns aggregate metrics about the memory store.

**Returns:**

```json
{
  "total_memories": 142,
  "memories_by_category": { "research_session": 38, "insight": 104 },
  "top_tags": { "climate": 12, "energy": 9 },
  "average_importance": 5.4,
  "most_accessed_memories": [
    { "id": "...", "content": "...", "access_count": 27 }
  ],
  "total_relations": 18
}
```

---

#### `consolidate_memories`

Simulates memory decay by decreasing the `importance` of old, rarely-accessed memories by 1 (minimum 1). Only memories that are:

- `importance >= min_importance` (default 3)
- Last accessed more than `days_old` days ago (default 30)
- `access_count < 5`

…are affected. Memories that are frequently accessed or very recent are left untouched.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `min_importance` | `int` | `3` | Only consolidate memories at or above this importance |
| `days_old` | `int` | `30` | Only consolidate memories not accessed within this window |

---

### Memory Record Shape

Every tool that returns memories uses this structure:

```json
{
  "id": "<uuid>",
  "content": "GLP-1 agonists reduce major adverse cardiovascular events by ~14%...",
  "category": "insight",
  "importance": 7,
  "created_at": "2026-03-06T12:00:00",
  "last_accessed": "2026-03-06T14:22:01",
  "access_count": 3,
  "tags": ["cardiology", "GLP-1", "RCT"],
  "metadata": { "source_query": "cardiovascular effects of GLP-1 agonists" },
  "similarity": 0.9142
}
```

`similarity` is only present in query results and is computed as `1 - cosine_distance`.

---

### Relationship Graph

The relationship graph is stored as native Neo4j `:RELATES_TO` edges between `:Entity` nodes. Edges have:

- `from_memory_id` / `to_memory_id` — the two connected memories
- `relation_type` — a free-form semantic label
- `strength` — a float weight (0.0–1.0)
- `created_at` — timestamp

Built-in suggested relation types (not enforced):

| Type | Meaning |
|---|---|
| `related_to` | Generic association |
| `causes` | Causal link |
| `part_of` | Hierarchical containment |
| `contradicts` | Factual conflict |
| `supports` | Corroborating evidence |
| `derived_from` | One memory was derived from another |

---

### Memory Scoring & Ranking

`recall_memories` ranks results by `importance × similarity`:

- A memory with `importance=10` and `similarity=0.5` scores `5.0`.
- A memory with `importance=5` and `similarity=0.95` scores `4.75`.

This means highly important memories can outrank slightly more semantically similar but lower-priority ones.

---

### Memory Decay

`consolidate_memories` is designed to be called periodically (e.g. on a cron schedule or by the `SelfOptimizingAgent`). It implements a simple importance decay model:

```
For each memory:
  if importance >= min_importance
  AND last_accessed < (now - days_old)
  AND access_count < 5:
      importance = max(1, importance - 1)
```

Memories that are accessed regularly will have their `access_count` incremented by `recall_memories` and will never be decayed.

---

### Deduplication

Before adding a new memory, `store()` uses the Neo4j vector index to find the most similar existing `:Memory` node. If its cosine similarity exceeds `0.95`, the new memory is rejected and the existing ID is returned. This prevents the store from accumulating near-identical records from repeated research sessions on the same topic.

---

### Persistence

Neo4j data is persisted to a Docker named volume (`neo4j_data`) mapped to `/data` inside the container. This survives container restarts and rebuilds. To reset all memory, remove the named volume:

```bash
docker volume rm research-assistant_neo4j_data
```

### Configuration (Long-Term Memory)

| Environment variable | Default | Description |
|---|---|---|
| `NEO4J_URI` | `bolt://localhost:7687` | Neo4j Bolt protocol URI |
| `NEO4J_USER` | `neo4j` | Neo4j authentication username |
| `NEO4J_PASSWORD` | `research_pass` | Neo4j authentication password |
| `NEO4J_DATABASE` | `neo4j` | Neo4j database name |
| `NEO4J_EMBEDDING_DIMENSIONS` | `384` | Vector index dimension (must match embedding model) |

---

## Web Search Server

> **Directory:** `mcp/web_search/`  
> **Port:** `9393`  
> **Service name:** `mcp-web-search-server`

### Purpose

Provides unified, **API-key-free** web search across multiple backends. The agent uses this server to retrieve links and snippets as the first stage of information gathering, before scraping selected URLs for full content.

### Search Backends

| Backend | Library | Auth required | Notes |
|---|---|---|---|
| DuckDuckGo | `ddgs >= 9.0.0` | No | Primary backend for all web, image, video, and suggestion searches |
| Wikipedia | `wikipedia >= 1.4.0` | No | Returns article summaries; handles disambiguation automatically |
| GitHub | GitHub REST API | Optional | Unauthenticated: 60 req/hr. Set `GITHUB_TOKEN` to raise the limit to 5 000 req/hr |
| arXiv | `arxiv >= 2.1.0` | No | Official arXiv SDK; CS, physics, math, economics, and quantitative biology preprints |
| Semantic Scholar | S2 REST API (direct HTTP) | Optional | 200M+ papers; unauthenticated rate limit applies. Set `S2_API_KEY` for higher throughput |

### Tools (Web Search)

#### `web_search`

General-purpose web search using DuckDuckGo.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `query` | `str` | *required* | Search query |
| `max_results` | `int` | `10` | Number of results (clamped 1–50) |
| `region` | `str` | `"wt-wt"` | Region code (e.g. `us-en`, `uk-en`, `wt-wt` for worldwide) |
| `safesearch` | `str` | `"moderate"` | `strict`, `moderate`, or `off` |

---

#### `image_search`

Image search using DuckDuckGo.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `query` | `str` | *required* | Image search query |
| `max_results` | `int` | `10` | Number of results (clamped 1–50) |
| `size` | `str` | `"Medium"` | `Small`, `Medium`, `Large`, `Wallpaper` |
| `type_image` | `str` | `"photo"` | `photo`, `clipart`, `gif`, `transparent`, `line` |
| `region` | `str` | `"wt-wt"` | Region code |

---

#### `video_search`

Video search using DuckDuckGo.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `query` | `str` | *required* | Video search query |
| `max_results` | `int` | `10` | Number of results (clamped 1–50) |
| `duration` | `str` | `"Medium"` | `Short`, `Medium`, `Long` |
| `resolution` | `str` | `"High"` | `High`, `Standard` |
| `region` | `str` | `"wt-wt"` | Region code |

---

#### `search_wikipedia`

Searches Wikipedia and returns article titles, URLs, and the first 300 characters of each article's summary. Handles `DisambiguationError` automatically by selecting the first option.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `query` | `str` | *required* | Search query |
| `max_results` | `int` | `10` | Number of articles (clamped 1–50) |

---

#### `search_github`

Searches GitHub public repositories by name, description, and topic using the GitHub REST search API. Results are sorted by star count descending. If `GITHUB_TOKEN` is set in the environment, it is sent as a `Bearer` token, raising the rate limit from 60 req/hr (unauthenticated) to 5 000 req/hr.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `query` | `str` | *required* | Search query |
| `max_results` | `int` | `10` | Number of repositories (clamped 1–50) |

---

#### `get_search_suggestions`

Returns DuckDuckGo autocomplete suggestions for a partial query. Useful for query expansion or disambiguation before a full search.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `query` | `str` | *required* | Partial query to get suggestions for |
| `region` | `str` | `"wt-wt"` | Region code |

---

#### `search_arxiv`

Searches [arXiv](https://arxiv.org) for academic papers and preprints using the official `arxiv` SDK. Best for CS, physics, mathematics, economics, and quantitative biology. Supports arXiv query syntax (`ti:`, `au:`, `abs:`, etc.).

| Parameter | Type | Default | Description |
|---|---|---|---|
| `query` | `str` | *required* | Search query; supports arXiv field prefixes |
| `max_results` | `int` | `10` | Number of papers (clamped 1–50) |
| `sort_by` | `str` | `"relevance"` | `relevance`, `lastUpdatedDate`, or `submittedDate` |

---

#### `search_semantic_scholar`

Searches [Semantic Scholar](https://www.semanticscholar.org) via their public Graph API (direct HTTP — no SDK). Indexes 200M+ peer-reviewed papers across all disciplines with rich metadata including citation counts, open-access PDF links, and field classification.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `query` | `str` | *required* | Search query |
| `max_results` | `int` | `10` | Number of papers (clamped 1–100) |
| `fields_of_study` | `List[str]` | `None` | Optional field filter, e.g. `["Computer Science", "Medicine"]` |
| `year_range` | `str` | `None` | Optional year filter, e.g. `"2020-2024"` or `"2023"` |

---

### Result Shapes

All search tools return a consistent envelope:

```json
{
  "success": true,
  "query": "the original query string",
  "results_count": 5,
  "results": [ ... ]
}
```

Individual result objects vary by tool:

**`web_search` result:**

```json
{ "title": "...", "url": "...", "snippet": "...", "source": "DuckDuckGo" }
```

**`image_search` result:**

```json
{ "title": "...", "url": "...", "thumbnail": "...", "source": "...", "width": 1920, "height": 1080 }
```

**`video_search` result:**

```json
{ "title": "...", "url": "...", "thumbnail": "...", "duration": "4:32", "source": "...", "published": "..." }
```

**`search_wikipedia` result:**

```json
{ "title": "...", "url": "...", "snippet": "First 300 chars of summary...", "source": "Wikipedia" }
```

**`search_github` result:**

```json
{ "title": "owner/repo", "url": "...", "snippet": "repo description", "source": "GitHub", "stars": 4821, "language": "Python", "updated": "2026-02-14T..." }
```

**`search_arxiv` result:**

```json
{
  "title": "...",
  "url": "https://arxiv.org/abs/2406.12345",
  "pdf_url": "https://arxiv.org/pdf/2406.12345",
  "snippet": "First 500 chars of abstract...",
  "authors": ["Alice Smith", "Bob Jones"],
  "published": "2024-06-18",
  "updated": "2024-07-01",
  "categories": ["cs.LG", "cs.AI"],
  "source": "arXiv"
}
```

**`search_semantic_scholar` result:**

```json
{
  "title": "...",
  "url": "https://www.semanticscholar.org/paper/...",
  "pdf_url": "https://arxiv.org/pdf/...",
  "snippet": "First 500 chars of abstract...",
  "authors": ["Alice Smith", "Bob Jones"],
  "year": 2023,
  "citation_count": 412,
  "fields_of_study": ["Computer Science"],
  "source": "Semantic Scholar"
}
```

On failure, all tools return `{ "success": false, "error": "...", "query": "..." }`.

### Configuration (Web Search)

| Environment variable | Default | Description |
|---|---|---|
| `HOST_PORT` | `9393` | Server bind port |
| `HOST_ADDRESS` | `0.0.0.0` | Server bind address |
| `GITHUB_TOKEN` | *(unset)* | Optional GitHub personal access token. When set, added as `Authorization: Bearer` on all GitHub API calls, raising the rate limit from 60 req/hr to 5 000 req/hr |
| `S2_API_KEY` | *(unset)* | Optional Semantic Scholar API key. When set, sent as `x-api-key` header, raising the unauthenticated rate limit substantially |

No API keys are required for DuckDuckGo, Wikipedia, or arXiv. All search backends rotate through a pool of six modern browser User-Agent strings per request to reduce bot-detection fingerprinting. All tool functions run in a thread pool (`asyncio.to_thread`) to avoid blocking the FastMCP event loop under concurrent orchestrator load.

---

## Web Scraper Server

> **Directory:** `mcp/web_scrape/`  
> **Port:** `9292`  
> **Service name:** `mcp-web-scraping-server`

### Purpose

Fetches full web page content given a URL and returns clean, structured text. While the Web Search server provides links and snippets, this server is used to retrieve the actual content of those pages for deeper analysis.

### Scraping Pipeline

```
URL input
    │
    ▼
asyncio.Semaphore(3)              ← at most 3 concurrent fetches
    │
    ▼
_enforce_domain_rate(domain)      ← 2s minimum interval per domain
    │
    ▼
httpx.AsyncClient.get(url)        ← 30s timeout, follows redirects,
    │                                rotated User-Agent from pool of 6
    │                                (Chrome 123/124, Firefox 125, Safari 17)
    │                                retry once on HTTP 429 with Retry-After
    ▼
BeautifulSoup HTML parse
    │
    ├── Remove <script> and <style> tags
    │
    ├── Extract text via soup.get_text()
    │
    ├── (if clean_text=True) Strip blank lines and double-spaces
    │
    ├── Truncate text to 50 KB (_SCRAPE_MAX_CHARS) to prevent log bloat;
    │     appends "[... truncated — original N chars]" marker if cut
    │
    ├── (if include_links=True) Extract all <a href> tags,
    │     convert relative URLs to absolute
    │
    └── (if include_images=True) Extract all <img src> tags,
          convert relative URLs to absolute
```

The `httpx` client is a **module-level singleton** with `follow_redirects=True`. A User-Agent is selected randomly from a pool of six modern browser strings (Chrome 123/124, Firefox 125, Safari 17) on every request to reduce bot-detection fingerprinting. Concurrent requests are capped at 3 via an `asyncio.Semaphore`, and a 2-second minimum interval is enforced per domain to prevent rate-limiting. HTTP 429 responses are retried once after the server's `Retry-After` delay (capped at 10 s).

### Tools (Web Scraper)

#### `scrape_url`

Fetches and extracts the complete content of a web page.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `url` | `str` | *required* | Fully-qualified URL to scrape |
| `include_links` | `bool` | `false` | Append a `--- LINKS ---` section with all anchor tags |
| `include_images` | `bool` | `false` | Append an `--- IMAGES ---` section with all image URLs |
| `clean_text` | `bool` | `true` | Strip blank lines and collapse whitespace |

**Returns:** A plain text string with the following structure:

```
URL: https://example.com/article
Status Code: 200
Content Type: text/html; charset=utf-8

--- PAGE CONTENT ---
<extracted text body>

--- LINKS ---          (if include_links=True)
Link text: https://example.com/other-page
...

--- IMAGES ---         (if include_images=True)
Alt text: https://example.com/image.jpg
...
```

On error, raises a `ValueError` with a descriptive message (HTTP status error, request error, or invalid URL). HTTP 403 errors are not retried — they indicate access denial and a retry would consume quota without improving the outcome.

### Configuration (Web Scraper)

| Environment variable | Default | Description |
|---|---|---|
| `HOST_PORT` | `9292` | Server bind port |
| `HOST_ADDR` | `0.0.0.0` | Server bind address |

The following constants are hardcoded in `main.py` and can be changed by editing the file:

| Constant | Value | Description |
|---|---|---|
| `_SCRAPE_MAX_CHARS` | `50 000` | Maximum extracted text characters returned; excess is truncated |
| `_DOMAIN_MIN_INTERVAL` | `2.0 s` | Minimum delay between successive requests to the same domain |
| Semaphore | `3` | Maximum simultaneous in-flight scrape requests |
| Client timeout | `30 s` | Per-request HTTP timeout |

Pages requiring JavaScript rendering (SPAs) will return minimal or empty content since no headless browser is used. For JS-heavy sites, consider adding a Playwright or Puppeteer-based tool to this server.

---

## File Handler Server

> **Directory:** `mcp/file_handler/`  
> **Port:** `9191`  
> **Service name:** `mcp-file-handler-server`

### Purpose

Provides **sandboxed file I/O** for the research agent. All reads and writes are scoped to `/app/data` inside the container, which is bind-mounted to `./file_handler_data` on the host. This isolates arbitrary file writes from the host filesystem while still making outputs accessible.

The server also provides **text embedding tools** powered by `sentence-transformers`, enabling semantic similarity search over file contents.

### Tool Modules

The implementation is split across four Python modules under `mcp/file_handler/tools/`:

| Module | Responsibility |
|---|---|
| `file_reader.py` | Simple and streaming file reads, document text extraction (PDF, DOCX, ODT), directory listing |
| `file_writer.py` | Chunked atomic writes and append operations |
| `file_transfer.py` | HTTP upload and download |
| `file_embeddings.py` | Embedding generation and similarity search |

### Tools (File Handler)

#### `list_files`

Recursively walks the output directory (`/app/data`) and returns a flat list of all files found.

**Returns:** Array of file info objects:

```json
[
  {
    "name": "research_report_abc123.txt",
    "absolute_path": "/app/data/research_report_abc123.txt",
    "relative_path": "research_report_abc123.txt",
    "size_bytes": 14823
  }
]
```

---

#### `read_file`

Reads the full content of a file into memory. Suitable for files up to the configured `max_file_size` limit (default 100 MB).

For binary document formats, the file is automatically converted to plain text before being returned. The following formats are supported:

| Extension | Library | Extraction method |
|---|---|---|
| `.pdf` | `pypdf` | Page-by-page text extraction, pages joined with double newlines |
| `.docx` | `python-docx` | Paragraph extraction, non-empty paragraphs joined with double newlines |
| `.odt` | `odfpy` | Paragraph node traversal, non-empty paragraphs joined with double newlines |
| All other text files (`.txt`, `.md`, `.csv`, etc.) | — | Direct `open()` read using the specified encoding |

| Parameter | Type | Description |
|---|---|---|
| `file_path` | `str` | Path to the file to read |

**Returns:** Plain text content as a string. For document formats, this is the extracted body text with no formatting, markup, or binary data.

---

#### `streaming_read_file`

Streams a file in chunks to avoid loading the entire content into memory. Intended for large files. Supports the same document formats as `read_file` — binary documents (PDF, DOCX, ODT) are extracted to plain text first and then streamed from an in-memory buffer.

The `invocation` dict accepts:

| Key | Type | Default | Description |
|---|---|---|---|
| `path` | `str` | *required* | Path to the file |
| `chunk_size` | `int` | `8192` | Bytes per chunk (512 B – 1 MB) |
| `encoding` | `str` | `utf-8` | File encoding (ignored for binary document formats) |
| `max_file_size` | `int` | `100 MB` | Maximum allowable file size |

**Yields:** `FileChunk` dicts: `{ "chunk": "...", "index": N, "eof": bool, "chunk_size": N }`.

---

#### `write_file`

Writes content to a file inside the output directory. Uses **atomic writes** for `write` and `create` modes (see [Atomic Writes](#atomic-writes)). Returns a `/files/<filename>` URL for the written file.

The `invocation` dict accepts:

| Key | Type | Default | Description |
|---|---|---|---|
| `file_name` | `str` | *required* | Filename (not a full path) — file is created under `/app/data/` |
| `content` | `str` | *required* | Text content to write |
| `encoding` | `str` | `utf-8` | File encoding |
| `mode` | `str` | `write` | `write` (overwrite), `append`, or `create` (fail if exists) |
| `max_file_size` | `int` | `10 MB` | Maximum allowed file size in bytes |
| `chunk_size` | `int` | `8192` | Internal chunk size for streaming writes |

**Returns:**

```json
{ "status": "success", "file_url": "/files/research_report_abc123.txt" }
```

---

#### `upload_file`

Uploads a file from the output directory to an external URL via HTTP POST (multipart/form-data).

The `invocation` dict accepts:

| Key | Type | Description |
|---|---|---|
| `file_path` | `str` | Path to the file inside the output directory |
| `upload_url` | `str` | Destination URL to POST the file to |

---

#### `download_file`

Downloads a file from an external URL and saves it into the output directory. Uses streaming download to handle large files without excessive memory usage.

The `invocation` dict accepts:

| Key | Type | Default | Description |
|---|---|---|---|
| `file_url` | `str` | *required* | URL to download from |
| `headers` | `Dict` | `{}` | Optional HTTP headers (e.g. `Authorization`) |

**Returns:** The absolute path of the saved file inside the container.

---

#### `create_file_embedding`

Reads a file and generates a semantic embedding vector for its content using `sentence-transformers`.

The `invocation` dict accepts:

| Key | Type | Description |
|---|---|---|
| `file_path` | `str` | Path to the file |

**Returns:** `EmbeddingOutput` dict:

```json
{
  "embeddings": [[0.021, -0.143, ...]],
  "dimensions": 384,
  "count": 1,
  "model_name": "all-MiniLM-L6-v2"
}
```

---

#### `find_similar_texts_in_file_embeddings`

Loads a precomputed embeddings file (produced by `save_embeddings` from `file_embeddings.py`) and returns the top-K most similar texts to an input query.

The `invocation` dict accepts:

| Key | Type | Default | Description |
|---|---|---|---|
| `input_text` | `str` | *required* | Query text |
| `embeddings_file_path` | `str` | *required* | Path to a JSON embeddings file |
| `top_k` | `int` | `5` | Number of top results to return |
| `model_name` | `str` | `all-MiniLM-L6-v2` | Sentence transformer model to use |

**Returns:** `SimilaritySearchOutput` dict:

```json
{
  "results": [
    { "text": "...", "similarity_score": 0.912, "index": 3 }
  ],
  "query_text": "...",
  "model_name": "all-MiniLM-L6-v2"
}
```

---

### Atomic Writes

For `write` and `create` modes, `write_file` uses a write-then-rename strategy to prevent partial writes from corrupting the target file:

```
1. Create a temporary file in the same directory as the target
2. Write all content chunks to the temp file
3. os.replace(temp_file, target_file)   ← atomic on POSIX systems
```

If the write fails at any point, the temp file is cleaned up and the original target file is left untouched. `append` mode writes directly to the target file since atomicity is not meaningful for appends.

### Embedding Support

`file_embeddings.py` wraps `sentence-transformers` with a clean Pydantic-validated API. Models are cached in a module-level `_model_cache` dict after first load so repeated calls within the same container lifetime don't reload the model from disk.

The default model is `all-MiniLM-L6-v2` — a lightweight 384-dimension model that balances speed and quality for document similarity tasks. This is the **same model** used by the Memory server, so embeddings produced by both servers are comparable in the same vector space.

### Persistence

Files written via `write_file` are stored in `/app/data` inside the container, which is bind-mounted to `./file_handler_data` on the host:

```yaml
volumes:
  - ./file_handler_data:/app/data
```

Files survive container restarts. To clear all outputs, delete `./file_handler_data` on the host.

### Configuration (File Handler)

| Environment variable | Default | Description |
|---|---|---|
| `HOST_PORT` | `9191` | Server bind port |
| `HOST_ADDR` | `0.0.0.0` | Server bind address |

---

## Server Summary

| Server | Registry key | Port | Tools | Storage | Auth |
|---|---|---|---|---|---|
| Memory | `memory` | `9494` | 9 | ChromaDB (`./chroma_data`) | None |
| Web Search | `web_search` | `9393` | 8 | Stateless | None |
| Web Scraper | `web_scraper` | `9292` | 1 | Stateless | None |
| File Handler | `file_handler` | `9191` | 8 | Filesystem (`./file_handler_data`) | None |

---

## How the Research Agent Uses Each Server

The agent interacts with these servers at different points in the research lifecycle. The backend registers all four servers at startup; the LLM then decides which tools to call during each step's tool-calling loop.

| Phase | Server | Tools called | Purpose |
|---|---|---|---|
| `generate_plan` | Memory | `recall_memories` | Prime planning prompt with relevant prior knowledge |
| `_execute_step` | Web Search | `web_search`, `search_wikipedia`, `search_github` | Find relevant URLs and snippets |
| `_execute_step` | Web Scraper | `scrape_url` | Fetch full page content from URLs found by search |
| `_execute_step` | File Handler | `read_file`, `list_files` | Read any uploaded reference documents |
| `synthesize_results` | Memory | `store_memory` | Persist research session and per-insight records |
| `synthesize_results` | File Handler | `write_file` | Save the final Markdown research report |

In the v2 Orchestrator, the same servers are used but routing goes through the specialist sub-agents: the `SearchAgent` primarily drives `web_search` and `scrape_url`, while the `ReportComposer` output is saved manually by the Orchestrator after `synthesise()` completes.

---

## Running the Servers

Start all four MCP servers using the dedicated compose file:

```bash
docker compose -f docker-compose-mcp.yml up --build
```

Start everything (backend + frontend + MCP servers) with the full stack compose file:

```bash
docker compose -f docker-compose-full.yml up --build
```

Verify individual server health:

```bash
curl http://localhost:9494/health   # Memory
curl http://localhost:9393/health   # Web Search
curl http://localhost:9292/health   # Web Scraper
curl http://localhost:9191/health   # File Handler
```

---

## Adding a New MCP Server

1. **Create the server directory** under `mcp/` following the existing layout:

   ```
   mcp/my_server/
     Dockerfile
     main.py
     requirements.txt
     tools/
       my_tool.py
   ```

2. **Implement `main.py`** using the FastMCP pattern:

   ```python
   from mcp.server import FastMCP
   from starlette.requests import Request
   from starlette.responses import JSONResponse

   mcp = FastMCP(name="My Server", host="0.0.0.0", port=9090)

   @mcp.custom_route("/health", methods=["GET"])
   async def health_check(request: Request) -> JSONResponse:
       return JSONResponse({"status": "healthy", "service": "my-server"})

   @mcp.tool(name="my_tool", description="Does something useful.")
   def my_tool(param: str) -> dict:
       return {"result": param}

   if __name__ == "__main__":
       mcp.run(transport="streamable-http")
   ```

3. **Add a service to `docker-compose-mcp.yml`:**

   ```yaml
   mcp-my-server:
     build:
       context: ./mcp/my_server
       dockerfile: Dockerfile
     ports:
       - "9090:9090"
     restart: unless-stopped
     networks:
       - research-network
   ```

4. **Register the server in `backend/mcp_client.py`** inside `create_mcp_registry()`:

   ```python
   servers = [
       ...
       ("my_server", config.my_server_url),
   ]
   ```

5. **Add the URL to `Config`** (`backend/config.py`):

   ```python
   my_server_url: str = Field(
       default_factory=lambda: os.getenv("MY_SERVER_URL", "http://localhost:9090")
   )
   ```

   And expose the environment variable in your `.env` / Docker environment:

   ```
   MY_SERVER_URL=http://mcp-my-server:9090
   ```

The server's tools will be automatically discovered at registration time, added to `MCPServerRegistry.tool_specs`, and made available to the LLM's tool-calling loop without any further changes.
