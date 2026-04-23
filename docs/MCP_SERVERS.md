# MCP Servers — Architecture & Reference

> **Directory:** `mcp/`  
> **Protocol:** [Model Context Protocol](https://modelcontextprotocol.io/) via [FastMCP](https://github.com/jlowin/fastmcp)  
> **Transport:** `streamable-http` (HTTP/SSE)  
> **Compose file:** `docker-compose.yml`

---

## Table of Contents

1. [Overview](#overview)
2. [Shared Conventions](#shared-conventions)
   - [Transport & Endpoint Shape](#transport--endpoint-shape)
   - [Health Checks](#health-checks)
   - [Docker Conventions](#docker-conventions)
   - [Network](#network)
3. [Web Search Server](#web-search-server)
   - [Purpose](#purpose-web-search)
   - [Search Backends](#search-backends)
   - [Tools](#tools-web-search)
   - [Result Shapes](#result-shapes)
   - [Configuration](#configuration-web-search)
4. [Web Scraper Server](#web-scraper-server)
   - [Purpose](#purpose-web-scraper)
   - [Scraping Pipeline](#scraping-pipeline)
   - [Tools](#tools-web-scraper)
   - [Configuration](#configuration-web-scraper)
5. [File Handler Server](#file-handler-server)
   - [Purpose](#purpose-file-handler)
   - [Tool Modules](#tool-modules)
   - [Tools](#tools-file-handler)
   - [HTTP REST Endpoints](#http-rest-endpoints)
   - [Atomic Writes](#atomic-writes)
   - [Persistence](#persistence)
   - [Configuration](#configuration-file-handler)
6. [Server Summary](#server-summary)
7. [How the Research Agent Uses Each Server](#how-the-research-agent-uses-each-server)
8. [Running the Servers](#running-the-servers)
9. [Adding a New MCP Server](#adding-a-new-mcp-server)

---

## Overview

The research assistant uses three independent MCP servers, each running in its own Docker container and communicating with the backend over a shared Docker network. Each server exposes a set of **tools** — callable functions that the agent's LLM can invoke during the tool-calling loop in `_execute_step`.

```
┌──────────────────────────────────────────────────────────┐
│                  Backend (api_server.py)                 │
│                                                          │
│  MCPServerRegistry                                       │
│  ├── "web_search"    → http://mcp-web-search-server:9393 │
│  ├── "web_scraper"   → http://mcp-web-scraping-server:9292│
│  └── "file_handler"  → http://mcp-file-handler-server:9191│
└──────────────────────────────────────────────────────────┘
          │               │               │
          ▼               ▼               ▼
   ┌─────────────┐  ┌──────────┐  ┌──────────────┐
   │ Web Search  │  │ Web      │  │ File Handler │
   │ Server      │  │ Scraper  │  │ Server       │
   │ :9393       │  │ :9292    │  │ :9191        │
   └─────────────┘  └──────────┘  └──────┬───────┘
                                         │
                                    /app/data
                                       ./data
```

The backend connects to all three MCP servers at startup via `create_mcp_registry()`. If a server is unreachable, the registry logs a warning and continues without it — research sessions will proceed with degraded capability rather than failing entirely.

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

All three MCP servers share the same Dockerfile pattern:

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

All services — including the backend — are attached to the `research-network` bridge network defined in `docker-compose.yml`. Containers address each other by Docker service name (e.g. `mcp-web-search-server`).

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
URL validation (scheme + netloc required)
    │
    ▼
asyncio.Semaphore(3)              ← at most 3 concurrent fetches
    │
    ▼
_enforce_domain_rate(domain)      ← 2s minimum interval per domain
    │
    ▼
httpx.AsyncClient.get(url)        ← configurable timeout (default 10s),
    │                                follows redirects,
    │                                rotated User-Agent from pool of 6
    │                                (Chrome 123/124, Firefox 125, Safari 17)
    │                                retry once on HTTP 429 with Retry-After
    ▼
Content-Type check                ← reject PDFs, images, and other binary types early
    │
    ▼
Binary content guard              ← detect null bytes in first 8 KB;
    │                                catches mislabeled binary responses
    ▼
BeautifulSoup HTML parse
    │
    ├── _extract_metadata()        ← title, author, description, published date
    │     from <title>, <meta name/property> tags
    │
    ├── _remove_boilerplate()      ← decompose <script>, <style>, <nav>,
    │     <footer>, <header>, <aside>, and elements matching cookie/consent/
    │     sidebar/ad/newsletter/promo/pagination patterns
    │
    ├── _extract_main_content()    ← 3-stage heuristic:
    │     1. Semantic elements: <article>, <main>, role=main, content/post id
    │     2. Positive class/id patterns: content, article, post, entry, story
    │     3. Highest-scoring block by text volume × paragraph count ÷ link density
    │
    ├── (if clean_text=True) Collapse whitespace and blank lines
    │
    ├── Truncate to _SCRAPE_MAX_CHARS (default 100 000);
    │     appends "[... truncated — original N chars]" marker if cut
    │
    ├── (if include_links=True) Extract all <a href> tags,
    │     convert relative URLs to absolute
    │
    └── (if include_images=True) Extract all <img src> tags,
          convert relative URLs to absolute
```

The `httpx` client is a **module-level singleton** with `follow_redirects=True`. A User-Agent is selected randomly from a pool of six modern browser strings on every request. Concurrent requests are capped at 3 via an `asyncio.Semaphore`, and a 2-second minimum interval is enforced per domain. HTTP 429 responses are retried once after the server's `Retry-After` delay (capped at 10 s). Non-text `Content-Type` headers (PDFs, images, binary files) are rejected before parsing, and a null-byte scan catches mislabeled binary responses.

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
Title: Article Title                  (if found in <title>)
Author: Jane Smith                    (if found in <meta name="author">)
Published: 2024-06-18T10:00:00Z       (if found in article:published_time meta)
Description: Short page description   (if found in <meta name="description">)

--- PAGE CONTENT ---
<extracted text body — boilerplate stripped, main content focused>

--- LINKS ---          (if include_links=True)
Link text: https://example.com/other-page
...

--- IMAGES ---         (if include_images=True)
Alt text: https://example.com/image.jpg
...
```

Non-text content types (PDFs, images, binary files) return a `[SKIPPED]` message instead of raising. HTTP status errors and request errors raise a `ValueError` with a descriptive message.

### Configuration (Web Scraper)

| Environment variable | Default | Description |
|---|---|---|
| `HOST_PORT` | `9292` | Server bind port |
| `HOST_ADDR` | `0.0.0.0` | Server bind address |
| `SCRAPE_MAX_CHARS` | `100000` | Maximum extracted text characters returned; excess is truncated |
| `SCRAPE_TIMEOUT` | `10` | Per-request HTTP timeout in seconds |

The following constants are hardcoded in `main.py`:

| Constant | Value | Description |
|---|---|---|
| `_DOMAIN_MIN_INTERVAL` | `2.0 s` | Minimum delay between successive requests to the same domain |
| Semaphore | `3` | Maximum simultaneous in-flight scrape requests |

Pages requiring JavaScript rendering (SPAs) will return minimal or empty content since no headless browser is used. For JS-heavy sites, consider adding a Playwright or Puppeteer-based tool to this server.

---

## File Handler Server

> **Directory:** `mcp/file_handler/`  
> **Port:** `9191`  
> **Service name:** `mcp-file-handler-server`

### Purpose

Provides **sandboxed file I/O** for the research agent. All reads and writes are scoped to `/app/data/documents` inside the container, which is bind-mounted via the `./data` host volume. This isolates arbitrary file writes from the host filesystem while still making outputs accessible.

The server also exposes HTTP REST endpoints for direct browser-based file management (upload, list, delete).

### Tool Modules

The implementation is split across four Python modules under `mcp/file_handler/tools/`:

| Module | Responsibility |
|---|---|
| `file_reader.py` | Simple and streaming file reads, document text extraction (PDF, DOCX, ODT), directory listing |
| `file_writer.py` | Chunked atomic writes and append operations |
| `file_transfer.py` | HTTP upload and download |
| `file_discovery.py` | Allowlisted shell command execution for file discovery and system inspection |

### Tools (File Handler)

#### `list_files`

Recursively walks the output directory (`/app/data`) and returns a flat list of all files found.

**Returns:** Array of file info objects:

```json
[
  {
    "name": "research_report_abc123.txt",
    "absolute_path": "/app/data/documents/research_report_abc123.txt",
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

| Parameter | Type | Default | Description |
|---|---|---|---|
| `file_path` | `str` | *required* | Path to the file |
| `chunk_size` | `int` | `8192` | Bytes per chunk (512 B – 1 MB) |
| `encoding` | `str` | `utf-8` | File encoding (ignored for binary document formats) |
| `max_file_size` | `int` | `10 MB` | Maximum allowable file size |

**Yields:** `FileChunk` dicts: `{ "chunk": "...", "index": N, "eof": bool, "chunk_size": N }`.

---

#### `write_file`

Writes content to a file inside the output directory. Uses **atomic writes** for `write` and `create` modes (see [Atomic Writes](#atomic-writes)). Returns a `/files/<filename>` URL for the written file.

| Parameter | Type | Description |
|---|---|---|
| `file_name` | `str` | Filename (not a full path) — file is created under `/app/data/documents/` |
| `content` | `str` | Text content to write |
| `encoding` | `str` | File encoding (e.g. `utf-8`) |
| `mode` | `str` | `write` (overwrite), `append`, or `create` (fail if exists) |
| `max_file_size` | `int` | Maximum allowed file size in bytes |
| `chunk_size` | `int` | Internal chunk size for streaming writes |

**Returns:**

```json
{ "status": "success", "file_url": "/files/research_report_abc123.txt" }
```

---

#### `upload_file`

Uploads a file from the output directory to an external URL via HTTP POST (multipart/form-data).

| Parameter | Type | Description |
|---|---|---|
| `file_path` | `str` | Path to the file inside the output directory |
| `upload_url` | `str` | Destination URL to POST the file to |

---

#### `download_file`

Downloads a file from an external URL and saves it into the output directory. Uses streaming download to handle large files without excessive memory usage.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `file_url` | `str` | *required* | URL to download from |
| `headers` | `Dict` | `{}` | Optional HTTP headers (e.g. `Authorization`) |

**Returns:** The absolute path of the saved file inside the container.

---

#### `run_command`

Executes a read-only shell command inside the container and returns its output. The command's base token is checked against an allowlist before any subprocess is spawned — commands not in the list are rejected with an error message listing permitted commands.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `command` | `str` | *required* | The full shell command string to execute |
| `timeout` | `int` | `30` | Maximum seconds to wait before killing the process |

**Allowed base commands:** `ls`, `cat`, `head`, `tail`, `pwd`, `find`, `du`, `df`, `stat`, `file`, `wc`, `grep`, `awk`, `sed`, `sort`, `uniq`, `cut`, `tr`, `diff`, `echo`, `date`, `uptime`, `whoami`, `uname`, `ps`, `env`, `which`, `lsof`, `git`

**Returns:**

```json
{
  "stdout": "file1.txt\nfile2.md\n",
  "stderr": "",
  "returncode": 0
}
```

---

### HTTP REST Endpoints

In addition to MCP tools, the server exposes three HTTP endpoints for direct browser-based file management:

| Method | Path | Description |
|---|---|---|
| `GET` | `/files` | Returns a JSON array of all files in the output directory |
| `POST` | `/files/upload` | Accepts a `multipart/form-data` upload and saves the file to the output directory |
| `DELETE` | `/files/{filename}` | Deletes a previously uploaded file from the output directory |

Filenames are sanitised with `os.path.basename()` on all write and delete operations to prevent path traversal.

---

### Atomic Writes

For `write` and `create` modes, `write_file` uses a write-then-rename strategy to prevent partial writes from corrupting the target file:

```
1. Create a temporary file in the same directory as the target
2. Write all content chunks to the temp file
3. os.replace(temp_file, target_file)   ← atomic on POSIX systems
```

If the write fails at any point, the temp file is cleaned up and the original target file is left untouched. `append` mode writes directly to the target file since atomicity is not meaningful for appends.

### Persistence

Files written via `write_file` are stored in `/app/data/documents` inside the container. The host volume mount is:

```yaml
volumes:
  - ./data:/app/data
```

Files survive container restarts. To clear all outputs, delete `./data/documents` on the host.

### Configuration (File Handler)

| Environment variable | Default | Description |
|---|---|---|
| `HOST_PORT` | `9191` | Server bind port |
| `HOST_ADDR` | `0.0.0.0` | Server bind address |

---

## Server Summary

| Server | Registry key | Port | Tools | Storage | Auth |
|---|---|---|---|---|---|
| Web Search | `web_search` | `9393` | 7 | Stateless | Optional (`SEARCH_SERVER_API_KEY`) |
| Web Scraper | `web_scraper` | `9292` | 1 | Stateless | Optional (`SCRAPER_SERVER_API_KEY`) |
| File Handler | `file_handler` | `9191` | 7 | Filesystem (`./data`) | Optional (`FILE_SERVER_API_KEY`) |

---

## How the Research Agent Uses Each Server

The agent interacts with these servers at different points in the research lifecycle. The backend registers all three MCP servers at startup; the LLM then decides which tools to call during each step's tool-calling loop.

| Phase | Component | Call | Purpose |
|---|---|---|---|
| `_execute_step` | Web Search MCP | `web_search`, `search_wikipedia`, `search_github` | Find relevant URLs and snippets |
| `_execute_step` | Web Scraper MCP | `scrape_url` | Fetch full page content from URLs found by search |
| `_execute_step` | File Handler MCP | `read_file`, `list_files` | Read any uploaded reference documents |
| `synthesize_results` | File Handler MCP | `write_file` | Save the final Markdown research report |

In the v2 Orchestrator, the same servers are used but routing goes through the specialist sub-agents: the `SearchAgent` primarily drives `web_search` and `scrape_url`, while the `ReportComposer` output is saved manually by the Orchestrator after `synthesise()` completes.

---

## Running the Servers

Start everything (backend + Neo4j + frontend + all MCP servers):

```bash
docker compose up --build
```

Or using the full-stack compose file explicitly:

```bash
docker compose -f docker-compose-full.yml up --build
```

Verify individual MCP server health:

```bash
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

3. **Add a service to `docker-compose.yml`:**

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
     healthcheck:
       test: ["CMD", "curl", "-f", "http://localhost:9090/health"]
       interval: 5s
       timeout: 3s
       retries: 10
       start_period: 10s
   ```

4. **Register the server in `backend/mcp_client.py`** inside `create_mcp_registry()`:

   ```python
   await registry.register(
       "my_server",
       url=config.my_server_url,
       transport="streamable-http",
       builtin=True,
   )
   ```

5. **Add the URL to `Config`** (`backend/config.py`):

   ```python
   my_server_url: str = Field(
       default_factory=lambda: os.getenv("MY_SERVER_URL", "http://localhost:9090/mcp")
   )
   ```

   And expose the environment variable in your `.env` / Docker environment:

   ```
   MY_SERVER_URL=http://mcp-my-server:9090/mcp
   ```

The server's tools will be automatically discovered at registration time, added to `MCPServerRegistry.tool_specs`, and made available to the LLM's tool-calling loop without any further changes.
