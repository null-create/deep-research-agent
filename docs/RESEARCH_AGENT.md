# Research Agent — Architecture & Reference

> **Core file:** `backend/research_agent.py`  
> **Extended agent:** `backend/advanced_features.py`  
> **Self-optimising agent:** `backend/optimization.py`

---

## Table of Contents

1. [Overview](#overview)
2. [Class Hierarchy](#class-hierarchy)
3. [ResearchAgent (Base)](#researchagent-base)
   - [State](#state)
   - [Public Methods](#public-methods)
   - [Three-Phase Research Loop](#three-phase-research-loop)
   - [Phase 1 — generate\_plan](#phase-1--generate_plan)
   - [Phase 2 — execute\_approved\_plan](#phase-2--execute_approved_plan)
   - [Phase 3 — synthesize\_results](#phase-3--synthesize_results)
   - [Plan Management](#plan-management)
   - [Chat Mode](#chat-mode)
4. [Internal Methods](#internal-methods)
   - [\_check\_memory](#_check_memory)
   - [\_generate\_plan](#_generate_plan)
   - [\_execute\_step](#_execute_step)
   - [\_synthesize\_findings](#_synthesize_findings)
   - [\_save\_to\_memory](#_save_to_memory)
   - [\_generate\_final\_document](#_generate_final_document)
   - [\_select\_model\_for\_step](#_select_model_for_step)
5. [AdvancedResearchAgent](#advancedresearchagent)
   - [Parallel Step Execution](#parallel-step-execution)
   - [Source Credibility Scoring](#source-credibility-scoring)
   - [Deep Scraping with Link Following](#deep-scraping-with-link-following)
   - [Cross-Source Fact Checking](#cross-source-fact-checking)
   - [Follow-up Question Generation](#follow-up-question-generation)
6. [SelfOptimizingAgent](#selfoptimizingagent)
   - [self\_optimize](#self_optimize)
7. [Data Models](#data-models)
   - [ResearchPlan](#researchplan)
   - [ResearchStep](#researchstep)
   - [SynthesisResult](#synthesisresult)
   - [ResponseMessage](#responsemessage)
8. [Model Selection](#model-selection)
9. [Memory Integration](#memory-integration)
10. [MCP Tool Integration](#mcp-tool-integration)
11. [Known Limitations & TODOs](#known-limitations--todos)

---

## Overview

`ResearchAgent` is the v1 research engine. It orchestrates a **three-phase pipeline** for answering a research query:

1. **Plan** — consult long-term memory, then ask the model to produce a structured, step-by-step `ResearchPlan`.
2. **Execute** — run each step in the plan using iterative model + MCP tool-calling loops.
3. **Synthesise** — distil all step results into a `SynthesisResult`, save it to memory, generate a final Markdown document, and persist it via the `file_handler` MCP server.

Each phase is an **async generator** that yields `ResponseMessage` events so the WebSocket handler in `api_server.py` can stream live progress to the frontend.

`AdvancedResearchAgent` subclasses `ResearchAgent` and adds parallel step execution, source credibility scoring, recursive link-following scraping, cross-source fact-checking, and follow-up question generation.

`SelfOptimizingAgent` also subclasses `ResearchAgent` and adds a `self_optimize()` mode that reads all stored memories, analyses usage patterns, and develops new research strategies that are written back to memory for future use.

---

## Class Hierarchy

```
ResearchAgent                      (research_agent.py)
├── AdvancedResearchAgent          (advanced_features.py)
└── SelfOptimizingAgent            (optimization.py)
```

The API server instantiates `AdvancedResearchAgent` by default, or `SelfOptimizingAgent` when `AGENT_MODE=self-optimization`.

---

## ResearchAgent (Base)

**File:** `backend/research_agent.py`

### State

All mutable state lives on the instance and is reset by `_clear_state()` after a research session completes or a plan is denied.

| Attribute | Type | Description |
|---|---|---|
| `model` | `ModelBackend` | The configured model backend (OpenAI, Bedrock, Ollama, etc.) |
| `mcp_servers` | `MCPServerRegistry` | Registry of all connected MCP tool servers |
| `conversation_history` | `List[Message]` | Accumulated conversation turns (currently unused in execution; reserved for future context management) |
| `query` | `str` | The active research query; set when execution begins |
| `step_results` | `List[str]` | Accumulated text results from each completed step; passed to synthesis |
| `memory_context` | `str` | Long-term memory context retrieved at plan time; reused in steps to avoid re-querying |
| `_pending_plan` | `Optional[ResearchPlan]` | The plan awaiting user approval |
| `_pending_query` | `Optional[str]` | The query associated with the pending plan |
| `_pending_memory_context` | `Optional[str]` | The memory context captured at plan time, held until execution begins |

---

### Public Methods

| Method | Returns | Description |
|---|---|---|
| `generate_plan(query)` | `AsyncIterator[ResponseMessage]` | Phase 1: checks memory and generates a plan |
| `execute_approved_plan()` | `AsyncIterator[ResponseMessage]` | Phase 2: executes the pending plan step by step |
| `synthesize_results()` | `AsyncIterator[ResponseMessage]` | Phase 3: synthesises findings, saves to memory, generates final report |
| `modify_plan(plan_id, feedback)` | `ResponseMessage` | Regenerates the pending plan incorporating user feedback |
| `deny_plan(plan_id)` | `ResponseMessage` | Rejects the pending plan and resets state |
| `get_plan_steps(plan_id)` | `List[ResearchStep]` | Returns the steps of the pending plan by ID |
| `chat(message)` | `AsyncIterator[str]` | Simple streaming chat; no tools, no research context |

---

### Three-Phase Research Loop

```
User submits query
       │
       ▼
┌──────────────────────────────────────────────────────┐
│  generate_plan(query)                                │
│                                                      │
│  1. Check long-term memory (MCP memory server)       │
│  2. Build planning prompt with memory context        │
│  3. Call model → parse JSON → build ResearchPlan     │
│  4. Store _pending_plan, _pending_query              │
│                                                      │
│  Yields: status × 3, plan                            │
└───────────────────────┬──────────────────────────────┘
                        │  User approves / modifies / denies
                        ▼
┌──────────────────────────────────────────────────────┐
│  execute_approved_plan()                             │
│                                                      │
│  For each step in the plan:                          │
│    ├─ step_start event                               │
│    ├─ _execute_step() — iterative tool-calling loop  │
│    │    ├─ model.generate(messages, tools)           │
│    │    ├─ if tool_calls → execute each via MCP      │
│    │    └─ repeat until no tool calls or max iters   │
│    └─ step_complete | step_failed event              │
│                                                      │
│  If ALL steps fail → error event, clear state, stop  │
│  Otherwise → research_complete event                 │
│                                                      │
│  Yields: step_start, step_complete|step_failed,      │
│          research_complete | error                   │
└───────────────────────┬──────────────────────────────┘
                        │  (automatic, no user action)
                        ▼
┌──────────────────────────────────────────────────────┐
│  synthesize_results()                                │
│                                                      │
│  1. _synthesize_findings() → SynthesisResult JSON    │
│  2. _save_to_memory() — session + per-insight keys   │
│  3. _generate_final_document() → Markdown text       │
│  4. Write document via file_handler MCP server       │
│  5. _clear_state()                                   │
│                                                      │
│  Yields: status × 4, synthesis, research_complete,   │
│          status (save), status (report URL) | error  │
└──────────────────────────────────────────────────────┘
```

---

### Phase 1 — `generate_plan`

```python
async def generate_plan(self, query: str) -> AsyncIterator[ResponseMessage]
```

**Events emitted (in order):**

| `type` | `message` |
|---|---|
| `status` | "Received research query. Starting research process..." |
| `status` | "Checking long-term memory for relevant information..." |
| `status` | "Generating research plan..." |
| `plan` | "Research plan generated. Awaiting user approval." *(+ `plan` field)* |

**What happens internally:**

1. Calls `_check_memory(query)` against the `memory` MCP server. The retrieved context is stored in `self.memory_context` so it does not need to be re-fetched during execution.
2. Builds a structured JSON planning prompt and calls the model via `_generate_plan(query, memory_context)`.
3. Parses the model's JSON response into a `ResearchPlan` with `ResearchStep` objects at `PENDING` status.
4. Stores the plan in `_pending_plan` and the query in `_pending_query`. Execution is deferred until the user approves.

---

### Phase 2 — `execute_approved_plan`

```python
async def execute_approved_plan(self) -> AsyncIterator[ResponseMessage]
```

**Guards:** Returns an error event immediately if `_pending_plan` is `None`.

**Events emitted per step:**

| `type` | `message` | `data` |
|---|---|---|
| `step_start` | "Starting step N: \<description\>" | `{"step": {...}}` |
| `step_complete` | "Completed step N" | `{"step": {...}, "result": "..."}` |
| `step_failed` | "Step N failed" | — (`error` field populated) |

**After all steps:**

| `type` | Condition |
|---|---|
| `error` | All steps failed — model generates a failure summary, state is cleared |
| `research_complete` | At least one step succeeded — proceeds to synthesis |

**Step execution (`_execute_step`)** uses an iterative tool-calling loop (up to `MAX_ITERATIONS`, default 10):

```
model.generate(messages, tools)
         │
         ├── no tool_calls → return response.content
         │
         └── tool_calls present
                  │
                  ├── for each tool_call:
                  │     mcp_servers.call_tool(name, parameters)
                  │     append result to messages
                  │
                  └── iterate again (up to max_iterations)
```

Context passed to each step includes the original query, the memory context captured at plan time, and all previous step results concatenated as plain text. See [Known Limitations](#known-limitations--todos) for the scaling concern this introduces.

---

### Phase 3 — `synthesize_results`

```python
async def synthesize_results(self) -> AsyncIterator[ResponseMessage]
```

**Guards:** Returns an error event if `self.query` or `self.step_results` are empty.

**Events emitted (in order):**

| `type` | `message` |
|---|---|
| `status` | "Synthesizing findings..." |
| `synthesis` | "Synthesis complete." *(+ `data` field containing full `SynthesisResult`)* |
| `status` | "Saving results to long-term memory..." |
| `research_complete` | "Research complete!" |
| `status` | "Generating final research report..." |
| `status` | "Report was generated successfully. Saving as artifact..." |
| `status` | "Final document saved successfully!" *(+ `data.url`)* |
| `error` | If file save fails |

The final document is written to the `file_handler` MCP server with a UUID filename (`research_report_<uuid>.txt`), allowing it to be retrieved later by URL.

---

### Plan Management

#### `modify_plan(plan_id, feedback)`

Regenerates `_pending_plan` by re-calling `_generate_plan` with the original query, the stored memory context, **and** the user's feedback appended to the prompt. Then asks the model to produce a human-readable summary of what changed between the original and updated plan.

Returns a `ResponseMessage(type="plan")` on success or `type="error"` if there is no matching pending plan or generation fails.

#### `deny_plan(plan_id)`

Clears all state via `_clear_state()`, then asks the model to produce a graceful acknowledgement and next-step suggestions for the user.

Returns a `ResponseMessage(type="plan_denied")`.

---

### Chat Mode

```python
async def chat(self, message: str) -> AsyncIterator[str]
```

A lightweight streaming chat interface that bypasses the entire research pipeline. Uses a simple system prompt ("You are a helpful research assistant") and calls `model.stream_generate()` directly. No MCP tools, no memory, no step context. Used by the `POST /chat` REST endpoint for quick conversational queries.

---

## Internal Methods

### `_check_memory`

```python
async def _check_memory(self, query: str) -> str
```

Calls the `memory` MCP server's `recall_memories` tool with `{"query": query, "limit": 1000}`. If results are returned, joins them as bullet points and caches the string in `self.memory_context`. Returns a plain-text context string, or a fallback message if the server is unavailable.

---

### `_generate_plan`

```python
async def _generate_plan(self, query: str, memory_context: str, user_feedback: str = None) -> ResearchPlan
```

Builds a system prompt instructing the model to return a specific JSON shape:

```json
{
  "goal": "Clear statement of the research goal",
  "steps": [
    {"id": 1, "description": "First step description"},
    {"id": 2, "description": "Second step description"}
  ]
}
```

Handles models that wrap their JSON in fenced code blocks (` ```json ` or ` ``` `). Raises an exception if the JSON cannot be parsed — no silent fallback, so the caller's error handler will surface this to the frontend.

When `user_feedback` is provided (i.e. this is a `modify_plan` call), the feedback is appended to the user prompt to steer the regenerated plan.

---

### `_execute_step`

```python
async def _execute_step(self, step: ResearchStep, query: str) -> str
```

The core execution primitive. Builds a prompt from:

- The original query
- The cached memory context
- All previous step results (raw text concatenation — see [Known Limitations](#known-limitations--todos))
- The current step description

Enters the iterative tool-calling loop described in [Phase 2](#phase-2--execute_approved_plan). Returns the model's final text response (the last generation that produced no tool calls).

---

### `_synthesize_findings`

```python
async def _synthesize_findings(self, query: str, step_results: List[str]) -> SynthesisResult
```

Instructs the model to return a JSON object matching the `SynthesisResult` shape:

```json
{
  "summary": "...",
  "key_insights": ["..."],
  "patterns": ["..."],
  "recommendations": ["..."],
  "creative_applications": ["..."],
  "knowledge_gaps": ["..."]
}
```

On parse failure, returns a fallback `SynthesisResult` with the raw model output in `summary` and empty lists for all other fields.

---

### `_save_to_memory`

```python
async def _save_to_memory(self, query, plan, step_results, synthesis) -> None
```

Writes two kinds of records to the `memory` MCP server:

1. **Session record** — keyed `research_<query>`, containing the full goal, all step findings, and the synthesis as a JSON blob.
2. **Per-insight records** — one entry per item in `synthesis.key_insights`, keyed `insight_<query>_<i>`, enabling fine-grained retrieval in future research sessions on related topics.

---

### `_generate_final_document`

```python
async def _generate_final_document(self, query: str, synthesis: Dict[str, Any]) -> str
```

Takes the `SynthesisResult` dict and asks the model to render a well-structured, human-readable research report (in practice, Markdown). The output is the string that gets written to the `file_handler` MCP server.

---

### `_select_model_for_step`

```python
def _select_model_for_step(self, step: ResearchStep | str) -> str
```

A keyword-matching heuristic that maps each step to the best available model for the configured backend. Three tiers:

| Tier | Keywords | Rationale |
|---|---|---|
| **High-reasoning** | `plan`, `synthesize`, `insight`, `pattern`, `analyze`, `summarize`, `research`, `develop`, `assess`, `recommendation`, `creative application`, `document` | Deep comprehension required |
| **Fast / light** | `search`, `find`, `gather`, `execute`, `retrieve`, `scrape`, `follow links` | High-volume, low-complexity retrieval |
| **Fast / light** | `status`, `message`, `failure`, `error`, `inform the user` | Simple prose generation |
| **High-reasoning** *(default)* | *(no keyword match)* | Safe fallback |

Backend-specific model names:

| Backend | High-reasoning | Fast |
|---|---|---|
| OpenAI | `gpt-5.2` | `gpt-5-nano` |
| Azure OpenAI | `gpt-5.2` | `gpt-5-nano` |
| AWS Bedrock | `global.anthropic.claude-sonnet-4-6` | `global.anthropic.claude-haiku-4-5-20251001-v1:0` |
| GCP Vertex AI | `gemini-2.5-pro` | `gemini-2.5-nano` |
| Ollama | `nemotron-3-nano` | `llama3.2` |

---

## AdvancedResearchAgent

**File:** `backend/advanced_features.py`  
**Inherits:** `ResearchAgent`

`AdvancedResearchAgent` is the default agent class instantiated by the API server. It extends the base class with five capabilities implemented as additional internal methods. These methods are available for use in subclasses or future extensions to the execution pipeline; they are not automatically wired into the base `_execute_step` loop.

```python
class AdvancedResearchAgent(ResearchAgent):
    def __init__(self, model_backend, mcp_registry):
        super().__init__(model_backend, mcp_registry)
        self.sources: List[Source]   # accumulates Source dataclass instances
```

The `Source` dataclass holds `url`, `title`, `content`, `credibility_score` (float 0–1), and a `timestamp`.

---

### Parallel Step Execution

```python
async def _execute_step_parallel(
    self, steps: List[ResearchStep], query: str, memory_context: str
) -> List[str]
```

Runs multiple `_execute_step` calls concurrently using `asyncio.gather`. Exceptions from individual steps are caught and recorded as `StepStatus.FAILED` rather than propagating and cancelling the whole batch.

---

### Source Credibility Scoring

```python
async def _assess_source_credibility(self, url: str, content: str) -> float
```

Sends a source URL and content preview (first 1 000 characters) to the model and asks it to score credibility on a 0.0–1.0 scale across six dimensions:

1. Domain authority
2. Content quality and depth
3. Presence of citations
4. Author credentials
5. Recency of information
6. Bias indicators

Returns the float score, or `0.0` on any parse failure (scores are only added when assessment succeeds).

---

### Deep Scraping with Link Following

```python
async def _deep_scrape(
    self, url: str, max_depth: int = 2, current_depth: int = 0
) -> List[Dict[str, Any]]
```

Recursively scrapes a URL via the `fetch` MCP server and follows relevant child links up to `max_depth` levels. At each level:

1. Calls the `scrape` MCP tool with `extract_links=True`.
2. Passes the extracted links to `_filter_relevant_links()` to select the top 3 most relevant.
3. Recurses on the filtered links using `asyncio.gather` for parallelism.

Returns a flat list of `{"url", "content", "depth"}` dicts spanning the entire scraped sub-tree.

---

### Cross-Source Fact Checking

```python
async def _cross_reference_sources(self, sources: List[Source]) -> Dict[str, Any]
```

Given a list of `Source` objects, asks the model to identify:

- Facts corroborated across multiple sources
- Conflicting information between sources
- Unique insights from each source
- Overall consensus view

Returns a structured dict. Falls back to empty lists on parse failure rather than raising.

---

### Follow-up Question Generation

```python
async def _generate_follow_up_questions(self, synthesis: Dict[str, Any]) -> List[str]
```

After synthesis, generates up to five follow-up research questions designed to deepen understanding. Focuses on unexplored implications, potential applications, contradictions that need resolution, and related areas. Returns a list of question strings, or an empty list on failure.

---

## SelfOptimizingAgent

**File:** `backend/optimization.py`  
**Inherits:** `ResearchAgent`

Activated when `AGENT_MODE=self-optimization`. Adds a `self_optimize()` async generator method. The regular research pipeline (`generate_plan`, `execute_approved_plan`, `synthesize_results`) is inherited unchanged.

### `self_optimize`

```python
async def self_optimize(self) -> AsyncIterator[ResponseMessage]
```

A four-step introspection cycle:

```
1. get_all_memories()
        │  Calls memory MCP server's `retrieve_all_memories` tool
        ▼
2. _analyze_memories(all_memories)
        │  Model identifies patterns, themes, and trends across all
        │  stored research sessions and insights
        ▼
3. _develop_new_research_methods(insights)
        │  Model proposes actionable new research strategies as a
        │  JSON array of description strings
        ▼
4. store_memory(type="optimization_insight", content={insights, new_methods})
        │  Persists strategies for use in future research sessions
        ▼
   Yields final status events with new_methods list
```

**Events emitted:**

| `type` | `message` |
|---|---|
| `status` | "Starting self-optimization process..." |
| `status` | "Retrieved N memories for analysis..." |
| `status` | "Insights from memory analysis:" *(+ `data.insights`)* |
| `status` | "Developing new research methods based on insights..." |
| `status` | "Developed N new research methods." |
| `status` | "New research methods:" *(+ `data.new_methods`)* |
| `error` | If memory server unavailable when storing results |

---

## Data Models

All models are defined in `backend/models.py`.

### ResearchPlan

A plain Python class (not Pydantic) with a `to_dict()` method for JSON serialisation.

| Field | Type | Description |
|---|---|---|
| `id` | `str` | UUID generated at construction time |
| `goal` | `str` | One-sentence statement of the research objective |
| `steps` | `List[ResearchStep]` | Ordered list of steps to execute |

---

### ResearchStep

Plain Python class with `to_dict()`.

| Field | Type | Description |
|---|---|---|
| `id` | `int` | Step number (1-based, from the model's plan) |
| `name` | `str` | "Step N" label |
| `description` | `str` | Full step description from the model |
| `status` | `StepStatus` | `pending` → `in_progress` → `completed` \| `failed` |
| `result` | `str` | Text result from `_execute_step` (empty until completed) |
| `error` | `str` | Error message (empty unless failed) |

---

### SynthesisResult

Pydantic model.

| Field | Type | Description |
|---|---|---|
| `summary` | `str` | Concise summary of all findings |
| `key_insights` | `List[str]` | Novel insights derived from the research |
| `patterns` | `List[str]` | Patterns and trends identified across sources |
| `recommendations` | `List[str]` | Actionable recommendations |
| `creative_applications` | `List[str]` | Innovative ways to apply the knowledge |
| `knowledge_gaps` | `List[str]` | Areas requiring further research |

---

### ResponseMessage

Pydantic model. The universal envelope for all WebSocket outbound events.

| Field | Type | Description |
|---|---|---|
| `type` | `str` | Event type (e.g. `"status"`, `"plan"`, `"step_complete"`) |
| `message` | `str` | Human-readable message |
| `data` | `Optional[Dict]` | Type-specific payload (step dict, synthesis dict, URL, etc.) |
| `error` | `Optional[str]` | Error detail string (present on `type="error"`) |
| `plan` | `Optional[Dict]` | Serialised `ResearchPlan` (present on `type="plan"`) |

---

## Model Selection

The `_select_model_for_step` heuristic is the single decision point for which model name is passed to `model.generate()` on any given call. It inspects the step description (or a plain string task label) for keywords and maps them to the appropriate model tier for the active backend.

This means a single `ResearchAgent` instance can transparently use different model sizes across the same research session — a powerful reasoning model for planning and synthesis, and a faster, cheaper model for web search and retrieval steps — without any configuration changes.

The full keyword-to-model mapping is documented in the [\_select\_model\_for\_step](#_select_model_for_step) section above.

---

## Memory Integration

The agent interacts with the `memory` MCP server at three points in its lifecycle:

| Point | Operation | Purpose |
|---|---|---|
| `generate_plan` | `recall_memories(query, limit=1000)` | Prime the planning prompt with prior knowledge relevant to the query |
| `_execute_step` | *(context only — no direct call)* | Memory context is included in every step prompt via `self.memory_context` |
| `synthesize_results` | `store_memory(key, content, metadata)` | Persist session + per-insight records for retrieval in future sessions |

The memory context string is captured once at plan time and reused across all execution steps without additional queries. This keeps latency low but means newly retrieved web data is not fed back into memory mid-session.

---

## MCP Tool Integration

The agent accesses four MCP servers, all registered in `MCPServerRegistry` at startup:

| Server key | Default URL (Docker) | Primary tools used |
|---|---|---|
| `memory` | `http://mcp-memory-server:9494/mcp` | `recall_memories`, `store_memory`, `retrieve_all_memories` |
| `web_search` | `http://mcp-web-search-server:9393/mcp` | `search` (via `get_all_tools` tool-calling) |
| `fetch` | `http://mcp-fetch-server:9292/mcp` | `fetch_url` (via `get_all_tools` tool-calling) |
| `file_handler` | `http://mcp-file-handler-server:9191/mcp` | `write_file`, `list_files` |

During `_execute_step`, the agent calls `mcp_servers.get_all_tools()` to retrieve the full tool spec list from every connected server and passes it to the model. The model may then invoke any of those tools freely; `MCPServerRegistry.call_tool` routes each call to the correct server automatically by matching the tool name against each server's registered spec.

---

## Known Limitations & TODOs

| # | Location | Issue | Suggested fix |
|---|---|---|---|
| 1 | `_execute_step` | All previous step results are concatenated as raw text into the prompt on every step call. As the number of steps grows, this rapidly consumes the context window. | Replace with vector-DB retrieval: store step results as embeddings and retrieve only the top-K most relevant to the current step. |
| 2 | `AdvancedResearchAgent` state | A single `agent` instance is shared across all REST `/chat` calls (and used by `/mcp/*` endpoints). The WebSocket `/ws/research` endpoint is **not affected** — it creates a fresh `Orchestrator` per session. The `AdvancedResearchAgent` singleton is not used by WebSocket connections. | No action needed for the primary research flow; applies only if the `/chat` REST endpoint is used concurrently under high load. |
| 3 | `_generate_plan` | If the model returns malformed JSON, an exception is raised and surfaced as an error event with no retry. | Add a retry loop (1–2 attempts) with a rephrased prompt before failing. |
| 4 | `synthesize_results` | `_save_to_memory` is called with `self._pending_plan`, but `_pending_plan` is `None` at synthesis time (it was cleared at the start of execution). | Pass the plan explicitly as a local variable rather than reading from `self._pending_plan`. |
| 5 | `AdvancedResearchAgent` | `_execute_step_parallel`, `_assess_source_credibility`, `_deep_scrape`, `_cross_reference_sources`, and `_generate_follow_up_questions` are defined but not wired into the main execution pipeline. | Integrate them into a new `execute_approved_plan` override in `AdvancedResearchAgent`. |
| 6 | `_check_files` | The method is defined but exits early without returning a value when `files_info` is missing the `"files"` key. | Add an explicit `return "No relevant files found."` at the end of the function and wire the method into `generate_plan`. |
