# API Server — Architecture & Reference

> **Framework:** [FastAPI](https://fastapi.tiangolo.com/)  
> **Default port:** `9999` (set via Docker / environment)

---

## Table of Contents

1. [Overview](#overview)
2. [Startup & Lifespan](#startup--lifespan)
3. [Agent Modes](#agent-modes)
4. [CORS](#cors)
5. [REST Endpoints](#rest-endpoints)
   - [GET /](#get-)
   - [GET /health](#get-health)
   - [GET /agent/research-methods](#get-agentresearch-methods)
   - [GET /project-docs](#get-project-docs)
   - [GET /project-docs/{filename}](#get-project-docsfilename)
   - [GET /mcp/servers](#get-mcpservers)
   - [POST /mcp/servers](#post-mcpservers)
   - [GET /mcp/servers/{name}](#get-mcpserversname)
   - [DELETE /mcp/servers/{name}](#delete-mcpserversname)
   - [GET /mcp/tools](#get-mcptools)
   - [GET /config](#get-config)
   - [POST /config](#post-config)
   - [POST /chat](#post-chat)
   - [Knowledge Graph Endpoints](#knowledge-graph-endpoints)
6. [WebSocket Endpoint](#websocket-endpoint)
   - [/ws/research (Orchestrator)](#wsresearch-orchestrator)
7. [Message Contract](#message-contract)
   - [Inbound Messages](#inbound-messages)
   - [Outbound Messages](#outbound-messages)
8. [Serialisation Helper](#serialisation-helper)
9. [Error Handling](#error-handling)
10. [Environment Variables](#environment-variables)

---

## Overview

`api_server.py` is the single entry point for all client–server communication. It exposes:

- A small set of **REST endpoints** for health checks, config management, MCP server introspection, file upload, and a simple streaming chat.
- A single **WebSocket endpoint** (`/ws/research`) that implements the full interactive research loop powered by the multi-agent `Orchestrator`.

---

## Startup & Lifespan

The server uses FastAPI's `lifespan` context manager to initialise and tear down all shared state exactly once, rather than on every request.

```
Application start
      │
      ▼
create_model_backend(config)         ← selects backend from MODEL_BACKEND env var
      │
      ▼
create_mcp_registry(config)          ← connects to all MCP servers
      │
      ├── web_search   (SEARCH_SERVER_URL)
      ├── fetch        (FETCH_SERVER_URL)
      └── file_handler (FILE_SERVER_URL)
      │
      ▼
AsyncLongTermMemory(...).async_init()  ← in-process Neo4j-backed memory
      │                                    (flat vector store + knowledge graph)
      │
      ├── agent                = ResearchAgent | SelfOptimizingAgent
      │                            (stored on app.state.agent; used for /mcp/* and /chat)
      ├── self_optimizing_agent = SelfOptimizingAgent
      │                            (always created; powers the self_optimize WS command)
      ├── agent_pool           = AgentPool.from_single_backend(backend)
      │                            (shared across all WebSocket sessions)
      ├── session_store        = SessionStore()          (in-memory, TTL-based)
      └── session_manager      = ResearchSessionManager() (owns all background tasks)
      │
      ▼
warm_up_embeddings()                 ← pre-loads the sentence-transformer model
      │
      ▼
mcp_registry.start_keepalive()       ← 60 s pings to all MCP servers
      │
     [serve requests]
      │
Application shutdown
      ├── session_manager.shutdown()  ← cancels all running research tasks
      ├── agent_pool.close()
      └── mcp_registry.close()
```

There is **no standalone memory MCP server** — long-term memory is handled in-process by `AsyncLongTermMemory`, backed by Neo4j (see [docs/RAG_NOTES.md](./RAG_NOTES.md)).

The `agent_pool` is created once at startup and shared across all WebSocket connections. Each incoming `query` message creates a **fresh, isolated `Orchestrator`** instance (via `_make_orchestrator()`) so concurrent research sessions never share mutable plan or execution state. The expensive shared resources — `AgentPool`, MCP registry, and long-term memory store — are reused from app state.

---

## Agent Modes

The `agent_mode` field in `Config` (set via the `AGENT_MODE` environment variable, defaulting to `"research"`) selects which agent class is instantiated for REST endpoints:

| `agent_mode` value | Agent class | Notes |
|---|---|---|
| `"research"` *(default)* | `ResearchAgent` | Single-agent helper used by the `/chat` and `/mcp/*` REST endpoints |
| `"chat"` | `ResearchAgent` | Same class; mode affects prompt behaviour |
| `"self-optimization"` | `SelfOptimizingAgent` | Agent that analyses stored memories and the knowledge graph, then rewrites `RESEARCH-METHODS.md` |

The `agent` instance is used only by the REST endpoints (`/chat`, `/mcp/*`). The WebSocket `/ws/research` endpoint always uses a fresh `Orchestrator` per session regardless of `AGENT_MODE`. A dedicated `SelfOptimizingAgent` is also created at startup (independent of `AGENT_MODE`) to power the `self_optimize` WebSocket command.

---

## CORS

The server enables CORS for **all origins** (`*`) with all methods and headers allowed. This is intentionally permissive for local development. For production deployments, replace the wildcard with an explicit list of allowed frontend origins.

---

## REST Endpoints

### GET `/`

Health ping. Returns the API name, version string, and running status.

**Response**

```json
{
  "name": "Research Agent API",
  "version": "1.0.0",
  "status": "running"
}
```

---

### GET `/health`

Detailed health check. Confirms the agent has been initialised.

**Response**

```json
{
  "status": "healthy",
  "timestamp": "2026-03-06T14:22:01.123456+00:00",
  "agent_initialized": true
}
```

**503** — returned if the agent failed to initialise during startup (rare; typically a missing MCP connection).

---

### GET `/agent/research-methods`

Returns the contents of the `RESEARCH-METHODS.md` instruction document that the Orchestrator injects into planning. This is the playbook the self-optimization pipeline rewrites.

**Response**

```json
{ "content": "# Research Methods\n\n..." }
```

**404** — the instruction file could not be found.

---

### GET `/project-docs`

Lists the Markdown documentation filenames available under the backend `docs/` directory (bind-mounted from the repo `docs/` folder).

**Response**

```json
{ "docs": ["API_SERVER.md", "BENCHMARKING.md", "CLI.md", "MCP_SERVERS.md", "ORCHESTRATOR.md", "RAG_NOTES.md"] }
```

> **Note:** the route is `/project-docs`, not `/docs` — the latter is reserved by FastAPI for the Swagger UI.

---

### GET `/project-docs/{filename}`

Returns the raw content of a single documentation file. The filename is sanitised (`os.path.basename`, `.md`-only) to prevent path traversal.

**Response**

```json
{ "filename": "API_SERVER.md", "content": "# API Server — Architecture & Reference\n\n..." }
```

**400** — invalid filename. **404** — file not found.

---

### GET `/mcp/servers`

Returns full info for every registered MCP server (built-in and user-defined), including connection status and transport.

**Response**

```json
{
  "servers": [
    { "name": "web_search", "transport": "streamable-http", "builtin": true, "status": "connected" },
    { "name": "fetch", "transport": "streamable-http", "builtin": true, "status": "connected" },
    { "name": "file_handler", "transport": "streamable-http", "builtin": true, "status": "connected" }
  ]
}
```

---

### POST `/mcp/servers`

Registers a new **user-defined** MCP server at runtime and persists it. Supports `stdio`, `sse`, and `streamable-http` transports. Built-in servers cannot be replaced.

**Request body** (`application/json`)

| Field | Type | Required | Description |
|---|---|---|---|
| `name` | `string` | ✓ | Unique server name |
| `transport` | `string` | ✗ | `streamable-http` *(default)*, `sse`, or `stdio` |
| `url` | `string` | ✗ | Endpoint URL (for `sse` / `streamable-http`) |
| `command` | `string` | ✗ | Executable (for `stdio`) |
| `args` | `string[]` | ✗ | Command arguments (for `stdio`) |
| `env` | `object` | ✗ | Environment variables (for `stdio`) |
| `headers` | `object` | ✗ | Extra HTTP headers |

**Response** — the stored server config. **400** — missing name / unsupported transport. **409** — name collides with a built-in server. **500** — registration failed.

---

### GET `/mcp/servers/{name}`

Returns the stored config plus live capability listings (tools, resources, resource templates, prompts) for a single server. If the server is registered but not connected, capability lists are empty and `status` is `"disconnected"`.

**404** — server not found.

---

### DELETE `/mcp/servers/{name}`

Removes a user-defined MCP server and persists the change.

**Response**

```json
{ "status": "deleted", "name": "my_server" }
```

**403** — built-in servers cannot be deleted. **404** — server not found.

---

### GET `/mcp/tools`

Lists every tool available across all connected MCP servers. The response is the raw tool-spec list used internally for model tool-calling.

**Response**

```json
{
  "tools": [
    {
      "type": "function",
      "name": "web_search",
      "description": "Search the web for a query",
      "parameters": { "type": "object", "properties": { "query": { "type": "string" } }, "required": ["query"] }
    },
    ...
  ]
}
```

**503** — agent not initialised.

---

### GET `/config`

Returns a snapshot of the current `Config` object (all fields, including model names and server URLs). Sensitive values such as API keys are included — restrict access to this endpoint in production.

**Response**

```json
{
  "config": {
    "model_backend": "ollama",
    "openai_heavy_model": "gpt-5.2",
    "openai_light_model": "gpt-5-nano",
    "ollama_heavy_model": "nemotron-3-nano",
    "max_iterations": 5,
    "default_temperature": 0.7,
    ...
  }
}
```

---

### POST `/config`

Updates model configuration at runtime, persists it to `configs/model_settings.json`, and **reinitialises the model backend and `agent_pool`** so changes take effect for all new research sessions without a restart. In-flight sessions are unaffected. (Note: persisted settings are re-applied on the next startup.)

**Request body** (`application/json`) — `ConfigUpdate`. All fields are optional; only the ones provided are applied. Credential/model fields are routed to the currently-selected backend.

| Field | Type | Description |
|---|---|---|
| `model_backend` | `string` | Switch active backend: `openai`, `azure`, `aws`, `bedrock`, `gcp`, `ollama`, `huggingface`, `anthropic` |
| `api_key` | `string` | API key for the selected backend |
| `api_base_url` | `string` | Base URL / endpoint for the selected backend (for `bedrock`, the AWS region) |
| `heavy_model` | `string` | Model for heavy tasks (planning, synthesis, report) |
| `light_model` | `string` | Model for light tasks (search execution, analyst extraction) |
| `model` | `string` | Legacy single-model field |
| `root_model_override` / `search_model_override` / `analyst_model_override` / `qa_model_override` | `string` | Per-agent model override (empty string clears it) |
| `root_temperature` / `search_temperature` / `analyst_temperature` / `qa_temperature` | `float` | Per-agent sampling temperature |
| `root_top_p` / `search_top_p` / `analyst_top_p` / `qa_top_p` | `float` | Per-agent nucleus-sampling threshold |
| `root_max_tokens` / `search_max_tokens` / `analyst_max_tokens` / `qa_max_tokens` | `int` | Per-agent max output tokens |

**Response**

```json
{
  "status": "success",
  "config": { "model_backend": "openai", "openai_heavy_model": "gpt-5.2", ... }
}
```

**500** — invalid configuration or failed to reinitialise the model backend / agent pool.

---

### POST `/chat`

Streaming chat endpoint for direct conversational interaction with the agent, bypassing the full research pipeline. Useful for quick questions or testing model connectivity.

**Request body** (`application/json`)

| Field | Type | Required | Description |
|---|---|---|---|
| `content` | `string` | ✓ | The user's message |
| `role` | `string` | ✗ | Message role (e.g. `user`); ignored by the handler |

**Response** — `text/plain` streamed in real-time as the model generates tokens.

```
The latest advancements in renewable energy include...
```

**503** — agent not initialised.

---

### Knowledge Graph Endpoints

All graph endpoints query the `KnowledgeGraph` layer of `AsyncLongTermMemory`. They return **503** (`{"detail": "Knowledge graph not available"}`) if Neo4j is not connected.

#### GET `/graph/stats`

Returns entity, relationship, community, contradiction, document, claim, and hierarchy counts.

#### GET `/graph/entities`

Semantic entity search across all typed node labels.

| Param | Type | Default | Description |
|---|---|---|---|
| `query` | `string` | `""` | Semantic search query. If empty, returns most recent entities. |
| `limit` | `int` | `10` | Max results (1–100) |
| `include_hierarchy` | `bool` | `false` | Include IS_A ancestor names |
| `node_type` | `string` | `""` | Filter by entity type (comma-separated: `person,technology`) |

#### GET `/graph/relationships`

Traverse typed relationships (CAUSES, ENABLES, USES, etc.) from a named entity.

| Param | Type | Default | Description |
|---|---|---|---|
| `entity` | `string` | `""` | Seed entity name |
| `max_hops` | `int` | `2` | Traversal depth (1–4) |
| `min_confidence` | `float` | `0.0` | Post-filter by confidence |

#### GET `/graph/communities`

List all community cluster summaries.

#### GET `/graph/contradictions`

Return CONTRADICTS edges, optionally filtered to a named entity.

| Param | Type | Default | Description |
|---|---|---|---|
| `entity` | `string` | `""` | Filter by entity name |
| `limit` | `int` | `20` | Max results (1–100) |

#### GET `/graph/provenance`

Return Document nodes linked to entities via SOURCED_FROM edges.

| Param | Type | Default | Description |
|---|---|---|---|
| `entity` | `string` | `""` | Filter by entity name |

#### GET `/graph/paths`

Find shortest paths between two named entities.

| Param | Type | Default | Description |
|---|---|---|---|
| `source` | `string` | *(required)* | Source entity name |
| `target` | `string` | *(required)* | Target entity name |
| `max_depth` | `int` | `4` | Max path length |

#### GET `/graph/session/{session_id}`

Return entities and relationships created during a specific research session.

#### GET `/graph/claims`

Search claims by semantic query, entity, or status.

| Param | Type | Default | Description |
|---|---|---|---|
| `query` | `string` | `""` | Semantic search query |
| `entity` | `string` | `""` | Filter by linked entity name |
| `status` | `string` | `""` | Filter by claim status (`supported`, `disputed`, `unverified`, `retracted`) |
| `limit` | `int` | `20` | Max results (1–100) |

#### PATCH `/graph/claims/{claim_id}`

Update a claim's status.

| Param | Type | Default | Description |
|---|---|---|---|
| `status` | `string` | *(required)* | New status value |

#### GET `/graph/documents`

Search documents by semantic query, optionally filtered.

| Param | Type | Default | Description |
|---|---|---|---|
| `query` | `string` | `""` | Semantic search query |
| `doc_type` | `string` | `""` | Filter by document type |
| `min_credibility` | `float` | `0.0` | Minimum credibility score |
| `limit` | `int` | `20` | Max results (1–100) |

#### POST `/graph/prune`

Prune stale graph elements. Runs in dry-run mode by default.

| Param | Type | Default | Description |
|---|---|---|---|
| `min_confidence` | `float` | `0.1` | Remove edges below this confidence |
| `max_age_days` | `int` | `180` | Remove edges not confirmed in this many days |
| `dry_run` | `bool` | `true` | When true, only count — do not delete |

---

## WebSocket Endpoint

### `/ws/research` (Orchestrator)

Powered by the multi-agent `Orchestrator`. Each `query` message creates a **new, isolated research session** with its own `Orchestrator` instance so concurrent connections do not share state. The session ID is returned immediately so the client can reconnect if the connection drops.

The full pipeline:

```
SearchAgent → AnalystAgent → LoopAgent (QA) → [retry if contradictions] → ReportComposer
```

Three-phase flow:

1. **Plan** — generate a `ResearchPlan` for the user's query.
2. **Execute** — run each plan step through the sub-agent pipeline with tool-calling.
3. **Synthesise** — multi-pass synthesis via `ReportComposer`; Markdown document emitted as `report` event.

See [ORCHESTRATOR.md](./ORCHESTRATOR.md) for the complete architecture reference.

---

## Message Contract

The `/ws/research` endpoint uses the JSON message protocol below.

### Inbound Messages

All messages sent **from the client to the server** are JSON objects with a `type` field.

#### `query`

Starts a new research session. Triggers plan generation.

```json
{
  "type": "query",
  "content": "What are the long-term cardiovascular effects of GLP-1 agonists?",
  "research_depth": "shallow"
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `content` | string | ✓ | The research question |
| `research_depth` | string | ✗ | `shallow` *(default)*, `moderate`, or `deep`. Controls whether the QA/contradiction loop runs and how many retries it gets. Invalid values fall back to `shallow`. |

---

#### `approve_plan`

Approves the pending plan and begins execution + synthesis.

```json
{
  "type": "approve_plan",
  "planId": "3f7a2b1c-..."
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `planId` | string | ✓ | UUID from the `plan` event |

---

#### `modify_plan`

Requests a revised plan incorporating free-text feedback. The server regenerates the plan and emits a new `plan` event.

```json
{
  "type": "modify_plan",
  "planId": "3f7a2b1c-...",
  "feedback": "Please add a step that searches specifically for meta-analyses and RCTs."
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `planId` | string | ✓ | UUID of the plan to modify |
| `feedback` | string | ✓ | Free-text description of desired changes |

---

#### `deny_plan`

Rejects the pending plan and resets agent state. The client may then send a new `query`.

```json
{
  "type": "deny_plan",
  "planId": "3f7a2b1c-..."
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `planId` | string | ✓ | UUID of the plan to reject |

---

#### `resume`

Reconnects to an existing research session by `session_id`. The server replays the full event log from the beginning, then streams any live events still being produced by the background execution task. If the session has already completed, the client receives the full replay immediately.

```json
{
  "type": "resume",
  "session_id": "3f7a2b1c-..."
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `session_id` | string | ✓ | The session ID returned in a prior `session_created` event |

---

#### `self_optimize`

Runs the five-phase self-optimization workflow on the dedicated `SelfOptimizingAgent`: it analyses session logs and the knowledge graph, then rewrites `RESEARCH-METHODS.md`. Available regardless of `AGENT_MODE`. Emits an `optimize_started` event, a stream of `optimize_progress` events, and a terminal `optimize_complete` event.

```json
{
  "type": "self_optimize"
}
```

---

### Outbound Messages

All messages sent **from the server to the client** have at minimum `type` and `message` fields.

```typescript
interface ServerMessage {
  type: string;
  message: string;
  data?: Record<string, any>;   // type-dependent payload
  error?: string;               // present on error types
  plan?: ResearchPlan;          // present on type="plan"
}
```

---

#### `session_created`

Sent immediately after a `query` message, before plan generation begins. Contains the `session_id` the client should persist for reconnection.

```json
{
  "type": "session_created",
  "session_id": "3f7a2b1c-..."
}
```

---

#### `session_resumed`

Sent after a successful `resume` before the event replay begins.

```json
{
  "type": "session_resumed",
  "session_id": "3f7a2b1c-...",
  "state": "executing",
  "complete": false,
  "event_count": 42
}
```

---

#### `status`

A progress or informational update. May include a `data` object with step-level detail. Emitted frequently throughout all phases.

```json
{
  "type": "status",
  "message": "[QA] Auditing findings for step 2…"
}
```

Some `status` events carry additional QA data:

```json
{
  "type": "status",
  "message": "[QA] 2 contradiction(s) flagged in step 2. Routing back for investigation…",
  "data": {
    "contradictions": [
      {
        "id": "...",
        "source_a": "https://...",
        "claim_a": "Drug X reduces MACE by 14%",
        "source_b": "https://...",
        "claim_b": "Drug X reduces MACE by 9%",
        "context": "Primary efficacy endpoint",
        "resolved": false,
        "flagged_at": "2026-03-06T14:30:00+00:00"
      }
    ]
  }
}
```

---

#### `plan`

The research plan is ready for the user to review. Sent after `query` or `modify_plan`.

```json
{
  "type": "plan",
  "message": "Research plan ready. Awaiting approval.",
  "plan": {
    "id": "3f7a2b1c-...",
    "goal": "Assess long-term cardiovascular effects of GLP-1 agonists.",
    "steps": [
      { "id": 1, "name": "Step 1", "description": "Search for landmark RCTs…", "status": "pending" },
      { "id": 2, "name": "Step 2", "description": "Retrieve meta-analyses…",   "status": "pending" }
    ]
  }
}
```

---

#### `step_start`

A plan step has begun execution.

```json
{
  "type": "step_start",
  "message": "Step 1: Search for landmark RCTs…",
  "data": {
    "step": { "id": 1, "name": "Step 1", "description": "...", "status": "in_progress" }
  }
}
```

---

#### `step_complete`

A plan step finished successfully.

```json
{
  "type": "step_complete",
  "message": "Step 1 complete.",
  "data": {
    "step":   { "id": 1, "status": "completed", ... },
    "result": "Found 4 landmark RCTs: SUSTAIN-6, LEADER, EMPA-REG, DECLARE-TIMI 58..."
  }
}
```

---

#### `step_failed`

A plan step failed. Research continues with the remaining steps.

```json
{
  "type": "step_failed",
  "message": "Step 2 failed",
  "error": "Connection timeout to fetch MCP server"
}
```

---

#### `research_complete`

All plan steps have finished executing. Synthesis is about to begin.

```json
{
  "type": "research_complete",
  "message": "All steps executed. Proceeding to synthesis…"
}
```

---

#### `synthesis_progress`

Incremental progress updates emitted during the multi-pass synthesis phase (outline → section drafting → assembly).

```json
{
  "type": "synthesis_progress",
  "message": "Drafting section 2 of 5: Cardiovascular Outcomes…"
}
```

---

#### `report`

The final Markdown research report from the `ReportComposer`. Sent as the last substantive event of a session.

```json
{
  "type": "report",
  "message": "Research report complete.",
  "data": {
    "document": "# GLP-1 Agonists: Cardiovascular Outcomes\n\n## Executive Summary\n..."
  }
}
```

---

#### `plan_denied`

Acknowledges the plan rejection and suggests next steps.

```json
{
  "type": "plan_denied",
  "message": "Understood. Consider narrowing your query to a specific drug class or trial population…"
}
```

---

#### `optimize_started` / `optimize_progress` / `optimize_complete`

Emitted by the `self_optimize` command. `optimize_started` is sent once when the workflow begins, followed by a stream of `optimize_progress` updates as each phase runs, and a terminal `optimize_complete` carrying the result.

```json
{ "type": "optimize_started",  "message": "Self-optimization workflow starting…" }
{ "type": "optimize_progress", "message": "Analysing 42 stored memories…" }
{ "type": "optimize_complete", "message": "RESEARCH-METHODS.md updated." }
```

---

#### `error`

An error occurred. The WebSocket remains open; the client may send a new `query`.

```json
{
  "type": "error",
  "message": "planId is required to approve a plan."
}
```

---

## Serialisation Helper

`make_serializable(obj)` is a recursive converter that ensures any research domain object can be safely passed to `websocket.send_json()`.

| Input type | Action |
|---|---|
| `ResearchStep` or `ResearchPlan` | Calls `.to_dict()`, then recurses |
| `dict` | Recurses over all values |
| `list` / `tuple` | Recurses over all items |
| Anything else (`str`, `int`, `float`, `bool`, `None`) | Passes through unchanged |

This is applied to every outbound WebSocket message before transmission.

---

## Error Handling

| Scenario | Behaviour |
|---|---|
| Agent not initialised | REST endpoints return **503**; WebSocket sends `type="error"` then closes |
| Empty `query` content | WebSocket sends `type="error"`, connection remains open |
| Missing `planId` | WebSocket sends `type="error"`, connection remains open |
| `modify_plan` failure | Caught, sends `type="error"`, connection remains open |
| All steps failed | Orchestrator emits `type="error"` after final failure |
| Unhandled exception in WS loop | Logged, sends `type="error"` to client |
| WebSocket disconnect | Background execution task continues; client can reconnect via `resume` |

---

## Environment Variables

All variables are read via the `Config` Pydantic model at startup. The most operationally relevant ones for the API server are below; see [config.py](../backend/config.py) and the root `README.md` for the complete list.

| Variable | Default | Description |
|---|---|---|
| `MODEL_BACKEND` | `ollama` | Model provider: `openai`, `azure`, `aws`, `bedrock`, `gcp`, `ollama`, `huggingface` |
| `AGENT_MODE` | `research` | Agent class for REST endpoints: `research`, `chat`, `self-optimization` |
| `LOG_LEVEL` | `20` (INFO) | Python logging level integer |
| `SEARCH_SERVER_URL` | `http://localhost:9393/mcp` | MCP web search server URL |
| `FETCH_SERVER_URL` | `http://localhost:9292/mcp` | MCP fetch (web scraper) server URL |
| `FILE_SERVER_URL` | `http://localhost:9191/mcp` | MCP file handler server URL |
| `OPENAI_API_KEY` | — | Required when `MODEL_BACKEND=openai` |
| `OPENAI_HEAVY_MODEL` | `gpt-5.2` | OpenAI model for heavy tasks (planning, synthesis) |
| `OPENAI_LIGHT_MODEL` | `gpt-5-nano` | OpenAI model for light tasks (search, analyst) |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama server base URL |
| `OLLAMA_HEAVY_MODEL` / `OLLAMA_LIGHT_MODEL` | `nemotron-3-nano` | Ollama model names |
| `AWS_MODEL` | `global.anthropic.claude-sonnet-4-5-20250929-v1:0` | Default Bedrock model ID |
| `AWS_HEAVY_MODEL` / `AWS_LIGHT_MODEL` | `…sonnet-4-6` / `…haiku-4-5…` | Bedrock heavy/light model IDs |
| `AWS_BASE_URL` / `AWS_API_KEY` | — | Required when `MODEL_BACKEND=aws` (OpenAI-compatible gateway) |
| `AWS_REGION` | `us-east-1` | Required when `MODEL_BACKEND=bedrock` (native boto3) |
| `AZURE_OPENAI_ENDPOINT` / `AZURE_OPENAI_API_KEY` | — | Required when `MODEL_BACKEND=azure` |
| `AZURE_OPENAI_DEPLOYMENT` | — | Azure deployment name |
| `GCP_ENDPOINT` | `https://us-central1-aiplatform.googleapis.com/v1` | GCP Vertex AI endpoint |
| `GCP_MODEL` | `gemini-2.5-pro` | Vertex AI model name |
| `GCP_API_KEY` | — | Required when `MODEL_BACKEND=gcp` |
| `MAX_ITERATIONS` | `5` | Max tool-calling iterations per research step |
| `MAX_QA_RETRIES` | `2` | Max LoopAgent contradiction-retry cycles per step |
| `MAX_TOKENS` | `4096` | Max output tokens per generation call |
| `DEFAULT_TEMPERATURE` | `0.7` | Default generation temperature |
| `MAX_SOURCES_PER_QUERY` | `5` | Max sources retrieved per search step |
| `MAX_DEPTH` | `2` | Max recursive scraping depth |
| `MAX_WORKERS` | `10` | Concurrent steps per research session |
| `MAX_CONCURRENT_SESSIONS` | `10` | Max research sessions executing simultaneously (0 = unlimited) |
| `ENABLE_PARALLEL_EXECUTION` | `True` | Run independent plan steps concurrently |
| `NEO4J_URI` | `bolt://localhost:7687` | Long-term memory Neo4j Bolt URI |
| `NEO4J_USER` / `NEO4J_PASSWORD` | `neo4j` / `research_pass` | Neo4j credentials |
| `EMBEDDINGS_ENABLED` | `true` | Enable in-process sentence-transformer embeddings |
| `MCP_KEEPALIVE_INTERVAL` | `60` | Seconds between MCP server keepalive pings |
