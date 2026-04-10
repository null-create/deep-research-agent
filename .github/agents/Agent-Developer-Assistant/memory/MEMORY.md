# Agent Memory — Research Assistant

## 2026-04-10 (Knowledge Graph Phase 1–5 Enhancement + Graph Pruning)

- **Phase 1 — Core primitives**: Added `Source` node type (url, title, credibility_score uniqueness constraint on `url`). Temporal props (`last_confirmed`, `confirmation_count`) on both Entity nodes and RELATES_TO edges. `stats()` now returns 6 keys (entities, relationships, communities, contradictions, sources, hierarchies).
- **Phase 2 — New relationship types**: `IS_A` (hierarchy), `CONTRADICTS` (between entities with linked rel references), `SOURCED_FROM` (entity → Source). 13 new async KG methods: `store_hierarchy`, `store_contradiction`, `find_contradictions`, `store_source`, `link_to_source`, `get_provenance`, `recent_entities`, `recent_relationships`, `session_diff`, `find_paths`, `find_common_neighbors`, `decay_confidence`, `prune`.
- **`store_relationship()` now deduplicates**: queries for existing (source, target, relation_type) triple; if found: merges (max confidence, appended evidence, incremented confirmation_count, returns `merged: True`); new edges get `confirmation_count: 1`.
- **`find_entities()` gains `include_hierarchy` param**: `OPTIONAL MATCH (node)-[:IS_A*1..3]->(anc)` returns `ancestors` list.
- **`recall_graph_context()` enhanced**: `min_confidence`, `include_contradictions`, `include_provenance` params; optional KNOWN CONTRADICTIONS + SOURCE PROVENANCE sections.
- **`prune(min_confidence, max_age_days, dry_run=True)`**: 3-pass: stale RELATES_TO → orphan Entity nodes → dangling CONTRADICTS. Safe default `dry_run=True`.
- **`decay_confidence(half_life_days=30)`**: Batch Cypher `exp(-0.693 * days_old / half_life)`, floor at 0.01.
- **Phase 3 — Orchestrator integration**: `_graph_mutations_since_community_update` counter; dynamic entity_limit in `_recall_memories` (based on `graph.stats()` entity count); enriched extraction prompt requests `parent_type`, `source_url`, `contradicts_prior`; context injection via `recall_graph_context` before each extraction; IS_A + provenance wiring post-store; smart community gate (only runs if `mutations >= config.graph_community_min_mutations`); `decay_confidence` called post-synthesis.
- **Config**: Two new fields: `confidence_decay_half_life` (default 30), `graph_community_min_mutations` (default 3).
- **Phase 4 — API endpoints**: 9 new `/graph/*` REST endpoints in `api_server.py` (stats, entities, relationships, communities, contradictions, provenance, paths, session/{id}, prune). `POST /graph/prune` is destructive; all others are GET.
- **Phase 5 — Smoke tests**: 9 new test functions (all static). All 57 tests pass.
- **CRITICAL neo4j driver 5.x pattern**: When Cypher returns a relationship object and `.data()` is called, neo4j driver 5.x serializes it as a 4-tuple `(start_id, end_id, type_name, properties_dict)` — NOT a dict. `rec["rel"].get(...)` will raise `'tuple' object has no attribute 'get'`. **Always extract relationship properties by name in Cypher** (`rel.prop AS prop`) instead of `RETURN rel`. This applies to any query where a relationship edge is returned as a bare column.

## 2026-04-01 (ChromaDB → Neo4j Migration)

- **Full migration of `long_term_memory.py` from ChromaDB to Neo4j.** All ChromaDB internals replaced with Neo4j async driver (`AsyncGraphDatabase`). Public API (`AsyncLongTermMemory` class + `KnowledgeGraph`) preserved — callers unchanged except constructor args.
- **Neo4j native graph model:** Entities, relationships, and communities are now first-class Neo4j primitives (`:Entity` nodes, `:RELATES_TO` edges, `:Community` nodes + `:MEMBER_OF` edges) instead of flat ChromaDB collections with JSON metadata. Relationship traversal uses Cypher variable-length paths instead of iterative Python BFS.
- **Vector indexes replace HNSW collections:** Three Neo4j vector indexes (`memory_embedding_idx`, `entity_embedding_idx`, `community_embedding_idx`) with 384 dimensions and cosine similarity function. Created via `_create_schema()` on init.
- **`_NoOpEmbeddingFunction` deleted.** Was needed for ChromaDB's embedding function interface. Neo4j vector indexes are optional — if embeddings disabled, zero-vectors are stored but similarity search is meaningless (same degradation behavior).
- **Natively async:** Neo4j driver is fully async (`AsyncGraphDatabase.driver`) — all `asyncio.to_thread()` wrappers from the ChromaDB era are gone. `close()` method added for clean driver shutdown (called in `api_server.py` lifespan shutdown).
- **Config changes:** `chroma_persist_dir`/`chroma_collection_name` → `neo4j_uri`/`neo4j_user`/`neo4j_password`/`neo4j_database`/`neo4j_embedding_dimensions`.
- **Docker:** `neo4j:5.26-community` service added with APOC plugin, healthcheck via `cypher-shell`, named volume `neo4j_data`.
- **Migration script:** `scripts/migrate_chroma_to_neo4j.py` — one-time, idempotent (uses MERGE). Migrates flat memories, entities, relationships, and communities in order.
- **Files modified:** `long_term_memory.py` (full rewrite), `config.py`, `api_server.py`, `cli.py`, `smoke_test.py`, `requirements.txt`, `docker-compose.yml`, `docker-compose-full.yml`, `README.md`, `docs/RAG_NOTES.md`, `docs/MCP_SERVERS.md`.
- **Files created:** `scripts/migrate_chroma_to_neo4j.py`.
- **Smoke tests:** All pass. Integration tests skip gracefully when Neo4j is unavailable.

## 2026-03-25 (Community update loop + synthesis UK spelling gap)

- **Synthesis step guard misses UK spelling:** `_SYNTHESIS_SIGNALS` had "synthesize all" (US 'z') but not "synthesise all" (UK/AU 's'). LLM used "Synthesise all findings from steps 1–9" in the plan — bypassed the guard, ran the full Search→Analyst→QA pipeline as an extra step. Fixed: added "synthesise all" and "compile and synthesise" to `_SYNTHESIS_SIGNALS`. Lesson: always add both spellings for synthesis/compile variants.
- **`_store_community_sync` triggered `_NoOpEmbeddingFunction` on every write:** Called `_communities_col.add()` / `.update()` without `embeddings=`. ChromaDB called the NoOp EF per document → DEBUG log spam that looked like a loop; communities stored with zero-vectors (useless for cosine queries). Fix: pre-compute embedding in async `update_communities` loop, pass `vector` to `_store_community_sync`, which now uses `embeddings=[vector]` when available.
- **`update_communities` had no cap on sequential LLM calls:** With 9 research steps × N entities/relationships, could produce many clusters each costing a sequential LLM call. Ran ~2.5 min post-report. Fixed: `max_communities: int = 10` param added; loop is `clusters[:max_communities]`.
- **Post-synthesis timing pattern:** Session job stays alive until `synthesize()` generator fully consumed — community LLM calls happen after the `report` event, so "Cancelling 1 running job" at shutdown doesn't mean the report wasn't delivered.
- **Smoke tests: 47 total** (unchanged count; updated `test_synthesis_step_detection` with UK-spelling cases; `test_orchestrator_community_detection_post_synthesis` now checks `max_communities` param exists).

## 2026-03-25 (Synthesis step leak — prevent final step from running Search→Analyst→QA)

- **Root cause:** The Root planning agent (despite CRITICAL CONSTRAINT in system prompt) would frequently include a final "synthesis" step in the research plan (e.g. "Synthesize all findings and produce a comprehensive report"). `execute()` routes all plan steps through `_run_step()`, which runs the full Search→Analyst→QA pipeline — wasting compute and generating duplicate content that then gets processed again by `synthesize()`.
- **Fix 1 (preventive): `_root_system_prompt()`** — Updated the parallelism rules section to remove the example mentioning "a synthesis step", and added an explicit `CRITICAL CONSTRAINT` paragraph instructing the Root NOT to include synthesis/report-generation steps. Real synthesis is always handled by the ReportComposer after all research steps complete.
- **Fix 2 (safeguard): `_is_synthesis_step(step)` static method** — Added to `Orchestrator`. Returns `True` when `step.description` matches any phrase in `_SYNTHESIS_SIGNALS` (19 entries like "synthesize all", "final synthesis", "generate a final report", etc.). Precision-tuned: "synthesize findings from X" (legitimate research) does NOT match; "synthesize all findings" (report step) does.
- **Fix 3 (safeguard): `_run_step()` synthesis guard** — Added check right after the existing dedup check, before the `try:` block. If `_is_synthesis_step()` returns `True`, the step is marked `COMPLETED` with a skip message, emits `step_complete` with `skipped_synthesis: True` flag, and returns immediately. Works for both sequential and parallel step execution paths.
- **Frontend update: `App.tsx` `step_complete` case** — If `data?.skipped_synthesis` is `True`, chat content shows "Skipped step: [description] — report synthesis is handled automatically by the ReportComposer." instead of "Completed step: [name]".
- **Smoke tests: 47 total** (was 46). Added `test_synthesis_step_detection`: tests 19 synthesis descriptions are caught, 9 legitimate research descriptions are not, and asserts presence of `CRITICAL CONSTRAINT` and `skipped_synthesis` in source. All 47 pass.
- **Pattern:** Two-layer defense is the right approach for LLM instruction failures — update the system prompt (layer 1), add a code-level guard that catches instruction violations (layer 2). The code guard is the final authority; the prompt update reduces frequency.

## 2026-03-25 (Outline retry + structured report metadata)

- **Outline retry:** Phase A calls `self.agents.report.run()` for the outline JSON. 65/67 observed failures were empty responses (not malformed JSON). Added a single retry immediately after the first call when `content.strip()` is falsy — before the parse block. The parse/fallback logic is unchanged; the retry just gives the model a second chance before falling through to defaults.
- **Structured report event metadata:** `report` WebSocket event `data` dict now has three new top-level fields: `title` (= `query`; no LLM-derived title exists in the multi-pass assembly flow), `sources` (normalised list of `{title, url}` from `all_sources`), `key_findings` (content string of the first drafted section whose title contains "finding", empty string if absent). Extracted from already-computed data — zero extra LLM calls.
- **Pattern:** The `_DEFAULT_SYSTEM_PROMPT` full-report format is intentionally NOT used in the multi-pass flow (Phase B bypass from previous session). The report header in `doc_parts` is always `"RESEARCH REPORT"` with no LLM-derived title line, so `query` is the canonical title.
- **Frontend changes required:** `src/types/conversation.ts` `SynthesisData` gained `key_findings?: string` and `sources?: Array<{title: string; url: string}>`. `App.tsx` `report` case updated to use `data?.title ?? 'Research Report'` (was hardcoded `'Research Report'`) and wire `key_findings` + `sources` into the stored message. Rendering components (`SynthesisMessage`, `ReportViewer`, `ResearchReportDocument`) needed no changes — they already read `synthesis.title` with a fallback.
- **Benchmark artifact:** `data/benchmarks/ANALYSIS-1.md` created — formatted run #4 analysis capturing all findings plus the three fixes applied this session (section duplication, outline retry, structured metadata), with open issues for the next run.

## 2026-03-25 (Section duplication bug — synthesize() Phase B)

