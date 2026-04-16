# PROJECT-KNOWLEDGE.md — Research Assistant

> Living architecture map. Load fully at session start. Update surgically when things change.
> Updated: 2026-04-10 — Pipeline data-flow audit: 9 fixes to ensure comprehensive data passage between agents. Config defaults: `max_iterations` 3→5, `distill_max_chars` 2000→4000, `step_summary_max_chars` 800→1500, `section_draft_top_k` 6→10, `analyst_top_k` 8→10. `_extract_sources()` converted from `@staticmethod` to instance method; now collects source URLs from RAG store chunks as fallback (8-session bug fixed). `_run_analyst()` now passes prior step structured claims/tensions to each analyst via `prior_claims_block` (cross-step `top_k` 3→5). `synthesize()` Phase B now passes `structured_claims_block`, `structured_tensions_block`, and `unresolved_contradictions_block` to section drafting; system prompt updated for novel insight generation. `_generate_step_summary` uses expanded input (claims[:15], tensions[:5], notes[:500]) and 8-bullet prompt. Distillation raw text input 8000→12000. Smoke tests: 62 total.
> Updated: 2026-04-10 — Knowledge Graph Phase 1–5 (graph pruning + full enhancement): `long_term_memory.py` gained `Source` node type (uniqueness constraint on `url`), temporal props on Entity/RELATES_TO, relationship deduplication in `store_relationship`, 13 new KG methods (store_hierarchy, store_contradiction, find_contradictions, store_source, link_to_source, get_provenance, recent_entities, recent_relationships, session_diff, find_paths, find_common_neighbors, decay_confidence, prune), enhanced `recall_graph_context` (min_confidence, include_contradictions, include_provenance), expanded `stats()` (6 keys). `orchestrator.py`: mutation counter, dynamic entity_limit, enriched extraction prompt with IS_A+provenance wiring, smart community gate, post-synthesis decay. `config.py`: `confidence_decay_half_life` (default 30) + `graph_community_min_mutations` (default 3). `api_server.py`: 9 new `/graph/*` endpoints (POST /graph/prune + 8 GETs). Smoke tests: 57 total (was 48), all pass.
> Updated: 2026-03-25 — Synthesis step leak fix: `_root_system_prompt()` updated with `CRITICAL CONSTRAINT` prohibiting synthesis steps in the plan. `_is_synthesis_step(step)` static method added (19-phrase detection). `_run_step()` gains a synthesis guard that skips steps matching the detector, emitting `step_complete` with `skipped_synthesis: True`. `App.tsx` `step_complete` handler shows a skip message rather than "Completed step" for skipped synthesis steps. Smoke tests: 47 total.
> Updated: 2026-04-01 — ChromaDB → Neo4j migration: `long_term_memory.py` fully rewritten to use Neo4j async driver (`AsyncGraphDatabase`). Entities, relationships, and communities are now native Neo4j graph primitives (`:Entity`, `:RELATES_TO`, `:Community`, `:MEMBER_OF`). Flat memories stored as `:Memory` nodes with vector indexes (`memory_embedding_idx`, `entity_embedding_idx`, `community_embedding_idx`). `_NoOpEmbeddingFunction` deleted. `config.py` replaces `chroma_persist_dir`/`chroma_collection_name` with `neo4j_uri`/`neo4j_user`/`neo4j_password`/`neo4j_database`/`neo4j_embedding_dimensions`. `requirements.txt`: `chromadb` → `neo4j>=5.26.0`. Docker Compose adds `neo4j:5.26-community` service (ports 7474/7687, `neo4j_data` volume). `api_server.py` and `cli.py` updated with new constructor args + `close()` on shutdown. `smoke_test.py` updated. Migration script: `scripts/migrate_chroma_to_neo4j.py`.
> Last verified: 2026-03-17 — E2E CONFIRMED WORKING (test_e2e.py + deployed Docker stack)
> Updated: 2026-03-18 — Azure Container Apps Terraform updated: `mcp_memory` Container App removed; ChromaDB volume now mounted on backend; `MEMORY_SERVER_URL` env var removed from backend; `CHROMA_PERSIST_DIR` + `CHROMA_COLLECTION_NAME` added.
> Updated: 2026-03-18 — Frontend dead code removed: `ReasearchProgress.tsx`, `StepResultModel.tsx`, `StatusIndicator.tsx`, `LoadingSpinner.tsx`, `ReportViewer.tsx`, `hooks/useGraphState.ts`.
> Updated: 2026-03-19 — Self-optimize pipeline fixed: `SelfOptimizingAgent` now accepts `AsyncLongTermMemory` and calls `.recall()` directly; `backend/instructions/` bind-mounted in both docker-compose files.
> Updated: 2026-03-19 — File upload architecture: frontend uploads directly to file handler MCP (port 9191) via custom HTTP routes; nginx + vite proxy `/files/*` to it. Orchestrator.plan() now reads uploaded files via MCP and injects context into planning prompt.
> Updated: 2026-03-20 — GraphRAG: `KnowledgeGraph` class added to `long_term_memory.py` with 3 new ChromaDB collections (entities, relationships, communities). Orchestrator extracts triples from analyst claims, recalls graph context during planning, updates communities post-synthesis.
> Updated: 2026-03-20 — Self-optimization graph integration: `SelfOptimizingAgent.get_all_memories()` queries knowledge graph alongside flat recall; `_analyze_memories()` prompt graph-aware; Phase 5 persistence uses `_long_term_memory.store()` (dead MCP code removed).
> Updated: 2026-03-20 — Research depth setting: `Orchestrator` now accepts `research_depth` ("shallow"/"moderate"/"deep"). Shallow skips QA loop; deep adds search/analyst depth-hint prompts and allows 3 QA retries. Frontend Settings tab has a 3-button depth toggle; Source Verification dropdown removed. `research_depth` sent in WS `query` message.
> Updated: 2026-03-20 — QA loop rework: `Contradiction` has `contradiction_type` field; LoopAgent classifies each contradiction (`factual_error`/`temporal_mismatch`/`source_disagreement`/`insufficient_evidence`); only actionable types trigger re-search; `source_disagreement` injected as analyst tensions; convergence check breaks loop when actionable count stops decreasing. Default `research_depth` changed to `"shallow"` in orchestrator, api_server, and frontend.
> Updated: 2026-03-21 — Frontend analyst_notes fix: `GraphView.tsx` and `StepResultViewer.tsx` now handle dict-type `analyst_notes` (typeof check + JSON.stringify fallback). StepResultViewer type updated to `string | Record<string, unknown>`. Fixes `t.analyst_notes.slice is not a function` crash that caused WebSocket disconnects.
> Updated: 2026-03-21 — WS resume reliability: `useWebSocket.ts` adds 150ms delay before `onReconnected` callback fires on reconnect, preventing race condition where `resume` message was sent before backend WS handler was ready. Backend `orchestrator.py` synthesis Phase B now emits `synthesis_progress` events (structured: `{section_index, section_title, total_sections}`) instead of generic `status` messages. `App.tsx` handles `synthesis_progress` to update status bar and re-enable `isResearching` on section 1 so the spinner stays visible during the post-`research_complete` synthesis window.
> Updated: 2026-03-21 — GCP Vertex AI fixes + config model selection: `GCPVertexAIBackend.generate()` now converts raw SDK tool call objects to internal `ToolCall` dataclass (was returning raw `ChatCompletionMessageFunctionToolCall`, causing `AttributeError` on `.name`). `_run_search` error handler uses `getattr(tool_call, "name", "<unknown>")` to prevent double-fault. `_select_model()` now reads from `config.*_heavy_model` / `config.*_light_model` fields instead of hardcoded strings. `config.py` has per-backend `heavy_model`/`light_model` fields (overridable via `{BACKEND}_HEAVY_MODEL` / `{BACKEND}_LIGHT_MODEL` env vars, defaults match previous hardcoded values). SearchAgent system prompt updated with explicit tool-use instruction to prevent Gemini refusal on current-events queries. `openai._base_client` and `openai.resources` added to silenced loggers in `observability.py`.
> Updated: 2026-03-21 — Session persistence (P0 fix): Docker restart during synthesis no longer loses research. `session_store.py` now writes checkpoints to `logs/sessions/{session_id}.json` (atomic via `.tmp` rename, respects `LOG_DIR` env var). `orchestrator.py` has `synthesis_checkpoint()` / `restore_synthesis_state()`. `api_server.py` writes checkpoint after `execute()` completes and again in `finally`; `resume` handler falls back to `load_checkpoint()` if session is not in memory; `_recover_session_from_checkpoint()` restores orchestrator state and re-runs synthesis as a tracked background job. 37 smoke tests pass.
> Updated: 2026-03-23 — Docs viewer UI: `GET /project-docs` + `GET /project-docs/{filename}` REST endpoints added to `api_server.py` (NOT `/docs` — FastAPI reserves that for Swagger UI). `docs/` bind-mounted as `./docs:/app/docs` in all three compose files (`docker-compose.yml`, `docker-compose-full.yml`, `docker-compose-agent.yml`). New `src/components/DocsViewer.tsx` (full-screen modal, sidebar nav + markdown content). "Docs" button in `App.tsx` top nav. `App.tsx` return wrapped in fragment to allow overlay sibling. New `apiClient.listDocs()` / `apiClient.getDoc()` methods.
> Updated: 2026-03-23 — Model Settings UI: "Model Settings" sub-panel added to Sidebar settings tab (`ModelSettings.tsx`). `config.py` gains per-agent model overrides (root/search/analyst/qa_model_override) + per-agent sampling (temperature, top_p, max_tokens x4) + `huggingface_api_key`. `POST /config` fully rewritten: applies all fields, persists to `config/model_settings.json`, rebuilds model_backend+agent_pool in-memory. Startup loads persisted settings overlay. `orchestrator._select_model()` bug fixed: was using module-level `config` instead of `self.config`; now uses `cfg = self.config` and checks per-agent overrides first. Temperature + max_tokens wired through all root backend generate() calls and SubAgent.run(). ConfigUpdate expanded. Smoke test updated; all 42 pass.
> Updated: 2026-03-24 — Academic search backends: `search_arxiv` (official `arxiv` SDK, sort_by support) and `search_semantic_scholar` (direct HTTP to S2 REST API — the `semanticscholar` SDK was dropped because it hangs via `nest_asyncio` conflicts with `asyncio.to_thread`) added to `mcp/web_search/search_backends.py` and exposed as new MCP tools in `main.py`. `arxiv>=2.1.0` added to `mcp/web_search/requirements.txt`. Optional `S2_API_KEY` env var for higher S2 rate limits. `docs/MCP_SERVERS.md` updated. Web search server now exposes **8 tools** (was 6).
> Updated: 2026-03-24 — Auto PDF export: new `backend/report_exporter.py` (fpdf2-based). `orchestrator.synthesize()` calls `export_report_pdf(document, query, session_id)` via `asyncio.to_thread` immediately after assembling the document, before yielding the `report` event — covers both API/frontend and CLI modes. Output goes to `REPORTS_DIR` env var (default `<repo_root>/data/reports`). File naming: `{YYYY-MM-DD}_{session_id[:8]}_{title_slug}.pdf`. `fpdf2==2.8.7` added to `requirements.txt`. `REPORTS_DIR=/app/data/reports` env var + `./data/reports:/app/data/reports` volume bind-mount added to both `docker-compose.yml` and `docker-compose-full.yml`. All 46 smoke tests pass.

