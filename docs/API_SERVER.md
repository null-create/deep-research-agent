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
   - [GET /mcp/servers](#get-mcpservers)
   - [GET /mcp/tools](#get-mcptools)
   - [POST /files/upload](#post-filesupload)
   - [GET /config](#get-config)
   - [POST /config](#post-config)
   - [POST /chat](#post-chat)
6. [WebSocket Endpoint](#websocket-endpoint)
   - [/ws/research (Orchestrator)](#wsresearch-orchestrator)
7. [Shared Message Contract](#shared-message-contract)
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
      ├── memory       (MEMORY_SERVER_URL)
      ├── web_search   (SEARCH_SERVER_URL)
      ├── web_scraper  (SCRAPER_SERVER_URL)
      └── file_handler (FILE_SERVER_URL)
      │
      ├── agent      = AdvancedResearchAgent | SelfOptimizingAgent
      │                  (stored on app.state.agent; used for /mcp/* and /chat)
      ├── agent_pool = AgentPool.from_single_backend(backend)
      │                  (shared across all WebSocket sessions)
      ├── session_store    = SessionStore()          (in-memory, TTL-based)
      └── session_manager  = ResearchSessionManager() (owns all background tasks)
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

The `agent_pool` is created once at startup and shared across all WebSocket connections. Each incoming `query` message creates a **fresh, isolated `Orchestrator`** instance (via `_make_orchestrator()`) so concurrent research sessions never share mutable plan or execution state.

---

## Agent Modes

The `agent_mode` field in `Config` (set via the `AGENT_MODE` environment variable, defaulting to `"research"`) selects which agent class is instantiated for REST endpoints:

| `agent_mode` value | Agent class | Notes |
|---|---|---|
| `"research"` *(default)* | `AdvancedResearchAgent` | Full research pipeline with parallel step execution and source credibility scoring |
| `"chat"` | `AdvancedResearchAgent` | Same class; mode affects prompt behaviour |
| `"self-optimization"` | `SelfOptimizingAgent` | Experimental agent that analyses stored memories and develops new research strategies |

The `agent` instance is used only by REST endpoints (`/chat`, `/mcp/*`) and the v1 AdvancedResearchAgent codepath. The WebSocket `/ws/research` endpoint always uses a fresh `Orchestrator` per session regardless of `AGENT_MODE`.

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

### GET `/mcp/servers`

Lists the names of all MCP servers that are currently registered and connected.

**Response**

```json
{
  "servers": ["memory", "web_search", "web_scraper", "file_handler"]
}
```

**503** — agent not initialised.

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

### POST `/files/upload`

Uploads a file and saves it under `backend/data/uploaded_files/<filename>`. The file is stored on the server's local filesystem and is then accessible to the `file_handler` MCP server.

**Request** — `multipart/form-data`

| Field | Type | Required | Description |
|---|---|---|---|
| `file` | binary | ✓ | The file to upload |

**Response**

```json
{
  "status": "success",
  "filename": "my_document.pdf"
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
    "model_backend": "openai",
    "openai_model": "gpt-5.2",
    "ollama_model": "nemotron-3-nano",
    "max_iterations": 10,
    "default_temperature": 0.7,
    ...
  }
}
```

---

### POST `/config`

Updates the active agent's model backend and/or model name at runtime. Reinitialises the `AdvancedResearchAgent` with the new configuration — the `Orchestrator` is **not** reinitialised (restart the server to apply backend changes to the Orchestrator).

**Request body** (`application/json`) — `ConfigUpdate`

| Field | Type | Required | Description |
|---|---|---|---|
| `model_backend` | `string` | ✗ | One of `openai`, `ollama`, `aws`, `azure`, `gcp`, `huggingface` |
| `model` | `string` | ✗ | Model name to use for the selected backend |

**Response**

```json
{
  "status": "success",
  "message": "Configuration updated",
  "config": {
    "model_backend": "openai",
    "model": "gpt-5.2"
  }
}
```

**500** — invalid configuration or failed to reinitialise the agent.

---

### POST `/chat`

Streaming chat endpoint for direct conversational interaction with the agent, bypassing the full research pipeline. Useful for quick questions or testing model connectivity.

**Request body** (`application/json`)

| Field | Type | Required | Description |
|---|---|---|---|
| `content` | `string` | ✓ | The user's message |

**Response** — `text/plain` streamed in real-time as the model generates tokens.

```
The latest advancements in renewable energy include...
```

**503** — agent not initialised.

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

## Shared Message Contract

Both WebSocket endpoints share the same JSON message protocol.

### Inbound Messages

All messages sent **from the client to the server** are JSON objects with a `type` field.

#### `query`

Starts a new research session. Triggers plan generation.

```json
{
  "type": "query",
  "content": "What are the long-term cardiovascular effects of GLP-1 agonists?"
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `content` | string | ✓ | The research question |

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

#### `step_failed` *(v1 only)*

A plan step failed. Research continues with remaining steps.

```json
{
  "type": "step_failed",
  "message": "Step 2 failed",
  "error": "Connection timeout to web_scraper MCP server"
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

#### `synthesis` *(v1 only)*

The final structured research synthesis from `AdvancedResearchAgent`. Follows the `SynthesisResult` shape.

```json
{
  "type": "synthesis",
  "message": "Synthesis complete.",
  "data": {
    "summary": "GLP-1 agonists demonstrate consistent MACE reduction…",
    "key_insights": ["..."],
    "patterns": ["..."],
    "recommendations": ["..."],
    "creative_applications": ["..."],
    "knowledge_gaps": ["..."]
  }
}
```

---

#### `report` *(v2 only)*

The final Markdown research report from the `ReportComposer`. Sent as the last event of a v2 session.

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

#### `research_paused` / `research_resumed` / `research_stopped` *(v1 only)*

Acknowledgements for in-flight control actions.

```json
{ "type": "research_paused",  "message": "Research paused. Send 'resume' to continue." }
{ "type": "research_resumed", "message": "Research resumed."  }
{ "type": "research_stopped", "message": "Research stopped."  }
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

All variables are read via the `Config` Pydantic model at startup. The most operationally relevant ones for the API server are:

| Variable | Default | Description |
|---|---|---|
| `MODEL_BACKEND` | `ollama` | Model provider: `openai`, `ollama`, `aws`, `azure`, `gcp`, `huggingface` |
| `AGENT_MODE` | `research` | Agent class for REST endpoints: `research`, `chat`, `self-optimization` |
| `LOG_LEVEL` | `20` (INFO) | Python logging level integer |
| `MEMORY_SERVER_URL` | `http://localhost:9494/mcp` | MCP memory server URL |
| `SEARCH_SERVER_URL` | `http://localhost:9393/mcp` | MCP web search server URL |
| `SCRAPER_SERVER_URL` | `http://localhost:9292/mcp` | MCP web scraper server URL |
| `FILE_SERVER_URL` | `http://localhost:9191/mcp` | MCP file handler server URL |
| `OPENAI_API_KEY` | — | Required when `MODEL_BACKEND=openai` |
| `OPENAI_MODEL` | `gpt-5.2` | OpenAI model name |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama server base URL |
| `OLLAMA_MODEL` | `nemotron-3-nano` | Ollama model name |
| `AWS_MODEL` | `global.anthropic.claude-sonnet-4-6` | Bedrock model ID |
| `AWS_BASE_URL` | — | Required when `MODEL_BACKEND=aws` |
| `AWS_API_KEY` | — | Required when `MODEL_BACKEND=aws` |
| `AZURE_OPENAI_ENDPOINT` | — | Required when `MODEL_BACKEND=azure` |
| `AZURE_OPENAI_API_KEY` | — | Required when `MODEL_BACKEND=azure` |
| `AZURE_OPENAI_DEPLOYMENT` | — | Azure deployment name |
| `GCP_ENDPOINT` | `https://us-central1-aiplatform…` | GCP Vertex AI endpoint |
| `GCP_MODEL` | `gemini-2.5-pro` | Vertex AI model name |
| `GCP_API_KEY` | — | Required when `MODEL_BACKEND=gcp` |
| `MAX_ITERATIONS` | `3` | Max tool-calling iterations per research step |
| `MAX_QA_RETRIES` | `2` | Max LoopAgent contradiction-retry cycles per step |
| `MAX_TOKENS` | `4096` | Max output tokens per generation call |
| `DEFAULT_TEMPERATURE` | `0.7` | Default generation temperature |
| `MAX_SOURCES_PER_QUERY` | `5` | Max sources retrieved per search step |
| `MAX_DEPTH` | `2` | Max recursive scraping depth |
| `ENABLE_PARALLEL_EXECUTION` | `True` | Run independent plan steps concurrently |
| `MCP_KEEPALIVE_INTERVAL` | `60` | Seconds between MCP server keepalive pings |