- **Root cause:** `ReportComposer._DEFAULT_SYSTEM_PROMPT` always instructs the LLM to produce a full multi-section report (RESEARCH REPORT → EXECUTIVE SUMMARY → KEY FINDINGS → NOVEL INSIGHTS → RECOMMENDATIONS → KNOWLEDGE GAPS → REFERENCES). Phase B of `synthesize()` calls `self.agents.report.run()` once per section with a user-prompt saying "Draft ONLY the '{sec_title}' section" — but the system prompt overrides this. For generic section names (especially "Analysis"), the LLM produces a full report structure, embedding all section headers in the content. The outer `doc_parts` assembly then prepends each section title again, creating duplicates.
- **Fix:** In Phase B, replaced `self.agents.report.run()` with a direct `self.agents.report.model.generate()` call using a minimal single-section system prompt. This bypasses `_DEFAULT_SYSTEM_PROMPT` without touching any class interfaces. One-line conceptual change, contained entirely within `synthesize()`.
- **Affected:** 100% of benchmark reports showed duplication. EXECUTIVE SUMMARY, KEY FINDINGS, RECOMMENDATIONS, KNOWLEDGE GAPS appeared twice; NOVEL INSIGHTS and REFERENCES appeared once (inside the "Analysis" section LLM output) + once in outer assembly.
- **Pattern:** `_DEFAULT_SYSTEM_PROMPT` is designed for single full-report calls (old synthesis style). Phase A/B/C multi-pass architecture was added later but Phase B's calls are incompatible with that full-report system prompt. Whenever `SubAgent.run()` is used for sub-task drafting, always verify the system prompt won't generate the entire parent artifact.

## 2026-03-24 (web_scrape main-content heuristic expansion)

- **`_extract_main_content()` rewritten as a 3-stage pipeline.** Stage 1 (semantic elements) unchanged. Stage 2 (new): positive class/id pattern match against `_CONTENT_PATTERNS` (`\b(content|article|post|entry|story|prose)\b`) with a link-density guard (<0.5) to skip tag clouds and nav menus that happen to have a `content` class. Stage 3 (improved): replaced raw `len(get_text())` with `_content_score()` composite: `(text_len + p_count * 50) * (1 - link_density)`. Paragraph count bonus generalises the old character-count heuristic; link-density penalty eliminates nav menus as false positives.
- **Two new helper functions:** `_link_density(el)` (ratio of `<a>` text to total text, 0–1) and `_content_score(el)` (composite). Both guard against empty elements (no ZeroDivisionError). Both importable and independently tested.
- **`_CONTENT_PATTERNS` constant** added alongside `_BOILERPLATE_PATTERNS`. `\b` word-boundary anchors prevent partial-match false positives (e.g. `"account"` does not match `"content"`).
- **Stage 3 score threshold** is 300 composite units (same number as the old char threshold, but now effectively much stricter for link-dense navs and lenient for paragraph-rich articles).
- **8 new tests** added to `mcp/web_scrape/tests.py` (18 total, was 10). All 18 pass. Run as `python3 tests.py` from `mcp/web_scrape/`.

## 2026-03-24 (Benchmark Run #3 — Shutdown race condition + cancel-of-complete fix)

- **33/100 tasks succeeded, 40 failed, 27 never ran.** Root cause was NOT OOM this time — a graceful `Shutting down` at 22:43:52 (likely accidental `make restart-all` or SIGTERM) cancelled 21 active sessions, and subsequent connections hit a shutdown race condition.
- **Three cascading failure modes identified:** (1) 10 "Research session was cancelled" — active sessions killed by `session_manager.shutdown()`, (2) 10 `'NoneType' object has no attribute 'start_job'` — `app.state.session_manager` nulled out during shutdown Step 6 while new WS connections still arriving, (3) 4 "Server not fully initialised" + 16 hard connection failures during/after restart.
- **11 of 21 cancelled sessions already had a `report` event** in their replay log — synthesis was done, the cancel arrived during post-report cleanup status events or the `finally` block. These were false cancellations.
- **Fix 1: Shutdown race condition.** Added `app.state.shutting_down = False` flag during lifespan startup. `_shutdown()` sets it to `True` as its very first action, before cancelling any jobs. WS handler guard now checks `getattr(app_state, "shutting_down", False)` alongside the existing `agent_pool` check. New sessions during shutdown get a clean "Server not fully initialised" error immediately instead of crashing on null `session_manager`.
- **Fix 2: Cancel of already-complete sessions.** `_run_session`'s `CancelledError` handler now checks `any(e.get("type") == "report" for e in s.replay_log)`. If a report event was already emitted, the handler sets `s.state = "complete"` instead of `"cancelled"` and does NOT emit the spurious error message. The benchmark client already received the report via the drain — it just needs the session to not emit a contradicting error afterward.
- **Concurrency note:** `run-bench.sh` was at `--concurrency 10` (commit `1c391b6`). Previous runs crashed at 10 (OOM). Should be lowered for next run, though this run's failure was NOT OOM.
- **Smoke tests: 45 total** (was 43). Added `test_shutdown_guard_rejects_during_shutdown` + `test_cancel_preserves_complete_sessions`. All pass.

## 2026-03-24 (Session concurrency limiter for benchmark scalability)

- **Backend-side session semaphore added.** `ResearchSessionManager` now accepts `max_concurrent_sessions` (default 10 via `MAX_CONCURRENT_SESSIONS` env var). When set >0, `start_job()` wraps the coroutine in a `_gated()` wrapper that acquires the semaphore before executing. Excess jobs are registered and start immediately as asyncio tasks, but block on semaphore acquisition — their plan generation can still proceed, the gate is on the execute+synthesize pipeline. Set to 0 to disable.
- **Docker memory raised 6GB→10GB** in both `docker-compose.yml` and `docker-compose-full.yml`. With `release_memory()` + the session semaphore capping concurrent pipelines, 10GB provides comfortable headroom for 10 concurrent shallow sessions.
- **Run #3 retrospective:** The 4.5-hour run at concurrency=10 did NOT OOM — `release_memory()` fix from Run #2 is working. The failure was entirely from the external shutdown + the two race conditions now fixed.
- **Smoke tests: 46 total** (was 45). Added `test_session_manager_concurrency_semaphore`. All pass.

## 2026-03-24 (Azure Terraform Audit + Fixes)

- **Critical nginx bug fixed:** `src/nginx.conf` hard-coded `host.docker.internal:9999`/`9191` — unusable in Azure Container Apps. `NGINX_BACKEND_URL` env var was set in Terraform but never consumed. Fixed by converting nginx.conf to use `${NGINX_BACKEND_URL}` and `${NGINX_FILE_HANDLER_URL}` placeholders; `src/Dockerfile` now copies conf to `/etc/nginx/templates/default.conf.template` so nginx's native envsubst mechanism substitutes at startup. `NGINX_FILE_HANDLER_URL` added to frontend Container App env in Terraform.
- **Four missing persistent volumes added to backend:** `logs` (session checkpoints — the P0 data-loss vector), `config` (model_settings.json persistence), `instructions` (self-optimization RESEARCH-METHODS.md). Each gets its Azure File Share + environment-level storage resource + volume block + mount. `LOG_DIR=/app/logs` env var now explicit in Container App.
- **AWS/Bedrock variables added:** `aws_base_url` + `aws_api_key` variables in `variables.tf`, wired to backend env. BedrockBackend uses OpenAI-compatible endpoint (not boto3), so only these two variables are needed.
- **Terraform resource count increased:** was ~15 resources, now ~19 (3 new file shares + 3 new env-level storage mounts).
- **`docs/` bundled in backend image (2026-03-24 follow-up):** Build context lifted from `./backend` to `.` (repo root). `backend/Dockerfile` now uses `COPY backend/ .` + `COPY docs /app/docs`. Root `.dockerignore` added to exclude `src/`, `mcp/`, `infra/`, `.git/`, etc. Both compose files updated (`context: .`, `dockerfile: backend/Dockerfile`). Azure README build command updated to `docker build -f backend/Dockerfile -t ... .`. Local dev unaffected: the `./docs:/app/docs` bind-mount in compose shadows the baked-in copy at runtime.

## 2026-03-24 (Benchmark Run #2 — Five fixes for complete run survivability)

- **95/100 tasks failed (5 succeeded then server OOM-crashed).** Same root cause family as Run #1 but at `concurrency=3`. 3GB Docker limit still insufficient: completed sessions held full Orchestrator state (RAG store, claims, step summaries) in memory with no eager cleanup. After 5 completions + 3 in-progress, memory exceeded limit → container OOM-killed → 92 tasks got instant "did not receive a valid HTTP response" during restart.
- **Three compounding problems identified:**
  1. **No session memory cleanup** — completed sessions held heavy Orchestrator buffers until TTL eviction (1 hour). `release_memory()` added to Orchestrator, called in `_run_session` finally block.
  2. **Resume logic bug** — `load_existing_results()` collected ALL task IDs (success + failure). Failed tasks were never retried on `--resume`. Fixed: only successful results (with `article` field) are preserved; output file rewritten to drop failures.
  3. **No connection retry** — transient server unavailability (OOM restart) caused permanent task failure. Added exponential backoff retry (5 attempts, 15s base) for `ConnectionClosed`/`WebSocketException`/`OSError`.
- **Additional fixes:** Docker memory limit raised 3GB→6GB. Default concurrency in `run-bench.sh` lowered 3→2. Execution event logging changed from INFO + full JSON dump to DEBUG + type-only (massive IO/memory reduction).
- **Smoke tests: 43 total** (was 42). Added `test_orchestrator_release_memory` (AST check + api_server wiring). All pass.

## 2026-03-23 (Benchmark Run #1 — OOM crash at concurrency=10)

- **96/100 benchmark tasks failed.** Root cause: Docker container OOM-killed while ~10 sessions were in synthesis simultaneously. `concurrency=10` (default) caused all active sessions to hit the memory-intensive synthesis phase at once. No graceful shutdown logged (0 `Session removed`, 0 `Shutting down` entries) — confirms hard kill.
- **All 9 persisted sessions had `state=executing` with last event `research_complete`.** Every session completed all steps successfully but died during synthesis. The benchmark evaluator requires a `report` event — none were emitted.
- **Diagnosis clues for future OOM:** (1) 0 `Session removed` entries, (2) no shutdown/cancel log messages, (3) NDJSON shows a fresh `Logging initialized` mid-stream with only a 4s gap from last activity, (4) all session checkpoints frozen at same phase.
- **Fix:** re-run with `--concurrency 3 --resume`. Long-term fix: consider a synthesis semaphore in the backend to cap concurrent synthesis operations.

## 2026-03-23 (Model Settings UI)