---

## Repository Layout

```
research-assistant/
├── backend/          Core Python/FastAPI application
├── src/              React/TypeScript frontend (Vite); src/README.md is the frontend-specific dev guide
├── mcp/              Standalone MCP protocol servers
│   └── auth/         JWT auth handler shared by MCP servers (JWTAuthHandler, TokenVerifier)
├── infra/
│   └── azure/        Terraform IaC for Azure Container Apps deployment (12 files — see §Infrastructure)
├── docs/             Architecture and API documentation (7 files: API_SERVER, CLI, MCP_SERVERS, ORCHESTRATOR, RAG_NOTES, RESEARCH_AGENT, BENCHMARKING)
├── scripts/          Operational scripts; `benchmark.py` — DeepResearch Bench runner; `migrate_chroma_to_neo4j.py` — one-time data migration
├── README.md         Thin landing page: quick start, Makefile commands, config example, docs table
├── config/           (empty / reserved)
├── data/             Logs and runtime data
├── chroma_data/      Legacy ChromaDB vector store data (pre-Neo4j migration)
├── file_handler_data/ File MCP server storage
├── docker-compose*.yml  Various compose configurations
├── Makefile          Top-level dev commands (run, run-all, run-agent, run-mcp, run-fe, stop*, restart*, init, clean)
└── .github/
    ├── agents/       Copilot agent instruction files
    └── workflows/    CI/CD workflows
```

