# RAG — Implementation Notes

> **Session-scoped store:** `backend/search_result_store.py`  
> **Cross-session store (flat + graph):** `backend/long_term_memory.py`  
> **Embedding utilities:** `backend/embeddings.py`  
> **Retrieval helpers:** `backend/retrieval_utils.py`

This document describes the retrieval-augmented generation (RAG) mechanisms used in the research pipeline: the **session-scoped store** for within-session context, the **flat long-term memory** for cross-session evidence, and the **knowledge graph (GraphRAG)** for cross-session structural knowledge.

---

## Architecture

The RAG system has two layers:

### Layer 1 — Session-Scoped Store (`SearchResultStore`)

Built entirely **in-process** — no external vector database required. Every research session gets its own isolated `SearchResultStore` instance created at the start of `Orchestrator._reset_state()`.

```
MCP tool result (web_scrape / web_search)
          │
          ▼
  Orchestrator._distill_tool_result()          ← strips navigation chrome,
          │                                       caps at config.distill_max_chars
          ▼
  SearchResultStore.add()
          │
          ├── Extract text from tool result dict
          ├── Split into overlapping chunks (_CHUNK_SIZE=1500 chars, _CHUNK_OVERLAP=150 chars)
          ├── Embed each chunk via embed_texts() — all-MiniLM-L6-v2 sentence transformer
          └── Store Chunk objects in memory (with hash-based dedup)

Step query / analyst context request
          │
          ▼
  SearchResultStore.retrieve(query, top_k=N)
          │
          ├── Embed query via embed_query() — cached per session
          ├── Compute cosine similarity between query embedding and all stored chunks
          ├── Rank and return top-k chunks as concatenated text
          └── Falls back to TF-IDF ranking if embedding model unavailable
```

The LoopAgent can call `SearchResultStore.mark_superseded(chunk_ids)` to flag chunks from invalidated claim sources so they are excluded from all future `retrieve()` calls (but remain in the store for debugging).

### Layer 2 — Cross-Session Long-Term Memory (`AsyncLongTermMemory`)

Persistent storage backed by Neo4j (async driver). Two sub-layers:

1. **Flat memory store** (`:Memory` nodes): raw text blobs with cosine similarity ranking via a Neo4j vector index. Raw evidence chunks and analyst claims are persisted here at the end of each session.

2. **Knowledge graph** (`KnowledgeGraph`): typed entity nodes (Person, Organization, Technology, Concept, Event, Location, Metric), typed relationship edges (CAUSES, ENABLES, USES, etc.), Claim nodes, Document nodes, and Community summaries. Used for relationship-aware recall that follows multi-hop entity connections across research sessions.

```
                    ┌──────────────────────────────────────────────┐
                    │         AsyncLongTermMemory                  │
                    │                                              │
                    │  :Memory nodes        (flat vector store)    │
                    │                                              │
                    │  ┌────────────────────────────────────────┐  │
                    │  │  KnowledgeGraph (ltm.graph)            │  │
                    │  │                                        │  │
                    │  │  :Person / :Organization / :Technology  │  │
                    │  │  :Concept / :Event / :Location / :Metric│  │
                    │  │  :Claim    (assertions w/ confidence)   │  │
                    │  │  :Document (source provenance)          │  │
                    │  │  :Community (cluster summaries)         │  │
                    │  │                                        │  │
                    │  │  Typed edges: CAUSES, ENABLES, USES... │  │
                    │  │  Structural: IS_A, CONTRADICTS,        │  │
                    │  │    SOURCED_FROM, MEMBER_OF, ASSERTS     │  │
                    │  └────────────────────────────────────────┘  │
                    └──────────────────────────────────────────────┘
```

There are no hard token caps anywhere in the pipeline — the RAG stores with in-process cosine ranking are the **sole context-management mechanism**.

---

## Chunking

| Parameter | Value | Purpose |
|---|---|---|
| `_CHUNK_SIZE` | `1500` chars | Maximum characters per chunk (~375 tokens). Small enough for tight prompts, large enough for coherence. |
| `_CHUNK_OVERLAP` | `150` chars | Overlap between adjacent chunks to prevent key sentences from being split across boundaries. |

Chunks are split from the raw text returned by MCP tools (scraped page content, search snippets). Each `Chunk` carries provenance: `source_url`, `step_id`, `tool_name`, `chunk_index`.

Duplicate chunks (same MD5 hash) are silently dropped — this prevents the same paragraph from being retrieved multiple times when a URL is hit by multiple steps.