- **New feature: frontend model configuration.** "Model Settings" sub-panel added to Sidebar settings tab → opens `ModelSettings.tsx` with Provider section + 4 per-agent sections (Root/Search/Analyst/QA).
- **BUG FIXED: `_select_model()` used module-level `config` not `self.config`.** The orchestrator had a scoping bug: `_select_model()` read the global config singleton (from `config.py`) instead of `self.config` (the per-session config). Fixed by `cfg = self.config` at top of method. Cross-session config updates would have been silently ignored prior to this fix.
- **Per-agent model overrides:** `root_model_override`, `search_model_override`, `analyst_model_override`, `qa_model_override` added to `Config`. `_select_model()` checks these before heavy/light fallback. UI shows each agent with a separate model ID field.
- **Sampling params wired through:** `root/search/analyst/qa_temperature`, `root/search/analyst/qa_top_p`, `root/search/analyst/qa_max_tokens` added to Config. Temperature + max_tokens forwarded to all `_root_backend.generate()` calls and through `SubAgent.run()` to the backend. `top_p` is stored in Config but NOT forwarded (backends don't have it in their generate() signatures).
- **Config persistence:** `POST /config` writes `config/model_settings.json` (atomic via `.tmp` rename). Lifespan loads and overlays these on startup. Pattern mirrors `user_mcp_servers.json` in same directory.
- **Hot reload:** `POST /config` rebuilds `model_backend` + `agent_pool` in-memory without process restart. New research sessions pick up new config immediately.
- **Smoke test:** `test_select_model_reads_config` updated to check `cfg.xxx` pattern (was `config.xxx`). All 42 tests pass.

## 2026-03-23 (Docs Viewer UI Feature)

- **New feature: in-app documentation browser.** "Docs" button in top nav bar opens a full-screen modal (`DocsViewer.tsx`) that loads all `.md` files from `docs/` via two new backend REST endpoints (`GET /project-docs`, `GET /project-docs/{filename}`).
- **GOTCHA: FastAPI reserves `/docs` for Swagger UI.** Any route named `/docs` is silently intercepted — this is not a 404, it returns the Swagger HTML page. Always use a different prefix for custom doc-serving endpoints. Route was renamed to `/project-docs`.
- **GOTCHA: `docs/` not inside the Docker build context.** `backend/Dockerfile` context is `./backend` — sibling directories like `docs/` must be bind-mounted as a volume. All three compose files needed `- ./docs:/app/docs`. The `_DOCS_DIR` path must use `os.path.dirname(__file__)` (resolves to `/app` inside the container) not `../docs`.
- **Pattern used:** mirrors the Research Methods viewer already in `Sidebar.tsx` — `react-markdown` + `remark-gfm` + Tailwind `prose` classes, same light/dark styling. No new npm deps needed.
- **Security note:** `GET /docs/{filename}` validates with `os.path.basename` + `.md` extension check to block path traversal. `filename` must not contain `/` or `\`.
- **Fragment wrapper:** `App.tsx` return was changed from a bare `<div>` to `<>...</>` fragment so `DocsViewer` can be a sibling (portal-style overlay) to the main layout div without nesting it inside.
- **New files:** `src/components/DocsViewer.tsx`. New API client methods: `listDocs()`, `getDoc(filename)`.

## 2026-03-23 (S8 Bug-Fix Session — 3 issues from pipeline analysis)

- **P1 FIXED: NDJSON timestamp `%f` bug.** `_NdjsonFormatter` was calling `self.formatTime()` which delegates to `time.strftime()` — that does NOT expand `%f`. Every timestamp had the literal string `%f` instead of microseconds. Fix: overrode `formatTime()` in `_NdjsonFormatter` using `datetime.fromtimestamp(record.created, tz=timezone.utc).strftime(datefmt or _ISO_FMT)`. Sub-second ordering now works.
- **P2 ALREADY FIXED (step_complete logging):** S8 analysis reported `step_complete` not logged at INFO, but commit `19e84d8` had already added `logger.info("[Step %d] complete (qa_retries=%d, contradictions=%d)", ...)` at the end of `_run_step`. The smoke test `test_orchestrator_step_level_logging` already validates this. No code change needed.
- **P2 FIXED: Confidence field inconsistency.** `AnalystAgent._DEFAULT_SYSTEM_PROMPT` had no `confidence` field in the claim schema — the model produced it non-deterministically. Added rule #6 to system prompt: "For EVERY claim, set confidence: corroborated / partially_corroborated / single_source" and added the field to the JSON schema. Downstream `record_event` call already reads `c.get("confidence", "")` — no other changes needed.
- **Smoke tests: 42 total** (was 40). Added `test_analyst_prompt_confidence_field` + extended `test_ndjson_formatter_valid_json` with `%f`-literal assertion and decimal-point check. All 42 pass.

## 2026-03-24 (Auto PDF export on report completion)

- **New feature: every report is auto-exported to `data/reports/` as a PDF.** Works for both API/frontend mode and CLI headless mode since the hook is in `orchestrator.synthesize()`.
- **Hook location:** `orchestrator.py` synthesize() — right after `document` is assembled and `context.save_step("final_report", ...)`, before yielding the `report` event. Pattern: `from report_exporter import export_report_pdf; await export_report_pdf(document, query, self.session_id)`. Wrapped in `try/except` so export failure never interrupts the pipeline.
- **`backend/report_exporter.py`:** new module. `_generate_pdf()` (sync, called via `asyncio.to_thread`) uses `fpdf2` to render sections. `export_report_pdf()` is the async entry point. File naming: `{YYYY-MM-DD}_{session_id[:8]}_{title_slug}.pdf`. `REPORTS_DIR` env var controls destination; fallback is `Path(__file__).parent.parent / "data" / "reports"` (resolves to `<repo_root>/data/reports` locally).
- **fpdf2 gotcha:** In fpdf2 2.7+, `multi_cell()` has `new_x=XPos.RIGHT` as default (changed from LMARGIN). All `multi_cell` calls must explicitly pass `new_x="LMARGIN", new_y="NEXT"` or the x coordinate will be at the right margin, causing "Not enough horizontal space" on the next call.
- **`fpdf2==2.8.7`** added to `requirements.txt`. Installed in venv.
- **Docker:** `REPORTS_DIR=/app/data/reports` env var added + `./data/reports:/app/data/reports` volume bind-mount added to both `docker-compose.yml` and `docker-compose-full.yml` under the `research-assistant` service.
- **`data/reports/` pre-existed** with old frontend-generated PDFs — the directory was already in the repo.
- All 46 smoke tests pass unchanged.

## Working Practices

- **Log analysis: always use temp Python scripts** instead of heredoc or inline shell approaches. Python scripts handle multiline JSON, malformed records, and complex aggregations far more reliably than shell pipelines or awk/jq. Create the script with `create_file`, run it, then `rm` it when done.

## 2026-03-22 (Session 8 Analysis — 33678dbd, Shallow Mode, 14 Steps, Quantum Computing)

- **Report killed again mid-synthesis — 4th occurrence in 8 sessions.** Same failure pattern: `make restart-all` during synthesis window. ~2.5 min into synthesis (outline fallback generated, Phase B started, 1 section drafted) when shutdown triggered. Session state: `cancelled`. Checkpoint was successfully persisted — persistence fix works but doesn't auto-recover.
- **Cross-session report completion: 2/8** (S2, S7 only). No deep-mode session has ever produced a report.
- **NEW BUG: NDJSON timestamp `%f` never resolves.** `_ISO_FMT = "%Y-%m-%dT%H:%M:%S.%fZ"` used in `_NdjsonFormatter.formatTime()` — but `time.strftime()` does NOT support `%f` (that's `datetime.strftime` only). All timestamps show literal `%f` instead of microseconds. Present since S7 but not previously caught. Fix: override `formatTime()` to use `datetime.fromtimestamp(record.created, tz=timezone.utc).strftime(_ISO_FMT)`.
- **Outline parse failure recurred** despite the `raw = content or ""` guard fix. `char 0` error means string was empty. S7 succeeded, S8 failed — confirms non-deterministic (model-dependent). May need to retry the LLM call once on empty response rather than falling back silently.
- **Orchestrator logs `step_start` at INFO but not `step_complete`** — asymmetric logging gap introduced in the S6→S7 fix. All 14 step_complete events only exist in `api_server` WS relay log, not orchestrator logger.
- **Analyst claim volume up 44%** (203 vs 141 in S7). Avg 14.5 claims/step. Data quality self-assessment excellent (14/14 steps include critical notes). But confidence field inconsistently populated — Steps 1,4 have per-claim labels; Steps 2-14 mostly empty strings.
- **Scrape failure rate tripled** (81% success vs 97% in S7). Quantum computing targets more paywalled/ephemeral sources. 5× HTTP 403, 3× HTTP 404.
- **`_step_sources` still empty — 8th consecutive session.** Broken `_extract_sources()` data path.
- **Step summaries 13/14 truncated at 800 chars.** Same as S7 (14/15). Config-level issue.
- **New cosmetic error: MCP anyio cancel scope mismatch on shutdown.** `streamable_http.terminate_session()` called from different task than scope was created. MCP SDK issue, not ours.

## 2026-03-22 (Session 7 Analysis — af45bde5, Shallow Mode, 15 Steps, FIRST COMPLETE REPORT)

- **First complete 15-step report produced.** 32,100 chars, 6 sections (Executive Summary, Key Findings, Novel Insights, Recommendations, Knowledge Gaps, References). Same query as S6 ("LLM/SLM architecture research in 2026") but shallow depth. All synthesis phases completed: outline generation, 6 section drafts (28,503 chars), cross-reference (8 contradictions flagged).
- **Shallow mode 15-step total time: ~48 min** (20 min execute + 28 min synthesis). Deep mode (S6) spent 57 min on execute alone and never produced a report. Synthesis is ~58% of total runtime — Phase B (6 Bedrock generate calls) is the bottleneck.
- **NDJSON fix validated in production:** 100% parse rate (434/434 valid JSON records). Was 28% in S6. Orchestrator events now appear: 34 records captured (was 0 in S6). Both logging fixes confirmed working.
- **`_step_sources` has NEVER worked across 7 sessions.** `_extract_sources()` parses SearchAgent's `final_message` for JSON with `sources` key — agent doesn't emit that format. All 15 steps show 0 sources in checkpoint. Synthesis falls back to `_analyst_output.claims` which contains source references. Low impact but technically broken data path.
- **Step summary truncation at 800 chars.** 14/15 summaries hit the 800-char cap exactly. May be losing synthesis context. Worth investigating if raising limit improves report quality.
- **Outline parse succeeded (first time since S2).** No fallback to default 5-section outline. Either the hardening fix or model behavior randomness. Non-deterministic.
- **New error type: tool name confusion.** Model called `arxiv.org/abs/2502.06807` as a tool name instead of passing URL to `scrape_url`. Pipeline recovered. Single occurrence.
- **Step 14 (gap-fill) produced 0 claims.** Analyst correctly identified insufficient sources rather than fabricating. Suggests gap-fill mechanism needs prompt tuning or alternative source strategy.
- **Cross-session report completion: 2/7.** S2 (shallow, simple) and S7 (shallow, complex). No deep-mode session has ever produced a report. Synthesis durability remains the constraint for deep mode.

## 2026-03-22 (Logging + Observability Fix — 3 issues)

- **Root cause of NDJSON corruption:** `%(message)r` in the log format string produces Python repr output (single-quoted strings) — NOT valid JSON. `json.loads` fails on ~72% of records. Only records whose messages happen to contain single quotes get double-quote outer delimiters from repr, accidentally producing valid JSON. Fixed with `_NdjsonFormatter` class using `json.dumps()`.
- **File handler was DEBUG-mode-only:** NDJSON file handler guarded by `debug_mode`, so production INFO-mode runs never wrote a file at all. Removed the guard; file handler now always enabled at INFO level. No more DEBUG noise from mcp_client in the file.
- **Outline parse hardened:** Previous code did `json.loads(outline_response.content)` — could receive None (TypeError) or empty string after code-fence stripping (JSONDecodeError). Fixed: `raw = outline_response.content or ""` + `if raw:` guard before json.loads. Code-fence stripping was already present.
- **Orchestrator events added at INFO level:** step_start, step_complete, research_complete, QA convergence/budget decisions now emit `logger.info()` at key pipeline milestones. Previously these were only yielded as ResponseMessage WebSocket events. Now they're captured by the NDJSON file handler for post-run analysis.
- **smoke_test.py now at 40 tests** (was 37): Added `test_ndjson_formatter_valid_json`, `test_outline_parse_handles_empty_content`, `test_orchestrator_step_level_logging`.

## 2026-03-22 (Log Analysis — Session fb6f2833, Deep Mode, 15 Steps)

- **Largest plan executed to date:** 15-step deep-mode plan on "LLM/SLM architecture design in 2026" using Bedrock (`claude-sonnet-4-6` / `claude-haiku-4-5`). All 15 steps completed. 454 tool calls (309 search, 142 scrape, 3 file). ~57 min execution wall-clock.
- **Report killed by Docker restart — 3rd time in 6 sessions.** Synthesis started at 14:55:30 (outline + Phase B drafting), Docker container restarted at 14:55:35 — **5 second window**. Appears to be a `make restart-all` command issued while synthesis was running. Session state frozen at `executing`.
- **Session persistence P0 fix validated but insufficient.** Checkpoint was successfully written to `logs/sessions/fb6f2833-...json` with full synthesis state (15 step summaries, 15 source maps, 182 contradictions). However, recovery requires a client to send `resume` — user stepped away and never reconnected. Need auto-recovery on server restart without client trigger.
- **97% scrape success rate** — best ever (138/142). Previous best was 75% (S3). Academic/tech queries target open sources. Only 4 failures: openai.com (2), lovechip.com, marktechpost.com.
- **New error type: Bedrock content filter block** at 14:08:16. One model generation rejected. Pipeline recovered — all 15 steps completed despite it.
- **Outline parse failure recurred** (3rd occurrence: S2, S5, S6). Still not fixed in `orchestrator.py`. Falls back to generic 5-section outline. The code-fence stripping fix works in `optimization.py` but was never applied to the outline generation path.
- **NDJSON logging fundamentally broken:** Only 28% of 4,767 lines parsed as valid JSON. Scraped content with embedded newlines destroys the format. Orchestrator events go only to in-memory ring buffer, not persisted. Critical for post-run analysis.
- **182 contradictions across 15 steps** (~12/step avg). Highest QA activity observed. All convergence checks passed.
- **Deep-mode runtime scales linearly with steps:** 15 steps = ~57 min vs. 10 steps = ~24.5 min. Updated estimate: ~3.8 min/step for deep mode.
- **Cross-session report completion: 1/6.** Only S2 (shallow, tutorial query) produced a complete report. S1/S3/S5/S6 killed during synthesis. S4 failed at execution (GCP bug). Synthesis durability is the existential issue.
- **Zero code-level bugs** — third consecutive session with no internal errors.

## 2026-03-21 (Session Persistence — P0 Fix)

- **Root cause:** `session_store.py` was purely in-memory; Docker restart during the ~1–3 min synthesis window destroyed all findings. 2 of 5 sessions lost reports this way.
- **Fix:** Three-file change — no new dependencies, no new infra.
  - `session_store.py`: Added `persist()` and `load_checkpoint()` methods. Sessions directory: `logs/sessions/{session_id}.json` (respects `LOG_DIR` env var, same pattern as `observability.py`). Atomic write via `.tmp` rename. `SessionStore.__init__` now accepts optional `sessions_dir` param.
  - `orchestrator.py`: Added `synthesis_checkpoint()` (serializes `_analyst_output`, `_step_summaries`, `_step_sources`, `_contradictions`, query) and `restore_synthesis_state(checkpoint)` (reconstructs those fields from dict). RAG store chunks are NOT persisted — synthesis falls back to `_analyst_output` if the store is empty.
  - `api_server.py`: (1) `_run_session()` writes synthesis checkpoint after `execute()` completes, before `synthesize()` starts — the critical window. Also writes final state in `finally`. (2) `_recover_session_from_checkpoint()` new module-level async function: loads checkpoint, creates fresh orchestrator, restores synthesis state, re-runs `synthesize()` as a tracked background job. (3) `resume` handler: tries `load_checkpoint()` before returning "session not found".
- **Recovery flow on reconnect:** client sends `resume` → backend hits in-memory miss → loads disk checkpoint → if `state == "executing"` + `synthesis_checkpoint` present + no `report` event in replay_log → re-runs synthesis → client receives full replay + streaming synthesis. If state is "complete"/"error" → replay-only, no synthesis re-run.
- **3 new smoke tests added** (37 total): `test_session_store_persistence`, `test_orchestrator_synthesis_checkpoint`, `test_api_server_recovery_function`. All 37 pass.
- **Disk file lifecycle:** Written at `research_complete` (checkpoint), overwritten at session end (final state). Files NOT auto-deleted after completion — low-priority maintenance concern.

## 2026-03-21 (Log Analysis — Session 82608f76, Deep Mode)

- **First deep-mode run.** Query: Iran conflict continuation, deep depth. Models: `claude-sonnet-4-6` (heavy) / `claude-haiku-4-5` (light) on Bedrock. **Pipeline completed successfully (24.5 min), but report lost to Docker restart during synthesis.**
- **Zero code-level bugs** — first session with no internal errors. All fixes from sessions 1–4 hold: NoneType scrape, dict-slice, analyst_notes type, GCP backend. Scrape 401/403 errors (31) are all external — paywalled news sites.
- **Convergence check validated at scale:** 10 retries used out of 27 possible (63% reduction). Every step except step 8 triggered exactly 1 retry then broke on convergence. Step 10 used 2 retries (contradiction count increased 8→9→11). Source disagreement reclassification working — step 8 had 8 contradictions but 0 actionable.
- **Tool volume:** 219 web_search + 72 scrape_url + 1 list_files = 292 total. Scrape success rate 57% (41/72) — lower than session 3 (75%) because geopolitical queries target more paywalled sources.
- **Docker restart killed synthesis:** `research_complete` at 21:09:21, outline parse failed at 21:10:24, server restart at 21:10:37 (~76s gap). User's computer slept → Docker killed container. In-memory session store lost all findings.
- **P0 issue confirmed: session persistence.** 2 of 5 sessions lost reports to mid-synthesis interruptions. `session_store.py` must persist to disk or ChromaDB.
- **Outline parse failure recurred** (same as session 2) — LLM returns JSON wrapped in markdown code fences. Need same code-fence-stripping fix as `optimization.py`.
- **Deep mode runtime estimate update:** Actual 24.5 min vs. estimated 60–90 min. Convergence check invalidates the pre-check extrapolation. Updated estimate: 20–35 min for 10-step plans.
- **Error trend (normalized):** S1: 384 scrape errors (NoneType+403), S2: ~10, S3: 10, S4: 0, S5: 31 (all 403, 0 code bugs). Code reliability is now stable; remaining errors are purely from external site access restrictions.
- **Cross-session report completion: 1/5** — only session 2 (shallow, LLM/SLM topic) produced a complete report. Sessions 1, 3, 5 killed during synthesis. Session 4 never reached synthesis (GCP backend failure). Synthesis durability is the critical path.

## 2026-03-21 (Self-Optimization Run #2 — Analysis)

- **Second self-optimization run produced 14 changes** to RESEARCH-METHODS.md (+110 lines). All grounded in observed failures from sessions 1–4. No content removed.
- **High-confidence wins:** Agent Temporal Anchoring Rule (fixes Gemini refusal P0), Gap-Triggered Re-search Obligation (fixes silent gap-skipping), Coverage Notes Convention (formalizes analyst output format), Zero-Result Escalation extended to agent refusals, Minimum Completeness Threshold for memory storage.
- **Aspirational but needs code changes:** Importance Scoring Rubric (code uses fixed `importance=7`), Authoritative URL Pinning (code doesn't detect canonical URLs), Partial Session Recovery (orchestrator doesn't implement the 3-consecutive-failure halt).
- **Prompt size creep risk:** Methods doc grew from ~190→~300 lines (~6K tokens). Injected into every planning prompt unconditionally. After 3–4 more optimization runs, may start diluting query context. Consider relevance-filtered injection or a hard line cap.
- **`_develop_new_research_methods()` JSON parse failed** — LLM returned JSON wrapped in markdown code fences (`` ```json ``` ``). `json.loads()` choked. The `new_methods` list was empty; all 14 changes came from the free-form "generate updated doc" phase compensating. Fix: strip code fences before parsing. One-line fix in `optimization.py`.
- **Domain-specific methods (Governance Maturity Check, Concentration Check) are positive but narrow** — useful for protocol/ecosystem research, noise for other query types. Currently injected into all plans unconditionally.
- **Prediction for next Bedrock run:** slightly better coverage completeness, marginal increase in step count (10–12 vs 9–10), ~10–15% longer wall-clock. No effect on error rate (those are code-level fixes).
- **Prediction for next Gemini run:** 50/50 odds Gemini still refuses sensitive current-events queries — refusal appears to be model-level safety, not prompt-sensitivity. Methods doc adds a second reinforcement layer on top of the hardcoded system prompt fix.

## 2026-03-21 (GCP Backend Fixes + Config Model Selection)

- **`GCPVertexAIBackend.generate()` fixed** (`model_backend.py`): Now converts raw `ChatCompletionMessageFunctionToolCall` SDK objects to internal `ToolCall(name=tc.function.name, parameters=json.loads(tc.function.arguments), ...)` — same pattern as BedrockBackend. Also fixed the early-return on empty content: now checks `not content and not raw_tool_calls` so tool-only responses (content=None) are not silently dropped.
- **Error handler double-fault fixed** (`orchestrator.py` `_run_search`): Error log now uses `getattr(tool_call, "name", "<unknown>")` instead of `tool_call.name`. Previously if the AttributeError was on `.name`, the error handler itself re-raised a second AttributeError.
- **`_select_model()` now reads from `config.*` fields** (`orchestrator.py`): All hardcoded model strings removed. Reads `config.{backend}_heavy_model` / `config.{backend}_light_model`. Same logic preserved (heavy-or-fallback → heavy model; explicitly-light → light model).
- **Heavy/light model config fields added** (`config.py`): Added `openai_heavy_model`/`openai_light_model`, `azure_heavy_model`/`azure_light_model`, `aws_heavy_model`/`aws_light_model`, `gcp_heavy_model`/`gcp_light_model`, `ollama_heavy_model`/`ollama_light_model`. All respect env vars: `{BACKEND}_HEAVY_MODEL` / `{BACKEND}_LIGHT_MODEL`. Defaults match previous hardcoded values. Existing `*_model` fields kept for backward compat.
- **SearchAgent system prompt updated** (`orchestrator.py`): Added a rule at the top of the Rules section: "You have real-time web search and scraping tools — use them. Never refuse based on training cutoff date." Intended to fix Gemini's refusal pattern where it classified current-events queries as "future/hypothetical".
- **`openai._base_client` and `openai.resources` suppressed to WARNING** (`observability.py`): These two loggers were generating 68% of log volume (1,075/1,583 records) at DEBUG level. Adding them to the silenced-libs list is the minimal fix.
- **5 new smoke tests added** (`smoke_test.py`): `test_gcp_backend_converts_tool_calls`, `test_error_handler_no_double_fault`, `test_config_heavy_light_model_fields`, `test_select_model_reads_config`, `test_search_agent_prompt_tool_use_instruction`. All 34 tests pass.

## 2026-03-21 (Log Analysis — Session 88953a2b, GCP Vertex AI)

- **First GCP Vertex AI run.** Query: same Iran conflict continuation, shallow depth. Models: `gemini-2.5-pro` (heavy) / `gemini-2.5-flash` (light) via `_select_model()`. **Total research failure — zero useful data retrieved.**
- **P0 Bug: `GCPVertexAIBackend.generate()` returns raw SDK tool call objects.** Unlike Bedrock (which converts to `ToolCall(name=tc.function.name, ...)`), the GCP backend passes `response.choices[0].message.tool_calls` through unmodified. These are `ChatCompletionMessageFunctionToolCall` objects with `.function.name` / `.function.arguments`, not `.name` / `.parameters`. Causes `AttributeError` at orchestrator line 2362 (tool call logging) and 2422 (error handler — double fault). **Steps 1 and 3 failed.** Fix: add same `ToolCall()` conversion as Bedrock.
- **P0 Behavioral: Gemini 2.5 Flash refuses current-events queries.** All 5 analyst outputs were meta-refusals ("hypothetical future scenario", "unable to research fictional events"). The model classified "Iran conflict in 2026" as speculative/future despite it being a real current event. Claude Sonnet on the identical query produced 200+ real claims with sources. Likely a combination of training cutoff interpretation + geopolitical topic sensitivity in Gemini's safety layer.
- **Error handler double-fault:** `_run_search` except block at line 2422 does `tool_call.name` in the error log message — if the original error was `.name` not existing, the error handler itself raises a second `AttributeError`. Needs `getattr(tool_call, 'name', '<unknown>')`.
- **Config gap confirmed:** `config.py` has single `gcp_model` field but `_select_model()` hardcodes `gemini-2.5-pro`/`gemini-2.5-flash`. Same hardcoding pattern for all backends. Need `{backend}_heavy_model` / `{backend}_light_model` env vars per backend so users can override via config.
- **Positive: WebSocket stability confirmed** — 0 disconnects (was 3 in 171108ba). The 150ms delay fix is working. Clean shutdown (0 crash errors). All 3 MCP servers connected. Structured analyst_notes working. NoneType scrape bug still eliminated.
- **Error trend: 105 → 37 → 14 → 2.** Remaining issues are integration/model-level, not core pipeline bugs.
- **`openai._base_client` logger noise:** 1,075 of 1,583 records (68%) from SDK HTTP client debug logging. Consider setting to WARNING.

## 2026-03-21 (P1 Fixes from Session 171108ba Analysis)

- **WebSocket resume timing fix:** `useWebSocket.ts` `onopen` handler now delays 150ms before calling `onReconnectedRef.current?.()` on reconnects. Prevents `session=none` race condition where `resume` message was sent before the backend WS handler was ready to process it.
- **`synthesis_progress` event added:** Backend (`orchestrator.py`) now emits `type="synthesis_progress"` with `{section_index, section_title, total_sections}` data before each section draft in Phase B, replacing the old `status` message. Frontend (`App.tsx`) handles `synthesis_progress`: updates `setCurrentStatus` to show "Drafting section N of M: Title…" and re-enables `isResearching(true)` on section 1 so the spinner reappears during synthesis (between `research_complete` and `report`). The `report` event continues to call `setIsResearching(false)`.
- **Why `isResearching(true)` on synthesis_progress section 1:** After `research_complete`, spinner disappears. Synthesis can take 2–3 min. Setting `isResearching(true)` re-shows the progress indicator. The ResearchControlBar also re-appears (pause/stop are harmless during synthesis — the pipeline is done but the WS session is still open).

## 2026-03-21 (Log Analysis — Session 171108ba, Shallow Depth)

- **Second shallow run; first after `str(notes)[:300]` fix and web scraper NoneType fix.** Query: "Research the current ongoing conflict in Iran", shallow depth. **~16.6 min wall-clock** (longer than 6ce9428a's ~12 min due to heavier geopolitical query + 6-min cross-reference step).
- **Both P0 bugs from analytics/2 CONFIRMED FIXED:** (1) `_generate_step_summary` dict-slice → 0 step failures. (2) NoneType scrape bug → 0 occurrences (was 28). Total errors: 14 (down from 37).
- **Frontend bug discovered — `analyst_notes.slice` TypeError:** `GraphView.tsx:385` calls `.slice()` on `analyst_notes` assuming string, but backend sends dict. Caused React crash → WebSocket disconnect. **FIXED:** Added `typeof` check + `JSON.stringify` fallback in GraphView and updated StepResultViewer type.
- **WebSocket disconnect/reconnect: 3 cycles (13:45:08–13:45:58).** First two reconnects failed to establish session (`session=none`). Third succeeded after ~37s total backoff. Resume protocol has a timing issue — `resume` message may fire before backend is ready. Recommendation: add 100–200ms delay after `open` or implement `resume_ack`.
- **Server killed mid-synthesis:** Only 2/5 report sections drafted (Executive Summary + Key Findings). `"type": "report"` event never emitted. Synthesis was still actively generating when the server was manually shut down at 13:51:02. Not a code bug — operational issue.
- **HTTP 403 is now the dominant scrape failure mode** (10 occurrences, all news/institutional sites: Britannica, NYT, France24, Reuters, BMJ, EU Council, UK Parliament). Query-topic-dependent — geopolitical queries target more gated sources.
- **Error trend: 105 → 37 → 14** across 3 sessions. Core reliability bugs are resolved. Remaining errors are external (HTTP 403s) and cosmetic (shutdown crash).
- **Analyst quality for geopolitical queries is exceptional:** Step 8 (cross-reference) produced a 4-tier source reliability assessment, 30% completeness score, explicit single-source flagging, and conditional prediction models based on unresolved key claims (Khamenei death confirmation, JCPOA status).
- **Shallow depth wall-clock stabilizing at 12–17 min** depending on query complexity. Geopolitical queries run longer due to more search/scrape activity per step.

## 2026-03-20 (Log Analysis — Session 6ce9428a, Shallow Depth)

- **First full run after QA loop rework + GraphRAG + research depth.** Query: "LLM/SLM architecture research 2025–2026", shallow depth (QA loop skipped). **~12 min wall-clock vs ~98 min for previous moderate-depth run — 88% reduction.**
- **`_generate_step_summary` dict-slice bug:** `orchestrator.py:3073` does `notes[:300]` but `notes` is now a dict (structured analyst notes). Raises `KeyError: slice(None, 300, None)`. Caused step 1 to fail. Fix: `str(notes)[:300]`.
- **NoneType scrape bug is proportionally WORSE:** 28 errors / 45 calls = 62% failure rate (vs. previous 81/166 = 49%). Absolute count is down because of fewer scrape calls in shallow mode, but the underlying bug is hit more frequently.
- **GraphRAG extraction working:** 146 entities, 151 relationships across 9 completed steps. Entity counts correlate with claim complexity.
- **LTM claim persistence working:** 77 claims + 5 RAG chunks persisted to ChromaDB.
- **`paper_server` MCP not running:** Configured but connection fails. Analyst flagged "no primary research papers — only survey abstracts" as critical gap. Paper server absence is a likely contributor.
- **Outline parse failure during synthesis:** JSON parse returned empty, fell back to defaults. Single occurrence — may be transient LLM formatting issue.
- **Shallow depth produced 138 claims, 37 tensions across 10 steps.** Comparable analytical quality to the previous moderate run that spent 3x compute on non-converging QA retries.
- **Runtime estimate update:** shallow depth = ~12–15 min for 10-step plans. Previous estimate of 15–25 min was conservative.

## 2026-03-20 (Benchmark Runtime Estimates — v1, from log analysis)

**Context:** Derived from `backend/logs/backend.ndjson` (2026-03-20 session). Single query "MCP adoption 2026", model=`claude-sonnet-4-6` on AWS Bedrock, moderate depth. Query started 19:49:59, server killed at 20:47:26 (~57 min, no `report` event seen — still running). Prior session `fed60e8d` at moderate depth ran ~98 min total. Both data points suggest moderate depth tasks run 55–100 min.

**Per-task wall-clock estimates (baseline v1):**

| Depth | QA retries | Estimate | Basis |
|---|---|---|---|
| `shallow` | 0 (QA skipped) | 15–25 min | Extrapolated: ~38% of moderate (no QA loop, no LoopAgent calls) |
| `moderate` | ≤2 | 40–70 min | Log evidence: 57 min still running, prior ~98 min (pre-convergence-fix) |
| `deep` | ≤3 + enhanced prompts | 60–90 min | Extrapolated from moderate + ~30% overhead |

**Full bench (100 tasks) projections at `--concurrency 3`:**

| Depth | Estimate |
|---|---|
| `shallow` | ~8–10 hours |
| `moderate` | ~20–25 hours |
| `deep` | ~35–45 hours |

**Dominant bottleneck:** AWS Bedrock Sonnet latency (P90=48.3s, max=125.9s from prior session). Not Python/async overhead.

**Key caveat:** No completed `shallow` run observed yet. The 15–25 min estimate is extrapolated from moderate — update once a shallow run with report event is logged.

**Script default set to:** `--depth shallow --concurrency 3` (as of 2026-03-20).

---

## 2026-03-20 (Log Analysis — Pipeline Bottleneck Discovery)

- **QA loop never converges for research topics with inherent nuance.** Session `fed60e8d` (10-step "MCP adoption 2026"): 8/9 active steps exhausted 2/2 retry budget, contradiction counts *increased* across retries (e.g. step 2: 5→6→8). The LoopAgent correctly identifies real source disagreements, but the loop mechanism converts this into wasted compute. Estimated ~36 extra Sonnet + ~54 extra Haiku calls (~3x per step) with zero measurable quality improvement.
- **`scrape_url` NoneType bug**: 303 errors from `'NoneType' object has no attribute 'get'` in `mcp/web_scrape/main.py`. BS4 processing pipeline has an unguarded `.get()` on a None return from `find()`. The generic `except Exception` catches it but strips the URL — can't identify which pages trigger it. Need URL in error message + defensive null-checks.
- **Scrape error distribution**: 303 NoneType, 41 Request Error, 19x 403, 10x 429, 8x 404, 3 connection failures = 384 total failures across session.
- **Sonnet latency is severe**: P90=48.3s, max=125.9s (>2 min). 21 of 87 Sonnet calls exceeded 30s. Total LLM wait time ~88 min out of ~98 min wall clock.
- **Analyst self-awareness is high quality**: AnalystAgent correctly identified source quality gradient, corroboration sparsity (only 7/32 claims verified by 2+ sources), temporal contamination (2025 HN source cited as 2026 evidence), and the "enthusiasm/adoption gap" as the most analytically significant pattern. These notes are embedded in the QA audit payloads (logged as DEBUG messages in the LLM request bodies).
- **`_analyst_recommendations` feedback loop may not be working**: Analyst explicitly recommended "targeted searches for OpenAI tool-calling vs MCP technical comparisons" in `coverage_notes`, but subsequent search queries didn't incorporate these. Need to audit how `extra_context` is forwarded to `_run_search()`.
- **RAG growth**: 34,079 chunks in one session (381 add ops, avg 89/step, max 217/step). Needs pruning/limits.
- **Convergence check needed**: If contradiction count doesn't decrease between QA retries, should break immediately (like the existing data-poverty check for chunk count).

## 2026-03-20 (QA Loop Rework — Convergence + Type-Based Routing)

- **`Contradiction` dataclass now has `contradiction_type`** field: `factual_error`, `temporal_mismatch`, `source_disagreement`, `insufficient_evidence`, `unknown`. Added to `to_dict()`.
- **LoopAgent classifies every contradiction by type.** Only `factual_error` / `temporal_mismatch` / `unknown` are actionable (trigger targeted re-search).
- **`source_disagreement` contradictions are injected as tensions** directly into `analyst_result` (topic/source_a/position_a/source_b/position_b schema). No re-search triggered. Propagates to ReportComposer as synthesis input — turns a bug into a feature.
- **`insufficient_evidence` contradictions become analyst recommendations** forwarded to subsequent steps' SearchAgent (`_analyst_recommendations`).
- **Convergence check added:** `prev_actionable_count` (None on first pass, skips check on first retry). If `len(actionable) >= prev_actionable_count` fires, breaks immediately with "not converging" message. Fixes the 5→6→8 explosion pattern from session `fed60e8d`.
- **Loop exit order:** clean break → type split + inject source_disagreements → no-actionable break → convergence check → budget check → increment → re-search → re-analyst → merge → continue.
- **Default `research_depth` changed from `"moderate"` to `"shallow"`** in 4 places: `Orchestrator.__init__`, `_make_orchestrator()` in `api_server.py`, WS handler fallback, `App.tsx` useState.
- All 29 smoke tests pass.

## 2026-03-20 (Research Depth Setting — UI + Backend)

- Added `research_depth` parameter to `Orchestrator.__init__` (replaces the old `max_qa_retries` parameter). `max_qa_retries` is now derived from depth: shallow=0, moderate=2, deep=3.
- **shallow**: Phase 3 (LoopAgent QA loop) is skipped entirely; pipeline is Search → Analyst only. `qa_retry_count` initialized to 0 before the if/else so `record_event` always has a valid value.
- **moderate**: standard behaviour (Search → Analyst → QA, up to 2 retries).
- **deep**: injected depth-hint prompts into `_run_search` (specific queries, primary sources, multiple independent sources per claim) and `_run_analyst` (require 2+ sources per claim, surface ALL tensions). QA up to 3 retries.
- `api_server.py`: `_make_orchestrator()` now accepts `research_depth` kwarg; `query` handler extracts `raw.get("research_depth", "moderate")` and validates against the allowed set before passing to orchestrator.
- Frontend: `ResearchDepth` type exported from `Sidebar.tsx`. Settings tab replaced `<select>` dropdowns with a 3-button toggle (Shallow/Moderate/Deep) and removed the "Source Verification" dropdown entirely (source verification is already inherent in the Search→Analyst→QA pipeline). `researchDepth` state lives in `App.tsx` (default `"moderate"`), passed into `Sidebar` via props, and included in the WebSocket `query` message as `research_depth`.
- **Important**: When the QA loop was refactored to an `if/else`, the code between the while loop's supplemental search and the final `_store_analyst_findings` call (data-poverty check, re-analyst extraction, merge) had to be re-indented from 16→20 spaces to stay inside the while body. Easy to regress if copy-pasting.

## 2026-03-20 (Self-Optimize → Knowledge Graph Integration)

- `SelfOptimizingAgent.get_all_memories()` now queries the knowledge graph via `graph.recall_graph_context()` (entity_limit=20, max_hops=2) in addition to the flat `recall(limit=100)`. Graph context returned under `"knowledge_graph"` key; stats under `"graph_stats"`.
- `_analyze_memories()` prompt updated to instruct the LLM to analyze entity clusters, relationship patterns, and thematic clusters from the graph context.
- **Dead code fix:** Phase 5 persistence (`self_optimize`) was still calling `self.mcp_servers.get("memory")` — the removed MCP memory server. Replaced with `self._long_term_memory.store()` using category `"optimization_insight"`, importance 8.

## 2026-03-20 (GraphRAG — Knowledge Graph for Long-Term Memory)

**Architecture:** Added `KnowledgeGraph` class to `long_term_memory.py` — three additional ChromaDB collections (entities, relationships, communities) layered on top of the existing flat memory store. No new external dependencies.

- `KnowledgeGraph` is a nested object on `AsyncLongTermMemory` (accessible as `ltm.graph`). Collections created during `_init_sync()` alongside existing flat collections.
- **Entities** (`_kg_entities`): name + description embedded. Deduplicated by semantic similarity (≥0.92 threshold). Mention count and session tracking on merge.
- **Relationships** (`_kg_relationships`): directed triples `source → relation → target` with evidence text, confidence score, session/step provenance.
- **Communities** (`_kg_communities`): LLM-generated cluster summaries. Detected by relationship co-occurrence (greedy BFS, ≥2 shared connections). Updated post-synthesis via `update_communities(summarize_fn)` — the orchestrator provides the LLM callable.
- **Extraction**: `Orchestrator._extract_graph_triples()` — one LLM call per analyst step (after QA pass). Parses structured JSON from the LLM to extract entities and relationships from vetted claims only.
- **Recall**: `Orchestrator._recall_memories()` now combines two sources: (1) `graph.recall_graph_context()` for entity/relationship/community context, (2) `find_similar()` for flat vector search. Both are injected into the planning prompt.
- **Post-synthesis**: `synthesize()` now calls `graph.update_communities(_summarize)` after persisting RAG chunks to LTM.

**`_NoOpEmbeddingFunction` fix for ChromaDB 1.5.2:**

- ChromaDB 1.5 calls `.name()` as a method (was a class attribute before). Changed to `def name(self) -> str`.
- `__call__` now returns zero-vectors instead of raising RuntimeError. When `EMBEDDINGS_ENABLED=false`, ChromaDB invokes the EF for documents without explicit vectors — raising blocked all storage. Zero-vectors allow storage to succeed; cosine similarity is meaningless but metadata queries work.

**Smoke tests:** 7 new tests (5 AST-based structural + 2 async integration). All 29 tests pass with both `EMBEDDINGS_ENABLED=true` and `false`.

**Python 3.12 environment:** User updated local venv to Python 3.12. ChromaDB integration tests now execute instead of skipping. AST-based Python 3.14 workarounds are no longer needed locally but remain valid structural checks.

## 2026-03-19 (File Upload → File Handler MCP + Orchestrator Plan Context)

**Architecture decision:** Frontend uploads files directly to `mcp/file_handler` (port 9191) via custom HTTP routes, not through the backend API. Keeps file I/O isolated to the container with the data mount.

- `mcp/file_handler/main.py`: added 3 `@mcp.custom_route` endpoints: `GET /files`, `POST /files/upload`, `DELETE /files/{filename}`. Path sanitized with `os.path.basename()` in both write handlers to prevent directory traversal.
- `python-multipart` added to file handler `requirements.txt` — required for Starlette `request.form()`.
- Both `src/nginx.conf` and `src/vite.config.ts` proxy `/files/*` to the file handler (`host.docker.internal:9191` / `localhost:9191`). No CORS complexity needed — same-origin via proxy in both dev and prod.
- `src/api/client.ts`: `uploadFile`, `listFiles`, `deleteFile` now use relative paths (`/files/upload`, `/files`, `/files/{name}`) so the proxy handles routing transparently.
- `src/components/FileUploader.tsx`: `handleRemoveFile(fileId, fileName)` — `fileName` sent to API (server identity), `fileId` used for React state filtering (keeps random ID as React key).
- Old `POST /files/upload` on backend `api_server.py` removed; `UploadFile`/`File` FastAPI imports also removed.
- `Orchestrator._gather_uploaded_file_context(query)`: calls `list_files` then `read_file` via `mcp_registry` for each file (cap: 5 files, 10 KB/file). MCP returns `[{"type":"text","text":"<json>"}]` — extract `result[0]["text"]` + `json.loads()`. Injected into planning prompt before `prior_memories`. Returns `""` if handler unavailable or no files — never raises.
- `Orchestrator.plan()` emits optional status event "reading uploaded reference files…" only when files are found.

**Pattern to remember:** MCP tool call results from `mcp_registry.call_tool()` always come back as `list[{"type":"text","text":"<serialized value>"}]`. For tools returning lists/dicts, `json.loads(result[0]["text"])`. For tools returning strings, `result[0]["text"]` directly.

## 2026-03-19 (Web Scraper Content Quality — 4 improvements)

`mcp/web_scrape/main.py` was feeding raw binary (PDFs), boilerplate (nav/footer/ads), and unstructured noise into agent context. Four targeted fixes:

1. **Content-Type gate**: `_is_textual_content()` checks response Content-Type against `_TEXTUAL_CONTENT_TYPES` allowlist before parsing. PDFs, images, video, `application/octet-stream` all return a `[SKIPPED]` message instead of binary soup. This is the highest-impact fix — prevents the PDF blob problem entirely.
2. **Boilerplate removal**: `_remove_boilerplate()` strips `nav`, `header`, `footer`, `aside`, `noscript`, `script`, `style` tags + elements whose class/id matches `_BOILERPLATE_PATTERNS` regex (cookie banners, ads, social share, newsletter signup, comments, breadcrumbs, pagination, etc.).
3. **Main content extraction**: `_extract_main_content()` tries `<article>`, `<main>`, `role=main`, `id~content|article|post|entry` before falling back to largest `<div>`/`<section>` by text length. Only used if the candidate has >200 chars (semantic) or >300 chars (heuristic). Falls back to full page if nothing qualifies.
4. **Structured metadata**: `_extract_metadata()` pulls `<title>`, meta description (with og:description fallback), author, and published date into the response header. Agents get structured context without parsing the body.

**Tests moved**: web_scrape tests live in `mcp/web_scrape/tests.py` (10 tests), not in `backend/smoke_test.py`. Backend smoke tests are exclusively for backend modules.

## 2026-03-19 (Self-Optimize Pipeline Fix — 2 root causes)

Log analysis of a self-optimize session revealed the RESEARCH-METHODS.md was never updated despite the workflow completing.

**Root cause 1: `get_all_memories()` used the removed memory MCP server.**
`SelfOptimizingAgent.get_all_memories()` called `self.mcp_servers.get("memory")`, which has been `None` ever since the standalone memory MCP server was replaced by in-process `AsyncLongTermMemory`. Always returned `{}`, so all three LLM phases (analyze, develop, update) ran on empty input and produced a no-op diff.

**Root cause 2: `backend/instructions/` was not bind-mounted.**
`_write_research_methods()` successfully wrote to `/app/instructions/RESEARCH-METHODS.md` *inside the container*, but the path was never volume-mounted so the file was invisible on the host and discarded on restart.

**Fixes:**

- `optimization.py`: `SelfOptimizingAgent.__init__` now accepts `Optional[AsyncLongTermMemory]`, stored as `self._long_term_memory`. `get_all_memories()` calls `self._long_term_memory.recall(limit=100)` directly.
- `api_server.py`: both `SelfOptimizingAgent(...)` call sites pass `long_term_memory=long_term_memory`.
- `docker-compose.yml` + `docker-compose-full.yml`: added `./backend/instructions:/app/instructions` volume mount to backend service.
- `smoke_test.py`: 2 new AST-based tests (`test_self_optimize_uses_long_term_memory`, `test_self_optimize_api_server_wiring`). AST inspection used (not runtime import) to avoid the Python 3.14 + chromadb pydantic v1 compatibility issue.

**Pattern to remember:** whenever a dependency is removed (MCP server, external API), grep for all callers — stale call sites that silently return empty values are the hardest bugs to spot from logs.

---

## 2026-03-19 (RAG Inter-Agent Data Flow Fixes — 7 improvements)

Implemented all 7 fixes from deep log analysis of session dbfd4629 ("MCP adoption in 2026").

### Changes Made

**`backend/long_term_memory.py`** — Fix LTM ChromaDB init failure

- Replaced broken `@property def __class__` override with plain `name: str = "no-op"` class attribute on `_NoOpEmbeddingFunction`. ChromaDB introspects `.name` directly; the property override triggered AttributeError. This was causing `_available=False` for every session on Python 3.14 (still works on 3.12 in Docker, but good to fix regardless).

**`backend/search_result_store.py`** — Binary content rejection + `exclude_step_id`

- Added `_is_binary(text, sample_size=512)` static method: counts non-printable chars (ord < 32, except \n\r\t). >10% ratio → binary. Prevents PDF blobs from being stored as chunks.
- Refactored `_extract_text()` with inner `_clean()` helper that checks binary for ALL code paths (list blocks, dict values, bare strings).
- Added `exclude_step_id: Optional[int]` parameter to `retrieve()`. Used by `_run_analyst` to fetch cross-step corroboration context (chunks from other steps) without including the current step's own chunks.

**`backend/orchestrator.py`** — 5 independent improvements

1. **Coverage notes pollution (Fix 1)**: `_run_analyst()` fallback path now extracts only `sources[*].knowledge_snippet` from the SearchAgent's `final_message` JSON instead of JSON-dumping the whole `search_output` dict. Explicitly excludes `coverage_notes` and `_tools_used` from the stripped fallback path.
2. **Cross-step corroboration (Fix 2)**: `_run_analyst()` now issues a second `retrieve()` call with `exclude_step_id=step.id` (no step_id_filter) to fetch top-3 chunks from OTHER completed steps. Injected as "Corroborating evidence from prior steps" section in the analyst prompt.
3. **Numbered source attribution (Fix 3)**: RAG chunks in the analyst prompt are now numbered `[Source 1]`, `[Source 2]`, etc., with the analyst instructed to cite using `[Source N]` notation in claims.
4. **Analyst recommendations feedback loop (Fix 4)**: Added `_analyst_recommendations: List[Dict]` to `__init__` and `_reset_state`. After each step completes, parses `analyst_notes` for "suggest/recommend/search for" lines and appends to `_analyst_recommendations`. The last 3 recommendations are forwarded to each subsequent `_run_search()` call as `extra_context`.
5. **QA data poverty early exit (Fix 5)**: After targeted re-search, `chunk_count(include_superseded=False)` is compared before/after. If no new chunks were added, the QA loop breaks immediately instead of burning the second retry on equally poor source material.

**`backend/smoke_test.py`** — 7 new tests added

- `test_ltm_noop_ef_has_name`: AST-based (avoids chromadb import on Python 3.14) — verifies `name` attr added and `__class__` property removed.
- `test_search_result_store_binary_detection`: tests dict, list, and string binary rejection + mixed-list clean passthrough.
- `test_search_result_store_exclude_step_id`: async test — verifies `exclude_step_id` filters and `step_id_filter` filters correctly.
- `test_orchestrator_analyst_recommendations`: verifies `_analyst_recommendations` attribute lifecycle.
- `test_run_analyst_strips_coverage_notes`: source inspection verifies `coverage_notes` and `_tools_used` are excluded in fallback.
- `test_run_analyst_cross_step_retrieval`: source inspection verifies `exclude_step_id` is present in `_run_analyst`.
- `test_qa_loop_data_poverty_exit`: source inspection verifies `chunks_before`/`chunks_after` comparison logic is present.

### Root Causes Addressed (from session dbfd4629 log analysis)

- All 10 steps hit max QA retries (qa_retries=2) due to data poverty and meta-commentary pollution
- ~40% of analyst context was `coverage_notes` / `coverage_notes`-style meta-commentary
- AnalystAgent was isolated to step-scoped chunks, preventing any cross-source corroboration
- `_NoOpEmbeddingFunction` AttributeError disabled LTM for entire sessions (cross-session bootstrap unavailable)
- PDF binary blobs from arxiv.org/pdf scrapes stored as chunks, wasting RAG slots
- Analyst recommendations written to logs but never fed back into subsequent search queries

- `orchestrator.py`: Added `record_event("analyst_step_complete", ...)` call after each step's QA loop, capturing `step_id`, `step_description`, `claim_count`, `tension_count`, `qa_retries`, `analyst_notes` (text), `claims` (list of {claim, confidence, source}), and `tensions` (list of topic strings).
- `orchestrator.py`: Added `session_id: Optional[str] = None` field to `Orchestrator.__init__`; passed as `session_id=` in `record_event` so events are filterable by session in the buffer.
- `api_server.py`: Set `orchestrator.session_id = session.session_id` immediately after `session_store.create(orchestrator)` to wire up the session ID.
- `observability.py` `log_dump()`: Added `analyst_steps` list that collects all `level == "EVENT"` + `message == "analyst_step_complete"` records, sorts them by `step_id`, and includes them as `summary.analyst_steps` in the dump JSON. This exposes per-step claim/tension counts, QA retry counts, and analyst notes text in every debug dump for pipeline diagnostics.
- Analyst step events only appear in dumps written in DEBUG mode (same gate as rest of `log_dump`).

## 2026-03-18 (Infra — Azure Container Apps Terraform updated for in-process LTM)

- `azurerm_container_app.mcp_memory` (port 9494) removed from `containers.tf` — no longer a deployed service.
- `chroma-data` Azure File Share now mounted on the **backend** container at `/app/chroma_data` (was on the memory MCP).
- Backend gains `CHROMA_PERSIST_DIR=/app/chroma_data` and `CHROMA_COLLECTION_NAME=agent_memories` env vars; `MEMORY_SERVER_URL` and `MEMORY_SERVER_API_KEY` env vars removed.
- `mcp_memory_url` local removed from `main.tf`; `mcp_memory` entry removed from `outputs.tf` `image_names` map.
- `container_env.tf` and `storage.tf` comments updated to reflect backend ownership of chroma volume.
- `infra/azure/README.md`: services table, image build commands, `az containerapp update` loop, and scaling table all updated — mcp-memory row removed, backend row notes volume + "do not scale to zero".
- `terraform validate` passes clean after all changes.

## 2026-03-18 (Parallel Step Dispatch Analysis + `_group_steps` Fix)

- Confirmed: all N tasks in a parallel batch are dispatched via a single list comprehension (`asyncio.create_task` per step) before any task acquires the semaphore. A 12-step batch → 12 tasks instantaneously. Semaphore gates concurrent *execution* (`async with pipeline_runner.semaphore:` inside `_worker`), not task count.
- `MAX_CONCURRENT_PIPELINES` = `configs.max_workers or 10`. Default 10. If a parallel batch exceeds 10 steps, tasks 11+ wait on the semaphore, then fire as slots free — all within the same batch, before the next batch starts.
- **Bug fixed**: `_group_steps` used pure adjacency — non-consecutive same-label steps (e.g. LLM returns `[A(g1), B(g2), C(g1)]`) created separate batches forcing A and C to serialize. Fixed with `pending_groups: Dict[str, List[ResearchStep]]` dict between `None` barriers. Same-label steps always coalesce into one batch regardless of interleaving. `_flush_pending()` emits them on each `None` step and at the end. Non-adjacent coalescing emits a `DEBUG` log.
- **Key invariant**: `_group_steps` is now a `@staticmethod` that accepts `List[ResearchStep]` — no side effects, pure transformation. Safe to call independently in tests.
- `pipeline.py` comment says `MAX_CONCURRENT_PIPELINES = 5` but actual value is `configs.max_workers or 10`. The constant in `pipeline.py` is the correct one at runtime; the PROJECT-KNOWLEDGE note was wrong (now corrected).

## 2026-03-18 (Web Tool Quality Fixes — Implemented)

- All 7 priority fixes from log analysis implemented across 3 files + 1 instructions file.
- `mcp/web_scrape/main.py`: UA pool (`_USER_AGENTS`, 6 strings), `asyncio.Semaphore(3)` concurrency cap, `_enforce_domain_rate()` with `_DOMAIN_MIN_INTERVAL=2.0s`, `_fetch_with_retry()` with 1-retry on 429 + fresh UA, `_SCRAPE_MAX_CHARS=50_000` output truncation.
- `mcp/web_search/server.py`: All 6 `def` tool handlers → `async def` with `asyncio.to_thread()`.
- `mcp/web_search/search_backends.py`: `_USER_AGENTS` pool + `_pick_ua()`, `GITHUB_TOKEN` env var → `Authorization: Bearer` header on GitHub API. Old static Chrome/91 UA removed.
- `backend/instructions/RESEARCH-METHODS.md`: URL dedup instruction added to Accumulated Insights.
- **Key design note**: `_enforce_domain_rate` updates `_domain_last_request` *before* sleeping — this chains waits for concurrent callers hitting the same domain, avoiding thundering herd even under Semaphore(3).
- **Key design note**: `_fetch_with_retry` raises 403 via `raise_for_status()` (no retry) — 403 is access-denied, retrying wastes quota; 429 is rate-limited, retry after backoff is correct.

### Metrics (3 log files, 280 web_search + 174 scrape_url dispatches)

- **Scrape success rate**: 160/174 = 92% (varies heavily by research topic — March 17 EV session was 20%)
- **Scrape error breakdown**: HTTP 403 (50%), HTTP 429 Too Many Requests (28%), HTTP 530 Cloudflare (14%), network errors (7%)
- **Search yield**: ~28% of searches return <10 results; ~5–7 per session return ≤1 result; 1 zero-result (`search_github`)

### Root Causes Identified

1. **Static Chrome 91 UA (2021-era)** in both `mcp/web_scrape/main.py` L33 and `mcp/web_search/search_backends.py` L16 — identical hardcoded string. Modern bot-detection (Cloudflare, Gartner, Akamai) fingerprints this instantly. Confirmed blocked: `gartner.com`, `anthropic.com`, `w3.org/community/agentprotocol/`, `cleantechnica.com`, `electrek.co`.

2. **Parallel burst triggers 429s** — SearchAgent dispatches 6–7 concurrent `scrape_url` calls per step with no inter-request delay, concurrency cap, or per-domain jitter. Root cause of all HTTP 429 errors.

3. **No retry or fallback in `scrape_url`** — raises `ValueError` immediately on any HTTP error (`main.py` L135). No exponential backoff, no alternative strategy.

4. **Overly specific queries → low yield** — SearchAgent generates verbose multi-clause queries (e.g. `"MCP Model Context Protocol agentic workflows tool-calling pipelines 2026 adoption patterns"`) that DuckDuckGo can only match 1 result for. `site:` operator on lightly-indexed domains also returns 1.

5. **`web_search` tool functions are synchronous** (`server.py` `def web_search`, not `async def`). DDGS and `requests.Session` in `search_backends.py` are blocking I/O — blocks the FastMCP event loop under concurrent orchestrator load.

6. **`search_github` unauthenticated** — hits 60 req/hr rate limit, silently returns 0 results with `"success": true`.

7. **No output size cap in `scrape_url`** — full page text returned. March 17 session produced 104MB + 114MB single-line NDJSON entries destroying two log rotation slots. Still unfixed.

8. **Duplicate URL scraping** — same URLs (e.g. `guptadeepak.com/the-complete-guide-to-mcp`) scraped multiple times across a session. No URL-level deduplication in the search pipeline.

### Priority Fix Order

1. UA rotation in `web_scrape/main.py`
2. Per-domain delay + concurrency cap in `scrape_url`
3. Retry with backoff (1 retry on 429, skip on 403)
4. Output size cap in `scrape_url` (e.g. 50KB)
5. Make `web_search` async (`asyncio.to_thread`)
6. GitHub token env var for `search_github`
7. Scrape URL deduplication in SearchAgent or `SearchResultStore`

### What Works Well

- DuckDuckGo delivers 10 results in ~69% of searches
- `site:` operator usage is sound practice; official domains (`modelcontextprotocol.io`, `devblogs.microsoft.com`, `github.blog`) scrape reliably
- All 4 MCP server keepalive pings healthy throughout all 3 sessions

## 2026-03-17 (smoke_test.py — Stale Constant Test)

- `test_new_orchestrator_constants` was checking for module-level constants `_DISTILL_MAX_CHARS`, `_STEP_SUMMARY_MAX_CHARS`, `_SECTION_DRAFT_TOP_K` on the `orchestrator` module — these were never added there.
- The implementation has always used `config.*` fields: `Config.distill_max_chars` (default 2000), `Config.step_summary_max_chars` (default 800), `Config.section_draft_top_k` (default 6), all defined in `config.py` with env-var overrides.
- Fix: test now does `from config import Config; cfg = Config()` and asserts on `cfg.*` fields.
- `config.py` exports only the `Config` class — there is no module-level `config` singleton in that file. Every consumer (orchestrator, etc.) instantiates `config = Config()` at its own module level.

## 2026-03-17 (Log Analysis — Session c8e5c4b2)

### Session facts

- Session span: `21:02 → 21:38` (~36 min), topic: EV market research (China penetration, investment landscape)
- Total log records: 6,896 (from `debug_2026-03-17T21-38-23_shutdown.json`)
- 45 errors, 2 warnings, 2 asyncio shutdown errors

### Bug: `_merge_analyst_outputs` TypeError (Steps 8 & 10 both failed)

- **Root cause:** `analyst_notes` in analyst JSON can be a dict/object when the LLM returns structured output rather than a plain string. `dict or ""` evaluates to the dict (truthy), then `dict + "\n\n"` raises `TypeError: unsupported operand type(s) for +: 'dict' and 'str'`. The inverse fires as `"can only concatenate str (not 'dict') to str"` depending on which side is a string first.
- **Location:** `orchestrator.py` `_merge_analyst_outputs()` ~L2198.
- **Fix pattern:** `str(existing.get("analyst_notes") or "") + "\n\n" + str(new.get("analyst_notes") or "")`.
- **Same risk exists** in `_run_analyst` fallback path where `response.content` is stored directly as `analyst_notes` — if the model returns a dict-like response object, downstream code will fail.

### Bug: Catastrophic log bloat from `mcp_client` DEBUG logging

- `mcp_client` logs the *full* `scrape_url` result body at DEBUG level. A single IEA PDF scrape produced a 104 MB single-line NDJSON entry; another produced 114 MB. Both `backend.ndjson.3` and `.5` are single-record, unreadable by any NDJSON parser. Log rotation is by size, so these immediately exhaust a rotation slot.
- **Fix pattern:** Add a `_truncate_for_log(result, max_chars=500)` helper in `mcp_client.py`. Log truncated content + original byte count.
- **Impact:** 218 MB disk consumed by 2 tool calls in a 36-minute session. In a production multi-session deployment this will fill disk quickly.

### Operational: 80% of errors are HTTP 403 bot-blocks

- 36 of 45 errors are `scrape_url` 403 Forbidden. Burst pattern: 10 errors in the first 2 minutes (parallel wave hitting news/financial sites simultaneously). Tails off to 1–2/min after 21:13.
- Sites confirmed blocking: cleantechnica.com, electrek.co, paywalled finance aggregators.
- No User-Agent rotation, proxy fallback, or inter-request jitter in `web_scrape/main.py`.

### LLM latency baseline

- Bedrock / Claude Haiku 4.5: **~7.4–7.5 seconds** upstream latency per call (consistent across all sampled calls). This is the dominant serial cost per research step.
- Client-side retry fired once (OpenAI SDK idempotency key with `-retry-` suffix). Transparent to the orchestrator — no orchestrator-level retry events observed.
- Distill compression working: 24–55% reduction before RAG chunking (observed step: 5,352 → 2,396 chars at 0.45 ratio).

### Logger volume distribution

- `openai._base_client` 34% + `model_backend` 17% + `mcp_client` 17% + `streamable_http` 15% = **83% of log volume at DEBUG level**. Only `orchestrator` (7%) and `search_result_store` (7%) are meaningful for operational insight. Consider raising the minimum level for the first four to INFO in production config.

### asyncio shutdown errors

- 2x "unhandled exception during asyncio.run() shutdown" — task cancellation during graceful stop. Harmless but noisy. The underlying task is likely a background MCP keepalive or RAG indexing task that doesn't handle `CancelledError` at shutdown.

## 2026-03-17 (Documentation Conventions)

- **Mermaid charts preferred in `docs/`**: wherever a workflow, pipeline, or data-flow diagram would be useful in a file under `docs/`, use a `mermaid` fenced code block rather than ASCII art. Mermaid renders natively in GitHub and VS Code Markdown preview.
- **README.md is intentionally thin**: it is a landing page only — quick start, Makefile commands, config snippets, and a table pointing to `docs/`. Do not add feature lists, architecture diagrams, advanced usage, or troubleshooting here; those belong in `docs/`.
- **`backend/.env` variable source of truth is `backend/config.py`**: all env var names, defaults, and groupings live there. When updating the README config example, read `config.py` directly rather than guessing.

## 2026-03-17 (E2E Validation Complete)

- **System confirmed working end-to-end**: both `test_e2e.py` (mock MCP + real LLM) and the fully deployed Docker stack (`make run-all`) are validated and working as of today.
- **Last bug fixed before green**: f-string double-brace bug in `orchestrator.py` outline prompt — `'{{"sections": [...]}}'.format(n=...)` was a literal format-string call inside what was meant to be an f-string. Fixed to a plain f-string with single braces; the `.format()` call removed. Commit: `5c8258d`.
- **`AgentPool` is the correct injection point**: `test_e2e.py` pattern (`AgentPool.from_single_backend(backend)` → `async_init(registry)` → `Orchestrator(agent_pool=pool)`) is the canonical way to instantiate the stack without the API server.
- **All subsystems validated**: async model backends, MCP keepalive (httpx health ping), session manager task ownership, semaphore-capped pipeline runner, RAG store, five-agent hierarchy, Docker build and compose stack.

## 2026-03-17 (Async Model Backends + Docker Validation)

- **model_backend.py now fully async**: replaced `OpenAI`/`AzureOpenAI` with `AsyncOpenAI`/`AsyncAzureOpenAI` in `OpenAIBackend`, `AzureOpenAIBackend`, `BedrockBackend`, and `GCPVertexAIBackend`. All `.create()` calls now `await`ed. Streaming uses `async for`. Ollama and HuggingFace were already async (aiohttp). This eliminates thread-pool blocking during the ~54min pipeline run.
- **Three bonus bugs fixed along the way**: (1) `AzureOpenAIBackend.generate` used `message.get("content", "")` on a Pydantic object — fixed to `message.content or ""`. (2) `OllamaBackend.generate` called `result.raise_for_status()` on a dict instead of the response object — fixed to `response.raise_for_status()` before `.json()`. (3) `GCPVertexAIBackend.stream_generate` used Responses-API event format (`event.type == "response.output_text.delta"`) instead of chat completions format, and had a copy-paste error logging "BedrockBackend" — fixed both.
- **Docker build confirmed working**: `docker build --progress=plain -f backend/Dockerfile ./backend` completes successfully. All layers cached. `torch==2.10.0` CPU-only install resolves correctly (it IS a valid version as of March 2026).
- **Docker infrastructure fully committed**: all previously documented fixes (60s keepalive, httpx health-check ping, `_is_session_error` narrowed to anyio errors, `config.py` `AGENT_MODE` fix, CPU-only torch Dockerfile, model pre-baked) are confirmed in the current codebase.
- **`backend/.env` already has Docker service names** for all MCP URLs (`http://mcp-memory-server:9494/mcp` etc.) — no docker-compose env overrides needed.
- **Only one WebSocket endpoint**: `/ws/research`. No v1/v2 split. `App.tsx` uses `VITE_WS_URL` which defaults to `ws://localhost:9999/ws/research`. PROJECT-KNOWLEDGE.md was wrong about this.
- **To run the full stack in Docker**: `make run-all` (→ `docker-compose-full.yml`). `make run` only starts backend+UI with no MCP servers; MCP registry handles missing servers gracefully but research will have no tools.

## 2026-03-17 (test_e2e.py Observations)

- `test_e2e.py` runs the full orchestrator pipeline (plan → execute → synthesize) with **mock MCP** but **real LLM backend** (reads `backend/.env`). Output goes only to the terminal that launched it — no log files are written (bypasses API server/logging infrastructure entirely).
- With `MODEL_BACKEND=aws` (Bedrock) and `MAX_ITERATIONS=3`, a full run takes **~54 minutes** end-to-end. This is dominated by LLM roundtrips across all five agents.
- Liveness check pattern: `lsof -p <pid> | grep -E 'IPv|TCP'` confirms active HTTPS connections to Bedrock while running — distinguishes "making LLM calls" from "hung at startup".
- macOS `ps` does not support `etimes` keyword — use `etime` or just `date` to calculate elapsed time from `lstart`.
- The `/tmp/e2e_out.txt` redirect attempt (exit 127) was a separate failed run from a different terminal context; PID 69667 was the successful detached run.

## 2026-03-17 (Keepalive Ping Session Contention Fix)

- **Root cause of 2nd-cycle keepalive failures**: `ping()` was calling `session.list_tools()` on the same shared `ClientSession` as active tool calls. At 60s intervals the ping fires while web_search/web_scraper/file_handler have in-flight requests — the 5s timeout on `list_tools()` expired because the SSE receive loop was busy routing those responses. Memory server survived because it has no concurrent tool calls during the search phase.
- **Fix**: `ping()` now GETs `{base_url}/health` via a fresh `httpx.AsyncClient` — never touches the shared session. Health endpoints are confirmed available on all four MCP servers (same endpoint used by docker-compose healthchecks).
- **Pattern to remember**: MCP `ClientSession` is not safe for concurrent requests from multiple asyncio tasks. The keepalive task and the tool-call tasks share the same session — any "ping" must use an out-of-band mechanism.

## 2026-03-17 (Embeddings + Docker Fixes)

- **MCP keepalive interval reduced from 240s → 60s** in `mcp_client.py` (`_KEEPALIVE_INTERVAL` default). 240s was too close to Docker Desktop's NAT idle timeout; all 3 SSE streams died before the first keepalive ping fired (confirmed in logs: all 3 failures at exactly ~246s after start).
- **Dockerfile: CPU-only torch** — `torch==2.10.0` removed from `requirements.txt` and installed via `--index-url https://download.pytorch.org/whl/cpu` in Dockerfile. CUDA build was ~1.7 GB wasted on a CPU-only container.
- **Dockerfile: model pre-baked** — `all-MiniLM-L6-v2` now downloaded at image build time. No more runtime HuggingFace download / rate throttling.
- **Two sentence-transformer instances** — backend + memory MCP server both load PyTorch + sentence-transformers. Reducing torch to CPU-only significantly reduces baseline footprint. With active research load, both containers together can push Docker Desktop VM toward OOM pressure.
- **Root cause of shutdown SIGTERM** — external (Docker or user), NOT a code crash. Shutdown sequence was completely clean. App was mid-operation (web_search tool calls in-flight) when killed.

## 2026-03-17 (Bootstrap)

- Project is a hierarchical multi-agent deep research system: Root → Search → Analyst → Loop (QA) → ReportComposer
- Five MCP servers: memory (:8001), web_search (:8002), web_scrape (:8003), file_handler (:8004)
- v2 WebSocket endpoint (`/ws/v2/research`) is the active path; v1 (`/ws/research`) is legacy
- `ResearchSessionManager` (session_manager.py) owns ALL background tasks — never use raw `asyncio.create_task()` in request handlers
- `PipelineRunner` (pipeline.py) enforces `MAX_CONCURRENT_PIPELINES=5` via a semaphore and deduplicates search queries across concurrent pipeline runs
- RAG store (`search_result_store.py`) is per-session with in-process cosine ranking — this is the *only* context management mechanism, no hard token caps
- `SearchAgent` uses a sliding window of `_MAX_SEARCH_HISTORY_MESSAGES=8` messages in `execution_messages` to avoid context bloat; full content is in the RAG store
- `LoopAgent` QA retry loop is capped at `max_qa_retries=2`
- Bare `except:` clauses are a bug in this codebase — `CancelledError` must propagate; use `except Exception:` at minimum
- WebSocket drain does NOT cancel the background task on disconnect — allows reconnection to live session
- Model backend abstraction supports: OpenAI, Ollama (default), Bedrock, Azure, GCP Vertex, HuggingFace
- Frontend state: single `AppContext` + discriminated `Message` type union keyed on `type` field
- `AGENT_MODE` env var controls which agent class is instantiated: `research` / `chat` / `self-optimization`

## 2026-03-17 (Shutdown Loop Investigation)

- **"Keeps shutting down" = SIGTERM, not a code crash.** `asyncio.exceptions.CancelledError: Cancelled via cancel scope` in uvicorn lifespan is anyio propagating an OS signal (SIGTERM/SIGINT from Docker stop / Ctrl+C). The shutdown sequence runs correctly; no internal self-exit code exists.
- **Critical bug (fixed in wip commit):** `config.py` `agent_mode` field previously read `os.getenv("MODEL_BACKEND", "ollama")` instead of `os.getenv("AGENT_MODE", "research")`. If `AGENT_MODE` was absent from `.env`, the field got the model backend string (e.g. "bedrock") as value, causing a Pydantic `ValidationError` at import time → startup crash → Docker `restart: unless-stopped` loops.
- **Check `backend/.env` for `AGENT_MODE=research`** when diagnosing restart loops.
- **MCP `_is_session_error` narrowed (fixed in wip commit):** Previously matched all `RuntimeError` which caught shutdown-time errors like "Event loop is closed" and triggered spurious reconnect attempts. Fixed to only match `anyio.ClosedResourceError`, `anyio.BrokenResourceError`, and known transport error substrings.
- **MCP keepalive added (wip commit):** `MCPServerRegistry.start_keepalive()` pings all servers every **60s** (configurable via `MCP_KEEPALIVE_INTERVAL`). Prevents Docker NAT from silently dropping idle SSE streams. Called in `lifespan()` after MCP registration. Default was initially 240s but that was too close to Docker Desktop NAT timeout; reduced to 60s.
- **Post-shutdown reconnect storm:** After a Docker restart, the frontend's `useWebSocket` hook retries up to 10x with exponential backoff. Each attempt sends `resume` with the old `session_id`, which no longer exists in the fresh in-memory `SessionStore` — gets an error and closes. This is cosmetic/expected; sessions are not persisted across restarts.
- **Session store is in-memory only** — all session state is lost on restart. `SessionStore.prune_expired()` is called on each new `query` message (opportunistic TTL eviction).
- **`docker-compose.yml` has `memory: 3g` limit** for `research-assistant`. In-process sentence-transformer (~90MB weights) + active LLM calls can push past 1GB under load. OOM kill (exit code 137) is another shutdown cause to check if SIGTERM is ruled out.
- **Production WebSocket port is `:9999`**, MCP ports: memory=9494, web_search=9393, web_scrape=9292, file_handler=9191 (internal Docker names: `mcp-{memory,web-search,web-scraping,file-handler}-server`).