---

## Backend Core Modules (`backend/`)

| File | Owns |
|------|------|
| `api_server.py` | FastAPI app, lifespan (agent/orchestrator init), REST endpoints, WebSocket handler (`/ws/research`), session drain/reconnect. `_recover_session_from_checkpoint(app_state, session_manager, session_store, checkpoint_data)` restores orchestrator synthesis state from disk and re-runs `synthesize()` as a tracked background job — called by the `resume` handler on in-memory miss. REST endpoints include `GET /project-docs` (list `.md` filenames from `docs/`) and `GET /project-docs/{filename}` (return content; path-traversal protected). NOTE: route is `/project-docs` not `/docs` — FastAPI reserves `/docs` for Swagger UI. **Shutdown guard:** `app.state.shutting_down` flag set to `True` at start of `_shutdown()`; WS handler checks it alongside `agent_pool` to reject new sessions during teardown. **Cancel-of-complete fix:** `_run_session` CancelledError handler checks `replay_log` for existing `report` events — if present, sets `state="complete"` instead of `"cancelled"`. |
| `orchestrator.py` | `Orchestrator` + `AgentPool` + five sub-agents (Root, Search, Analyst, Loop, Report); per-step RAG constants; multi-pass synthesis. `AgentPool.from_single_backend(backend)` + `.async_init(registry)` is the canonical construction path. `research_depth` param ("shallow"/"moderate"/"deep") controls QA loop: shallow skips it, moderate=2 retries, deep=3 retries + enhanced prompts. `synthesis_checkpoint()` returns a JSON-safe dict of synthesis inputs (`_analyst_output`, `_step_summaries`, `_step_sources`, `_contradictions`, query); `restore_synthesis_state(checkpoint)` reconstructs those fields for post-restart recovery. `release_memory()` frees heavy intermediate buffers after session completes (called by `_run_session` finally block to prevent OOM during long benchmark runs). `_is_synthesis_step(step)` static method returns `True` when a step description matches synthesis/report-generation phrases (19 signals); `_run_step()` uses it to skip such steps immediately with `skipped_synthesis: True` in the event data — final report assembly is exclusively handled by `synthesize()`. `_root_system_prompt()` has an explicit `CRITICAL CONSTRAINT` prohibiting synthesis steps from the plan. |
| `research_agent.py` | `ResearchAgent` base class: plan generation, step execution, basic synthesis |
| `advanced_features.py` | `AdvancedResearchAgent`: parallel step execution, credibility scoring |
| `optimization.py` | `SelfOptimizingAgent`: five-phase self-optimize workflow (read_methods → retrieve_memories → analyze → develop → update_methods). Accepts `Optional[AsyncLongTermMemory]`; `get_all_memories()` queries both flat `.recall(limit=100)` AND `graph.recall_graph_context()` (entity_limit=20, max_hops=2) — returns graph context under `"knowledge_graph"` key + stats under `"graph_stats"`. `_analyze_memories()` prompt instructs LLM to analyze entity clusters, relationship patterns, and thematic clusters. Phase 5 persists insights via `_long_term_memory.store(category="optimization_insight", importance=8)`. Reads/writes `backend/instructions/RESEARCH-METHODS.md`. Also contains unused utility classes (`AsyncCache`, `BatchProcessor`, `OptimizedResearchAgent`). |
| `pipeline.py` | `PipelineRunner`: `MAX_CONCURRENT_PIPELINES=5` semaphore, cross-pipeline query dedup |
| `session_manager.py` | `ResearchSessionManager`: owns all background tasks, graceful shutdown. Accepts `max_concurrent_sessions` (default 10 via `MAX_CONCURRENT_SESSIONS` env var); when >0, `start_job()` wraps coroutines with a `Semaphore`-gated wrapper so at most N pipelines execute concurrently. |
| `session_store.py` | Session storage — in-memory registry + disk persistence. `SessionStore.persist(session, synthesis_checkpoint=None)` writes `logs/sessions/{session_id}.json` atomically (`.tmp` rename; respects `LOG_DIR` env var). `SessionStore.load_checkpoint(session_id)` reads it back (returns `None` if absent or corrupted). TTL-based eviction via `prune_expired()` called on each new query. Checkpoint written after `execute()` completes and in `finally`. |
| `model_backend.py` | LLM backend abstraction (async): `AsyncOpenAI`/`AsyncAzureOpenAI` for OpenAI/Azure/Bedrock/GCP; `aiohttp` for Ollama/HuggingFace. All `.create()` calls are `await`ed — never blocks the event loop. **GCP note:** `GCPVertexAIBackend.generate()` converts raw SDK `ChatCompletionMessageFunctionToolCall` objects to internal `ToolCall(name=tc.function.name, ...)` — same as Bedrock. |
| `mcp_client.py` | `MCPClient` (multi-transport: `stdio` / `sse` / `streamable-http`), `MCPServerRegistry` (stores rich `server_configs` metadata per server; `builtin` flag; user servers persisted to `config/user_mcp_servers.json`; `get_all_server_info()` for API), tool discovery/invocation |
| `context.py` | `ResearchContext`: intermediate results, cross-step state accumulation |
| `search_result_store.py` | Per-session RAG store, in-process cosine similarity ranking |
| `models.py` | Shared data models: `ResearchPlan`, `ResearchStep`, `Message`, `ModelResponse`, `StepStatus` |
| `config.py` | Pydantic-based config loaded from environment variables |
| `embeddings.py` | Embedding utilities for the RAG store (in-process sentence-transformers) |
| `long_term_memory.py` | `AsyncLongTermMemory`: persistent cross-session Neo4j store + `KnowledgeGraph` (GraphRAG). `:Memory` nodes for raw evidence; 7 typed entity labels (`:Person`, `:Organization`, `:Technology`, `:Concept`, `:Event`, `:Location`, `:Metric`) with type-specific properties and per-type vector indexes; 18 typed relationship labels (CAUSES, ENABLES, USES, etc.); `:Claim` nodes for assertions; `:Document` nodes (subsumes old Source) for provenance; `:Community` nodes + `:MEMBER_OF` edges. `classify_relation()` maps verb phrases to typed labels. `store_source()`/`link_to_source()` preserved as deprecated wrappers. `close()` method for clean driver shutdown. |
| `report_exporter.py` | PDF export: `export_report_pdf(doc, query, session_id)` async entry point. `_generate_pdf()` via `asyncio.to_thread` using `fpdf2`. `REPORTS_DIR` env var (default `<repo_root>/data/reports`). Called by `orchestrator.synthesize()` before yielding `report` event. |
| `env.py` | Read/write `.env` file values programmatically (preserves formatting) |
| `error_handling.py` | Shared error handling utilities |
| `metrics.py` / `observability.py` / `monitoring.py` | Logging and observability helpers |
| `pipeline.py` | Semaphore-capped pipeline runner with query dedup |
| `cli.py` | Command-line interface for running research without the frontend |
| `test_e2e.py` | Quick E2E smoke test: mock MCP registry + real LLM backend. Runs full plan→execute→synthesize flow. No API server needed. Run from `backend/` dir as `python test_e2e.py`. |
| `smoke_test.py` | **Primary CI smoke test.** Standalone script (not a pytest module — excluded via `conftest.py`). Runs from `backend/` as `python smoke_test.py`. Covers RAG, context, models, orchestrator internals, LTM wiring, analyst pipeline, and session persistence via unit + AST inspection tests. **47 tests total** as of 2026-03-25. Exits `1` on any failure. Set `EMBEDDINGS_ENABLED=false` (TF-IDF fallback) to run without the sentence-transformer model. |