---

## Embedding Model

- **Model:** `sentence-transformers/all-MiniLM-L6-v2` (configurable via `EMBEDDINGS_MODEL` env var)
- **Loaded:** Once at startup during `warm_up_embeddings()` in `lifespan()`.
- **Disabled:** Set `EMBEDDINGS_ENABLED=false` to skip model loading entirely on memory-constrained hosts. `retrieve()` falls back to TF-IDF ranking via `tfidf_similarity()` from `retrieval_utils.py`.
- **Thread pool:** Encode calls are offloaded to a `ThreadPoolExecutor` (size controlled by `MAX_EMBEDDING_WORKERS`, default `2`) so the FastAPI event loop is never blocked.

Query embeddings are cached per session — the same query string is never embedded twice within a single research run.

---

## Retrieval Constants

These are `Config` fields in `config.py` (Pydantic, loaded from environment variables). All can be overridden at runtime without code changes:

| `Config` field | Default | Env var | Purpose |
|---|---|---|---|
| `analyst_top_k` | `8` | `ANALYST_TOP_K` | Chunks retrieved per AnalystAgent RAG query |
| `max_tool_result_chars_in_message` | `5000` | `MAX_TOOL_RESULT_CHARS_IN_MESSAGE` | Characters of a tool result kept in SearchAgent's sliding message window (full content in RAG store) |
| `max_search_history_messages` | `8` | `MAX_SEARCH_HISTORY_MESSAGES` | Sliding window size for SearchAgent's `execution_messages` |
| `max_analyst_fallback_chars` | `8000` | `MAX_ANALYST_FALLBACK_CHARS` | Chars of raw search output used when the RAG store has no chunks yet (first iteration) |
| `distill_max_chars` | `2000` | `DISTILL_MAX_CHARS` | Max chars the distillation filter preserves from a raw tool result before chunking |
| `step_summary_max_chars` | `800` | `STEP_SUMMARY_MAX_CHARS` | Max chars for per-step summary bullets fed to the multi-pass synthesis Outline Phase |
| `section_draft_top_k` | `6` | `SECTION_DRAFT_TOP_K` | Chunks retrieved per report section during Section-Drafting |

---

## Multi-Pass Synthesis

The `ReportComposer` uses the RAG store in two phases:

### Phase A — Outline

The Root backend receives only the compressed **step summaries** (≤ `config.step_summary_max_chars` each) — a "weekly briefing" of what every step found. It produces a structured section outline. This keeps the outline call small and focused.

### Phase B — Section Drafting (loop)