---

## Frontend Structure (`src/`)

**Docs:** `src/README.md` — full frontend dev guide (tech stack, directory layout, env vars, scripts, WS message types, state architecture).

**Config files** (vite.config.ts, tailwind.config.js, tsconfig.json, postcss.config.js) all live inside `src/`.

### Key Components (`src/components/`)

| Component | Role |
|-----------|------|
| `ChatContainer.tsx` | Main message list rendering |
| `ChatInput.tsx` | Query submission, input controls |
| `ChatMessage.tsx` | Individual message rendering |
| `PlanMessage.tsx` | Plan approval/modify/deny UI |
| `SynthesisMessage.tsx` | Synthesis/report message rendering |
| `ResearchReportViewer.tsx` | Full report display; generates PDF via `@react-pdf/renderer` (`ResearchReportDocument.tsx`) |
| `ResearchReportDocument.tsx` | `@react-pdf/renderer` document definition for the PDF export |
| `GraphView.tsx` | Real-time agent progress graph visualization |
| `ResearchControlBar.tsx` | Pause/resume/stop controls |
| `Sidebar.tsx` | Conversation history list |
| `StepResultViewer.tsx` | Per-step result viewer (collapsible inline) |
| `ProgressSpinner.tsx` | Loading spinner (active; `LoadingSpinner.tsx` was removed) |
| `MCPServerManger.tsx` | MCP server status/management UI — shows all servers (builtins + user-added), transport badge, tools count, lock icon for builtins, delete for user-added servers; transport selector (Streamable HTTP / SSE / stdio) with context-aware form fields in add form |
| `DocsViewer.tsx` | Full-screen modal overlay: left sidebar lists all `docs/*.md` files (auto-loaded on open, auto-selects first), right pane renders selected doc as markdown using `react-markdown` + `remark-gfm` + Tailwind `prose`. Closes on Escape or backdrop click. Opened by "Docs" button in `App.tsx` top nav bar. |