Before the drafting loop begins, `_recall_memories(query, limit=8)` fetches relevant prior-session evidence, then `filter_long_term_memories()` removes any items that duplicate current-session chunks (see [Deduplication](#deduplication-with-long-term-memory) below). The surviving memory items are included in every section's drafting prompt as "Prior Research Context".

For each section, a **targeted RAG query** retrieves `config.section_draft_top_k` chunks (default 6) from the full store, giving the ReportComposer detailed evidence for that section only. Sections are drafted sequentially; each one is emitted as a `section_draft` WebSocket event so the frontend can stream the report incrementally. A `synthesis_progress` event is emitted before each draft with `{section_index, section_title, total_sections}`.

### Phase C — Assembly

All drafted sections are concatenated into the final Markdown document and emitted as the `report` event. After `report` is emitted, the top-20 semantically-ranked session chunks are persisted to long-term memory (`persist_to_long_term_memory(query, top_k=20)`), then `graph.update_communities()` runs community detection.

---

## Deduplication with Long-Term Memory

There are two separate dedup mechanisms:

**1. Store-time dedup in `AsyncLongTermMemory`** (`_DEDUP_THRESHOLD = 0.95` in `long_term_memory.py`): When persisting a new memory, the store checks whether a near-identical entry already exists. If the cosine similarity to any existing memory exceeds this threshold, the new entry is not stored. This prevents the long-term store from accumulating near-duplicate evidence across repeated sessions on the same topic.

**2. Recall-time dedup in `SearchResultStore.filter_long_term_memories()`** (`_DEDUP_SIMILARITY_THRESHOLD = 0.85` in `search_result_store.py`): Called during synthesis Phase B, after `_recall_memories()` returns prior-session items. Long-term memory strings that are near-duplicates of **current-session chunks** are removed before being injected into section drafting prompts. This prevents the report from restating facts the current session already found. The filter uses TF-IDF similarity (fast, no separate embedding call).

---

## Knowledge Graph (GraphRAG)

> **Implementation:** `KnowledgeGraph` class in `backend/long_term_memory.py`, accessible as `ltm.graph`

The knowledge graph captures **relationships between facts** — not just the facts themselves — so that cross-session recall can surface structural knowledge, entity connections, and thematic clusters.

### Node Types (Typed Labels)

Each entity type has its own Neo4j label, uniqueness constraint, and vector index:

| Node Label | Stores | Type-Specific Properties |
|---|---|---|
| `:Person` | People | `affiliation`, `role`, `expertise_areas` |
| `:Organization` | Companies/institutions | `org_type`, `industry`, `headquarters` |
| `:Technology` | Tools/frameworks | `tech_category`, `version`, `license`, `maturity` |
| `:Concept` | Abstract ideas | `domain`, `abstraction_level` |
| `:Event` | Temporal occurrences | `start_date`, `end_date`, `event_type`, `location` |
| `:Location` | Geographic entities | `geo_type`, `parent_location` |
| `:Metric` | Quantitative data | `value`, `unit`, `measurement_date`, `trend` |

All entity types share base properties: `id`, `name`, `entity_type`, `description`, `mention_count`, `source_sessions`, `first_seen`, `last_seen`, `last_confirmed`, `confirmation_count`, `embedding`.

Additional node types:

| Node Label | Stores | Key Properties |
|---|---|---|
| `:Memory` | Raw evidence text blobs | `content`, `category`, `importance`, `tags` |
| `:Claim` | Individual assertions | `text`, `confidence`, `status` (supported/disputed/unverified/retracted), `source_session` |
| `:Document` | Research source documents | `url` (unique), `title`, `content_summary`, `doc_type`, `credibility_score`, `domain` |
| `:Community` | LLM-generated cluster summaries | `entity_ids`, `topic`, `summary` |

### Relationship Types (Typed Labels)

Factual relationships use typed Neo4j labels instead of a single `:RELATES_TO`:

| Label | Semantics | Example |
|---|---|---|
| `CAUSES` | Causal relationship | Climate change → CAUSES → sea level rise |
| `ENABLES` | Facilitation | GPU computing → ENABLES → deep learning |
| `PREVENTS` | Inhibition | Vaccination → PREVENTS → disease spread |
| `REQUIRES` | Dependency | Machine learning → REQUIRES → training data |
| `PART_OF` | Containment | GPU → PART_OF → NVIDIA A100 |
| `USES` | Utilization | GPT-4 → USES → transformer architecture |
| `PRODUCES` | Output | Solar panel → PRODUCES → electricity |
| `COMPETES_WITH` | Competition | AWS → COMPETES_WITH → Azure |
| `AFFILIATED_WITH` | Membership | Researcher → AFFILIATED_WITH → MIT |
| `AUTHORED_BY` | Authorship | Paper → AUTHORED_BY → Researcher |
| `FUNDED_BY` | Financial backing | Startup → FUNDED_BY → VC firm |
| `PRECEDED_BY` | Temporal ordering | GPT-4 → PRECEDED_BY → GPT-3 |
| `OCCURRED_AT` | Location/venue | Conference → OCCURRED_AT → San Francisco |
| `ASSERTS` | Claim-entity link | Claim → ASSERTS → Entity |
| `SUPPORTS` | Evidence support | Document → SUPPORTS → Claim |
| `REFUTES` | Contradiction | Finding → REFUTES → Claim |
| `MENTIONS` | Reference | Document → MENTIONS → Entity |
| `RELATES_TO` | Catch-all fallback | Used when no specific label matches |

Free-form verb phrases from LLM extraction are classified into typed labels via keyword matching (`classify_relation()` in `long_term_memory.py`). Unmatched phrases fall back to `RELATES_TO`.

Structural (non-factual) relationship labels:

| Label | Semantics |
|---|---|
| `IS_A` | Hierarchical taxonomy (child → parent) |
| `CONTRADICTS` | Flags conflicting claims between entities |
| `SOURCED_FROM` | Entity → Document provenance |
| `MEMBER_OF` | Entity → Community membership |

### Entity Deduplication

When upserting an entity, the graph embeds the new entity's `name: description` and queries for existing entities with cosine similarity ≥ `_ENTITY_DEDUP_THRESHOLD` (0.92). On match: `mention_count` is incremented, `session_id` is appended, and the longer description is kept. On miss: a new entity is created.

### Graph Extraction (Write Path)

After each analyst step completes its QA loop, `Orchestrator._extract_graph_triples()` makes **one LLM call** to extract structured entities, relationships, and claims from the step's vetted findings:

```
Analyst claims (QA-vetted)
       │
       ▼
  _extract_graph_triples()
       │
       ├── Build extraction prompt with claims text + existing graph context
       ├── LLM returns JSON: {entities: [...], relationships: [...], claims: [...]}
       ├── Parse JSON (strip markdown fences if present)
       ├── For each entity  → graph.upsert_entity(name, type, properties)
       │     └── If parent_type provided → graph.upsert_entity(parent) + graph.store_hierarchy()
       ├── For each relationship → graph.store_relationship(source, target, label)
       │     └── If source_url provided → graph.store_document() + graph.link_document_to_entity()
       └── For each claim → graph.store_claim(text, confidence, entity_names)
```

The extraction prompt requests type-specific properties per entity type (e.g., `affiliation` for Person, `version` for Technology) and explicit relationship labels from the typed vocabulary. The LLM can also provide a `label` field to suggest a specific relationship type, which is validated against the typed vocabulary before storage.

Only QA-vetted claims are extracted — raw search results are never fed to the graph. This keeps the graph's signal-to-noise ratio high.

### Graph Recall (Read Path)

`Orchestrator._recall_memories(query, limit=N)` combines two sources. It is called in two places: during `plan()` with `limit=5` (default) and during `synthesize()` Phase B with `limit=8`.

1. **Graph-aware recall** via `graph.recall_graph_context(query, entity_limit=5, max_hops=2)`:
   - Vector-search entities relevant to the query (top 5) across all typed entity indexes
   - Traverse up to 2 hops of typed relationships from seed entities
   - Retrieve claims linked to seed entities
   - Retrieve community summaries for overlapping entity clusters
   - Format as structured text (`KNOWN ENTITIES`, `KNOWN RELATIONSHIPS`, `CLAIMS`, `THEMATIC CLUSTERS`)
   - Optionally include `KNOWN CONTRADICTIONS` and `SOURCE DOCUMENTS`

2. **Flat vector search** via `find_similar(query, limit=N, min_similarity=0.5)`:
   - Cosine rank over the flat memory collection
   - Returns raw evidence + claim text from prior sessions

During planning, both are injected into the Root agent's planning prompt under "Relevant prior research context". During synthesis, the results are further deduped against current-session chunks before being included in section prompts.

### Community Detection (Post-Synthesis)

After `Orchestrator.synthesize()` emits the `report` event and persists session chunks, it calls `graph.update_communities(summarize_fn)`:

1. Fetch all typed factual relationships and build an adjacency graph by entity name
2. Greedy BFS clustering: entities sharing ≥ `_COMMUNITY_MIN_SHARED_RELS` (2) connections are grouped
3. For each cluster: build context from entity descriptions + intra-cluster relationships
4. LLM generates a concise summary per cluster
5. Store/update community in `_kg_communities` (overlapping communities are merged by ≥50% entity overlap)

Communities provide high-level thematic context during planning — the Root agent sees "these topics are connected" without needing to traverse individual relationships.

### Graceful Degradation

- If Neo4j connection fails during initialization, `_available=False` and all graph methods return empty results
- If `EMBEDDINGS_ENABLED=false`, zero-vectors are stored — storage succeeds but similarity search is meaningless; metadata-filtered Cypher queries still work
- All graph operations are wrapped in try/except — extraction or recall failures never block the research pipeline

---

## Session Isolation

Each `Orchestrator` instance (one per WebSocket `query` message) creates its own `SearchResultStore`. There is **no shared state between concurrent research sessions** — all chunks, embeddings, and query caches are garbage-collected when the session completes.

---

## Performance Characteristics (Observed)

- **Embedding throughput:** `all-MiniLM-L6-v2` handles batches of 20–30 chunks in < 100 ms on CPU.
- **Retrieval latency:** Cosine similarity over a typical session's store (100–400 chunks) completes in < 10 ms.
- **Dominant cost:** LLM inference (Bedrock/Claude Haiku 4.5 = ~7.4 s/call), not the RAG layer.
- **Log volume caution:** MCP `scrape_url` results can be very large (PDF scrapes > 100 MB). The `mcp_client` should truncate tool result bodies before logging at DEBUG level to avoid log rotation exhaustion.