### Key Hooks (`src/hooks/`)

| Hook | Role |
|------|------|
| `useWebSocket.ts` | WebSocket connection, message dispatch |
| `useConversations.ts` | Conversation CRUD (local state) |
| `useResearchControl.ts` | Pause/resume/stop via `research_control` WS messages |
| `useConversationGraphs.ts` | Per-conversation research execution graph (supersedes the removed `useGraphState.ts`) |
| `useTypingEffect.ts` | Streaming text typing animation |
| `useTheme.ts` | Light/dark theme management |

### State (`src/contexts/AppContext.tsx`)

Single context provides: messages, conversations, research status, plan status. All shared state lives here.

---

## MCP Servers (`mcp/`)

**Docs:** `mcp/README.md` — full per-server reference (tools, ports, env vars, operational notes, local startup).

> ⚠️ `mcp/memory/` (port 9494) has been **retired** — long-term memory is now handled in-process by `backend/long_term_memory.py` (ChromaDB + `embeddings.py`). The `mcp/memory/` directory still exists for reference but the server is no longer registered or built in docker-compose.

| Server | Port | Docker container name | Tools | ML deps? |
|--------|------|-----------------------|-------|----------|
| `web_search/` | **9393** | `mcp-web-search-server` | 8 tools: web_search, image_search, video_search, search_wikipedia, search_github, get_search_suggestions, search_arxiv, search_semantic_scholar | ❌ lightweight |
| `web_scrape/` | **9292** | `mcp-web-scraping-server` | 1 tool: `scrape_url` (Content-Type gate, binary-null-byte guard, boilerplate removal, 3-stage main content extraction, metadata). Stages: (1) semantic elements `<article>`/`<main>`/role=main/content-id, (2) positive class/id via `_CONTENT_PATTERNS` with link-density guard (<0.5), (3) `_content_score()` = `(text_len + p_count*50) * (1-link_density)`. Helpers `_link_density()` + `_content_score()` are standalone + tested. `mcp/web_scrape/tests.py` — **18 unit tests**, run as `python3 tests.py` from `mcp/web_scrape/`. | ❌ lightweight |
| `file_handler/` | **9191** | `mcp-file-handler-server` | 7 MCP tools (list_files, read_file, streaming_read_file, write_file, upload_file, download_file, run_command) + 3 HTTP routes: `GET /files`, `POST /files/upload`, `DELETE /files/{filename}` — browser-facing file upload/delete endpoints proxied by nginx + vite | ❌ lightweight |

**Dockerfile state (as of 2026-03-18):**

- `backend/Dockerfile`: installs CPU-only torch, pre-bakes `all-MiniLM-L6-v2`; `chromadb` included in `requirements.txt` for in-process LTM.
- `torch==2.10.0` removed from `backend/requirements.txt` (Dockerfile-only install).
- `mcp/web_search`, `mcp/web_scrape`, `mcp/file_handler` Dockerfiles unchanged — no ML deps.

---

## WebSocket Event Flow

```
Client → query
Server → plan                    (Root agent generates plan)
Client → approve_plan | modify_plan | deny_plan
  (if denied)  → Server → plan_denied
  (if approved/modified) →
    Server → step_start          (per step)
    Server → status              (sub-agent updates)
    Server → step_complete       (per step, includes findings)
    (if QA fails) → step_failed
    Server → synthesis           (ReportComposer streaming)
    Server → research_complete   ← timer frozen here (pipeline done)
    Server → report              (compiled document, timer overwritten with final ts)
Server → error                   (any time on failure) ← timer frozen here too
Server → research_stopped        ← timer frozen here (user-initiated stop)
```

### Graph Timer (`ResearchTimer` in `GraphView.tsx`)

The elapsed-time counter in the graph header is controlled by `GraphState.startTime` / `GraphState.endTime` (ISO strings). When `endTime` is set the interval is cleared and the display freezes.

`setResearchEndTime` is called in `App.tsx` on **all four terminal states**:

- `research_complete` — pipeline finished, report still being compiled
- `report` — full document arrived (slightly overwrites the above timestamp)
- `error` — pipeline failed
- `research_stopped` — user manually stopped

Prior to the 2026-03-17 fix, `setResearchEndTime` was only called on `report` and `research_stopped`. The timer continued counting during the `synthesis` → `report` gap (~seconds) after `research_complete`, and ran indefinitely on `error`.

---

### MCP API Endpoints (`/mcp/*`)

| Method + Path | Notes |
|---------------|-------|
| `GET /mcp/servers` | Returns full server info objects (name, transport, url/command, status, tools_count, builtin) via `registry.get_all_server_info()` |
| `POST /mcp/servers` | Register user-defined server; validates transport type; protects builtins; persists to `config/user_mcp_servers.json` |
| `DELETE /mcp/servers/{name}` | Remove user-defined server only; builtins return 403 |
| `GET /mcp/tools` | All tools from all registered servers |

### Concurrency & Task Model

- **`ResearchSessionManager`** (`session_manager.py`): central registry of all running background tasks. `start_job()` creates tasks; `shutdown()` cancels all. `approve_plan` uses `session_manager.start_job()` — never raw `asyncio.create_task()`. Accepts `max_concurrent_sessions` (from `Config.max_concurrent_sessions` / `MAX_CONCURRENT_SESSIONS` env var, default 10); when >0 wraps job coroutines with an `asyncio.Semaphore` gate so at most N pipelines execute simultaneously.
- **`PipelineRunner`** (`pipeline.py`): created per `Orchestrator.execute()` call. Holds the `asyncio.Semaphore(MAX_CONCURRENT_PIPELINES)` and a set of seen query strings for dedup. `MAX_CONCURRENT_PIPELINES = configs.max_workers or 10` (default **10**, not 5 — `pipeline.py` module-level constant).
- **Step parallelism**: `_group_steps()` partitions the plan into ordered execution batches. Steps sharing a `parallel_group` label are coalesced into one batch (even if non-adjacent in the plan — the dict-based accumulator handles LLM interleaving). Each `None`-group step is a singleton sequential batch. All N tasks in a parallel batch are `asyncio.create_task`-ed *before* any acquires the semaphore, so all N are truly concurrent up to the semaphore limit. Dependent steps (where one step needs another's output) run sequentially.
- **QA retry loop**: `LoopAgent` can flag contradictions and trigger re-research. Capped at `max_qa_retries=2` per step.
- **WebSocket disconnect**: drain handler does NOT set `session.state = "cancelled"` — background task continues, client can reconnect.
- **MCP keepalive**: `MCPServerRegistry.start_keepalive()` is called in `lifespan()` after registration. Pings all servers every **60s** (`MCP_KEEPALIVE_INTERVAL` env var). Prevents Docker NAT from silently dropping idle SSE streams. Reconnects automatically on failed ping. (Was 240s — changed after logs confirmed SSE streams died at ~246s before first ping fired.)

---

## RAG / Context Management

- **`SearchResultStore`** (`search_result_store.py`): per-session, per-step. Full tool results chunked and stored with cosine similarity ranking.
- **No hard token caps** anywhere in the pipeline — RAG store with in-process ranking is the sole context management mechanism.
- **`_DEFAULT_ANALYST_TOP_K = 10`**: chunks retrieved per analyst query.
- **`retrieve()` params**: `query`, `top_k`, `max_chars`, `step_id_filter` (only this step), `exclude_step_id` (skip this step's chunks, for cross-step retrieval), `include_superseded`.
- **Binary detection**: `_is_binary(text)` static method checks for >10% non-printable chars. Applied to all text paths in `_extract_text()` — rejects PDF blobs and other binary scrape output silently.
- **`_MAX_TOOL_RESULT_CHARS_IN_MESSAGE = 5000`**: max chars of a tool result kept in `SearchAgent`'s execution_messages window (full content stays in RAG store).
- **`_MAX_SEARCH_HISTORY_MESSAGES = 8`**: sliding window size for `SearchAgent` execution_messages.
- **`_MAX_ANALYST_FALLBACK_CHARS = 8000`**: safety-net fallback when RAG store has no chunks yet.
- **Hierarchical distillation**: controlled by `Config` fields. `config.distill_max_chars` (default 4000) caps raw tool result text. `config.step_summary_max_chars` (default 1500) caps per-step Outline summaries. `config.section_draft_top_k` (default 10) sets top-k RAG chunks per section during Section-Drafting.

### Analyst Context Strategy (as of 2026-04-10)

`_run_analyst()` builds its context from four sources in priority order:

1. **Step-scoped RAG** (`step_id_filter=step.id`, top_k=config.analyst_top_k): chunks from THIS step's own searches, numbered `[Source N]`.
2. **Cross-step corroboration** (`exclude_step_id=step.id`, top_k=5): chunks from OTHER completed steps, injected as "Corroborating evidence from prior steps". *Cross-source corroboration key path.*
3. **Prior step structured claims** (`prior_claims_block`): Last 20 claims + last 5 tensions from `_analyst_output` accumulated across prior steps. Injected as "Structured findings from prior steps" so the analyst can triangulate against curated analytical output.
4. **Fallback** (if step-scoped RAG is empty): parses `sources[*].knowledge_snippet` from `final_message` JSON — explicitly excludes `coverage_notes` and `_tools_used` to avoid meta-commentary pollution.

### Analyst Recommendations Feedback Loop (as of 2026-03-19)

`Orchestrator._analyst_recommendations: List[Dict]` accumulates per-step analyst follow-up suggestions (parsed from `analyst_notes` lines containing "suggest/recommend/search for" etc.). After each step completes, the last 3 entries are forwarded as `extra_context` to the next step's `_run_search()`.

### QA Data Poverty Exit (as of 2026-03-19)

Inside the QA retry loop, `chunk_count(include_superseded=False)` is sampled before and after the targeted re-search. If equal (no new content found), the loop breaks immediately with "data poverty" status rather than burning the second retry budget on equally poor material.

### Knowledge Graph (GraphRAG) — as of 2026-04-01 (Neo4j)

`AsyncLongTermMemory.graph` (`KnowledgeGraph` class) uses native Neo4j graph primitives:

| Node/Edge | Purpose | Key properties |
|---|---|---|
| `:Entity` nodes | Named entities (people, orgs, technologies, concepts) | `name`, `entity_type`, `mention_count`, `source_sessions`, `embedding` (vector index) |
| `:RELATES_TO` edges | Directed triples (`source → relation → target`) | `source_entity`, `target_entity`, `relation_type`, `confidence`, `session_id`, `step_id` |
| `:Community` nodes | LLM-generated cluster summaries | `entity_ids`, `topic`, `created_at`, `embedding` (vector index) |
| `:MEMBER_OF` edges | Entity → Community membership | |

**Extraction:** `Orchestrator._extract_graph_triples(step, analyst_result)` — one LLM call per analyst step after QA pass. Extracts entities + relationships from vetted claims only. Called from `_store_analyst_findings()`.

**Recall:** `Orchestrator._recall_memories(query)` now combines: (1) `graph.recall_graph_context(query)` → entities + relationships + communities, (2) `find_similar(query)` → flat vector search. Both injected into planning prompt.

**Community detection:** `graph.update_communities(summarize_fn)` called post-synthesis. Greedy BFS clustering by relationship co-occurrence (≥2 shared edges). LLM generates a summary per cluster.

**Entity dedup:** Semantic similarity ≥0.92 triggers merge (increment `mention_count`, append `session_id`, keep longer description).

**Relationship traversal:** Uses Cypher variable-length paths (`MATCH path = (seed)-[r:RELATES_TO*1..N]-(other)`) instead of iterative Python BFS.

**Vector indexes:** Three Neo4j vector indexes (`memory_embedding_idx`, `entity_embedding_idx`, `community_embedding_idx`) with 384 dimensions and cosine similarity function.

---

## Environment & Config

### Backend (`backend/.env`)

| Var | Default | Notes |
|-----|---------|-------|
| `MODEL_BACKEND` | `ollama` | `openai` / `ollama` / `bedrock` / `azure` / `gcp` / `huggingface` |
| `AGENT_MODE` | `research` | `research` / `chat` / `self-optimization` |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | |
| `OPENAI_API_KEY` | — | |
| `MEMORY_SERVER_URL` | — | **Removed** — long-term memory is now in-process via `long_term_memory.py` (Neo4j) |
| `SEARCH_SERVER_URL` | `http://localhost:9393` | Docker: `http://mcp-web-search-server:9393` |
| `SCRAPER_SERVER_URL` | `http://localhost:9292` | Docker: `http://mcp-web-scraping-server:9292` |
| `FILE_SERVER_URL` | `http://localhost:9191` | Docker: `http://mcp-file-handler-server:9191` |
| `MAX_TOKENS` | `4096` | Changed from 8192 in wip commit |
| `MCP_KEEPALIVE_INTERVAL` | `60` | Seconds between MCP keepalive pings |
| `EMBEDDINGS_ENABLED` | `true` | Set `false` on memory-constrained hosts to skip in-process embedding model |
| `NEO4J_URI` | `bolt://localhost:7687` | Neo4j Bolt protocol URI. Docker: `bolt://neo4j:7687` |
| `NEO4J_USER` | `neo4j` | Neo4j authentication username |
| `NEO4J_PASSWORD` | `research_pass` | Neo4j authentication password |
| `NEO4J_DATABASE` | `neo4j` | Neo4j database name |
| `NEO4J_EMBEDDING_DIMENSIONS` | `384` | Vector index dimension (must match embedding model) |
| `HF_TOKEN` | — | Avoids HuggingFace unauthenticated rate limits on model download |
| `S2_API_KEY` | *(unset)* | Optional Semantic Scholar API key. Sent as `x-api-key` header by `search_semantic_scholar` to raise the unauthenticated rate limit. No key required for basic use. |
| `OPENAI_HEAVY_MODEL` | `gpt-5.2` | Heavy model for OpenAI backend (planning, synthesis, report). Used by `_select_model()`. |
| `OPENAI_LIGHT_MODEL` | `gpt-5-nano` | Light model for OpenAI backend (search, distillation). |
| `AZURE_HEAVY_MODEL` | `gpt-5.2` | Heavy model for Azure OpenAI backend. |
| `AZURE_LIGHT_MODEL` | `gpt-5-nano` | Light model for Azure OpenAI backend. |
| `AWS_HEAVY_MODEL` | `global.anthropic.claude-sonnet-4-6` | Heavy model for AWS Bedrock backend. |
| `AWS_LIGHT_MODEL` | `global.anthropic.claude-haiku-4-5-20251001-v1:0` | Light model for AWS Bedrock backend. |
| `GCP_HEAVY_MODEL` | `gemini-2.5-pro` | Heavy model for GCP Vertex AI backend. |
| `GCP_LIGHT_MODEL` | `gemini-2.5-flash` | Light model for GCP Vertex AI backend. |
| `OLLAMA_HEAVY_MODEL` | `nemotron-3-nano` | Heavy model for Ollama backend. |
| `OLLAMA_LIGHT_MODEL` | `llama3.2:3b` | Light model for Ollama backend. |

### Frontend (`src/.env`)

| Var | Default | Notes |
|-----|---------|-------|
| `VITE_API_URL` | `http://localhost:9999` | |
| `VITE_WS_URL` | `ws://localhost:9999/ws/research` | Only WS endpoint; baked at build time |

---

## Make Commands (from repo root)

| Command | Does |
|---------|------|
| `make run-all` | Full stack: UI + backend + MCP servers |
| `make run` | Backend + UI only |
| `make run-fe` | Frontend only (`cd src && npm run dev`) |
| `make run-agent` | Backend without UI |
| `make run-mcp` | MCP servers only |
| `make stop-all` | Stop all |

---

## CI / Workflows (`.github/workflows/`)

| File | Trigger | What it does |
|------|---------|-------------|
| `ci.yaml` | push/PR → `main` | Checks out repo, installs `backend/requirements.txt` (pip-cached), runs `python smoke_test.py` from `backend/` with `EMBEDDINGS_ENABLED=false`. Python 3.12 on `ubuntu-latest`. |

**Note:** `EMBEDDINGS_ENABLED=false` is mandatory in CI — without it the job would attempt to download the `all-MiniLM-L6-v2` sentence-transformer model (~90MB) on every run. The smoke tests that depend on semantic similarity fall back to TF-IDF, which is sufficient for the structural and unit-level assertions they make.

---

## Current Status

**As of 2026-03-17: FULLY WORKING END-TO-END** — validated via both `test_e2e.py` and deployed Docker containers (`make run-all`).

All subsystems confirmed green:

- Async model backends (OpenAI, Bedrock, Azure, GCP, Ollama, HuggingFace)
- MCP keepalive (httpx health-endpoint ping, 60s interval)
- Docker build (CPU-only torch, pre-baked HF model)
- Five-agent orchestration pipeline (Root → Search → Analyst → Loop → ReportComposer)
- Session manager + semaphore-capped pipeline runner
- RAG / SearchResultStore per-session context management
- Graph timer correctly frozen on all terminal states (`research_complete`, `report`, `error`, `research_stopped`)

---

## Known Tech Debt / Open Issues

- `advanced_features.py` credibility scoring is heuristic and not well-tested
- `optimization.py` self-optimization strategies are experimental
- `chroma_data/` is the legacy ChromaDB persist directory. Retained for migration via `scripts/migrate_chroma_to_neo4j.py`. Can be deleted after successful migration.
- Neo4j data persisted in Docker named volume `neo4j_data` (mapped to `/data` in container). Reset with `docker volume rm research-assistant_neo4j_data`.
- `pipeline.py` query dedup uses a flat string set — collisions possible if the same semantic query is worded differently (future: embed + cosine dedup)
- `backend/instructions/` bind-mount (`./backend/instructions:/app/instructions`) added to both `docker-compose.yml` and `docker-compose-full.yml` (2026-03-19). **Not yet mirrored in `infra/azure/containers.tf`** — needed if self-optimize is used in ACA.
- Session state is **in-memory only** — all sessions lost on restart; no persistence layer
- **Frontend nginx proxy**: `host.docker.internal:9999` in `src/nginx.conf` doesn't resolve in Azure Container Apps. Must use envsubst in nginx.conf (replace proxy target with `${NGINX_BACKEND_URL}`) or bake `VITE_WS_URL` as a build arg at CI time. See `infra/azure/README.md §4`.

---

## Infrastructure (`infra/azure/`)

Full Terraform IaC for deploying the stack to Azure Container Apps. Validated with `terraform validate` against azurerm ~4.0.

| File | Purpose |
|------|---------|
| `main.tf` | Terraform + azurerm/random providers, partial `azurerm` backend, shared `locals` (prefix, tags, MCP internal FQDNs) |
| `variables.tf` | All inputs: SP credentials, location, project_name, image_tag, ACR SKU, LLM creds, MCP API/secret keys, embeddings toggle |
| `outputs.tf` | frontend_url, backend_url, backend_ws_url, container_registry_login_server, image_names map |
| `backend.conf` | Remote state connection values — populate from secrets before `terraform init`; gitignored |
| `terraform.tfvars` | Credential template — gitignored; generated from secrets at CI/CD runtime |
| `resource_group.tf` | `azurerm_resource_group` |
| `registry.tf` | ACR (`acr_sku` variable) + user-assigned managed identity with `AcrPull` role, shared by all Container Apps |
| `storage.tf` | Storage account + two Azure File Shares: `chroma-data` (10 GB) and `file-handler-data` (10 GB) |
| `log_analytics.tf` | Log Analytics workspace (mandatory for Container Apps environment) |
| `container_env.tf` | Container Apps environment + two `azurerm_container_app_environment_storage` mounts for the File Shares |
| `containers.tf` | All 6 Container Apps (see table below) |

### Container Apps

| App resource | Ingress | Port | CPU/Mem | Volumes | Notes |
|---|---|---|---|---|---|
| `mcp_web_search` | Internal | 9393 | 0.25 / 0.5Gi | — | 1/2 replicas |
| `mcp_web_scrape` | Internal | 9292 | 0.25 / 0.5Gi | — | 1/2 replicas |
| `mcp_file_handler` | Internal | 9191 | 0.25 / 0.5Gi | file-handler-data → /app/data | 1/1 replicas (stateful) |
| `backend` | **External** | 9999 | 2.0 / 4Gi | chroma-data → /app/chroma_data | transport=auto (WebSocket), 1/3 replicas; do not scale to zero |
| `frontend` | **External** | 80 | 0.25 / 0.5Gi | — | 1/3 replicas; depends_on backend |

### ACA-specific schema notes (azurerm 4.x)

- `volume_mounts {}` (not `volume_mount`) inside container blocks
- `storage_account_id` (not `storage_account_name`) in `azurerm_storage_share`
- No `sticky_sessions_affinity` attribute in `azurerm_container_app` ingress block
- `transport = "auto"` enables WebSocket on the backend ingress

### State storage bootstrap

The `rg-tfstate` resource group + storage account + `tfstate` blob container must be created manually **before** `terraform init`. See `infra/azure/README.md §1`.

### gitignore additions (2026-03-18)

Added to `.gitignore`: `infra/azure/terraform.tfvars`, `infra/azure/backend.conf`, `infra/azure/.terraform/`, `infra/azure/.terraform.lock.hcl`, `infra/azure/*.tfstate*`, `infra/azure/*.tfplan`
