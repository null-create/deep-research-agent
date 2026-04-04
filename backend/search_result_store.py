"""
search_result_store.py

A session-scoped RAG store for raw web-scrape and search tool results.

During the orchestrator's run-step loop every raw tool result (scraped page,
search snippet, etc.) is chunked and stored here.  At retrieval time the store
uses in-process sentence-transformer embeddings (via ``embeddings.py``) to rank
chunks by semantic similarity to the current query and returns only the most
relevant content — so downstream agents always receive *focused* context
instead of either a firehose of raw text or hard-truncated fragments.

Architecture
------------
                ┌──────────────────────────────────────────┐
                │          SearchResultStore               │
                │                                          │
  tool result ─►│  chunk() ──► embed_texts()               │
                │              (in-process sentence-xfmr)  │
                │                                          │
  step query  ─►│  retrieve() ─► cosine similarity rank    │
                │              ─► top-k chunks returned    │
                └──────────────────────────────────────────┘

Falls back gracefully to local TF-IDF ranking if sentence-transformers is
not installed (e.g. lightweight dev environment without ML deps).

Each Orchestrator run gets its own store instance (created in _reset_state),
so there is no cross-session bleed.
"""

from __future__ import annotations

import hashlib
import json as _json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, List, Optional

import numpy as np

from embeddings import (
    embed_texts,
    embed_query as _embed_query_fn,
    find_similar,
    EmbeddingError,
)
from retrieval_utils import tfidf_similarity

if TYPE_CHECKING:
    from long_term_memory import AsyncLongTermMemory

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------

# Maximum characters per chunk when splitting a raw tool result.
# ~1 500 chars ≈ ~375 tokens with typical LLM tokenizers — small enough to
# keep individual chunk prompts tight, large enough to preserve coherence.
_CHUNK_SIZE: int = 1_500

# Number of characters to overlap between consecutive chunks so that a key
# sentence that straddles a boundary is never split across two chunks.
_CHUNK_OVERLAP: int = 150

# Default number of top-k chunks to return from retrieve().
_DEFAULT_TOP_K: int = 5

# Cosine similarity threshold above which a long-term memory item is
# considered a duplicate of a session chunk and omitted from the report prompt.
_DEDUP_SIMILARITY_THRESHOLD: float = 0.85


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class Chunk:
    """A single piece of text extracted from a tool result, with provenance."""

    text: str
    source_url: str = ""
    step_id: int = 0
    tool_name: str = ""
    chunk_index: int = 0
    # Embedding vector — populated lazily when available
    embedding: Optional[List[float]] = field(default=None, repr=False)
    # Superseded chunks are excluded from active retrieval; they remain in the
    # store so their content can still be inspected for debugging.
    superseded: bool = field(default=False)

    @property
    def id(self) -> str:
        """Stable hash-based identifier for deduplication."""
        return hashlib.md5(self.text.encode()).hexdigest()[:12]


# ---------------------------------------------------------------------------
# Core store
# ---------------------------------------------------------------------------


class SearchResultStore:
    """
    Session-scoped vector store for raw search / scrape results.

    Usage
    -----
    store = SearchResultStore(long_term_memory)   # one per research session

    # After each tool call:
    await store.add(tool_name="web_scrape", result=tool_result_dict,
                    step_id=step.id, source_url=url)

    # Before building an analyst prompt:
    context_str = await store.retrieve(query=step.description, top_k=5)
    """

    def __init__(
        self,
        long_term_memory: Optional["AsyncLongTermMemory"] = None,
    ) -> None:
        """
        Parameters
        ----------
        long_term_memory:
            Optional in-process ``AsyncLongTermMemory`` instance for cross-session
            persistence.  When ``None``, ``persist_to_long_term_memory`` is a no-op.
        """
        self._long_term_memory = long_term_memory
        self._chunks: List[Chunk] = []
        self._embeddings_ready: bool = False
        # Track which chunk ids we've seen to avoid storing duplicates
        self._seen_ids: set[str] = set()
        # Cache of query string → embedding vector so the same query is never
        # embedded twice during a single research session.
        self._query_embedding_cache: Dict[str, List[float]] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def add(
        self,
        tool_name: str,
        result: Dict[str, Any],
        step_id: int = 0,
        source_url: str = "",
    ) -> int:
        """
        Extract text from a raw MCP tool result, chunk it, embed each chunk
        via the file_handler MCP server, and store everything in-memory.

        Parameters
        ----------
        tool_name:
            Name of the MCP tool that produced the result (for provenance).
        result:
            The raw dict returned by ``mcp_registry.call_tool()``.
        step_id:
            The plan step this result belongs to.
        source_url:
            The URL or identifier of the original source, if known.

        Returns
        -------
        int
            Number of new (non-duplicate) chunks added.
        """
        text = self._extract_text(result)
        if not text:
            return 0

        raw_chunks = self._split_into_chunks(text)
        new_chunks: List[Chunk] = []

        for i, chunk_text in enumerate(raw_chunks):
            chunk = Chunk(
                text=chunk_text,
                source_url=source_url,
                step_id=step_id,
                tool_name=tool_name,
                chunk_index=i,
            )
            if chunk.id in self._seen_ids:
                continue
            self._seen_ids.add(chunk.id)
            new_chunks.append(chunk)

        if not new_chunks:
            return 0

        # Attempt to embed via file_handler MCP server
        embedded = await self._embed_chunks(new_chunks)
        self._chunks.extend(embedded)

        logger.debug(
            "[SearchResultStore] step=%d: added %d chunks (total=%d)",
            step_id,
            len(embedded),
            len(self._chunks),
        )
        return len(embedded)

    async def retrieve(
        self,
        query: str,
        top_k: int = _DEFAULT_TOP_K,
        max_chars: Optional[int] = None,
        step_id_filter: Optional[int] = None,
        exclude_step_id: Optional[int] = None,
        include_superseded: bool = False,
    ) -> str:
        """
        Return the most relevant stored chunks as a single context string.

        Superseded chunks are excluded by default (set ``include_superseded=True``
        to override, e.g. for debugging).

        If embeddings are available the ranking is done with cosine similarity
        (using the file_handler MCP server).  Otherwise falls back to a local
        TF-IDF rank over chunk text.

        Parameters
        ----------
        query:
            The question / step description to rank chunks against.
        top_k:
            Maximum number of chunks to return.
        max_chars:
            Optional hard character budget for the returned string.
            If ``None`` (default) no cap is applied — all top_k chunks
            are returned in full.
        step_id_filter:
            If set, only consider chunks from this step id.
        exclude_step_id:
            If set, exclude all chunks from this step id.  Used to retrieve
            cross-step context while keeping the current step's own chunks
            out of the result (they are handled separately via step_id_filter).
        include_superseded:
            If True, superseded chunks are included in ranking/results.
        """
        if not self._chunks:
            return ""

        pool = [
            c
            for c in self._chunks
            if (include_superseded or not c.superseded)
            and (step_id_filter is None or c.step_id == step_id_filter)
            and (exclude_step_id is None or c.step_id != exclude_step_id)
        ]
        if not pool:
            return ""

        ranked = await self._rank_chunks(query, pool)
        selected: List[Chunk] = []
        total_chars = 0
        for chunk in ranked[:top_k]:
            if max_chars is not None and total_chars + len(chunk.text) > max_chars:
                break
            selected.append(chunk)
            total_chars += len(chunk.text)

        if not selected:
            return ""

        parts: List[str] = []
        for chunk in selected:
            provenance = f" [{chunk.source_url}]" if chunk.source_url else ""
            parts.append(f"[Step {chunk.step_id}{provenance}]\n{chunk.text}")

        return "\n\n---\n\n".join(parts)

    def mark_superseded(self, chunk_ids: List[str], reason: str = "") -> int:
        """
        Flag specific chunks as superseded so they are excluded from future
        ``retrieve()`` calls.

        This is triggered by the LoopAgent when it resolves a contradiction:
        the "losing" claim's source chunks are superseded so they no longer
        pollute retrieval results.

        Parameters
        ----------
        chunk_ids:
            List of ``Chunk.id`` values to flag.
        reason:
            Optional human-readable explanation (logged for debugging).

        Returns
        -------
        int
            Number of chunks actually flagged (may be less than len(chunk_ids)
            if some ids were not found in the store).
        """
        id_set = set(chunk_ids)
        flagged = 0
        for chunk in self._chunks:
            if chunk.id in id_set and not chunk.superseded:
                chunk.superseded = True
                flagged += 1
        if flagged and reason:
            logger.debug(
                "[SearchResultStore] Superseded %d chunk(s): %s", flagged, reason
            )
        return flagged

    def mark_superseded_by_step(self, step_id: int, reason: str = "") -> int:
        """
        Flag all chunks belonging to *step_id* as superseded.

        Convenience wrapper used when an entire step's findings are invalidated
        by later QA resolution (e.g. the step searched the wrong sub-question).
        """
        ids = [c.id for c in self._chunks if c.step_id == step_id]
        return self.mark_superseded(ids, reason=reason)

    def chunk_count(self, include_superseded: bool = True) -> int:
        """Total number of chunks currently stored."""
        if include_superseded:
            return len(self._chunks)
        return sum(1 for c in self._chunks if not c.superseded)

    async def filter_long_term_memories(
        self,
        memory_items: List[str],
        similarity_threshold: float = _DEDUP_SIMILARITY_THRESHOLD,
    ) -> List[str]:
        """
        Remove long-term memory strings that are near-duplicate of an existing
        session chunk (cosine similarity ≥ *similarity_threshold*).

        This prevents the ReportComposer's prompt from being padded with stale
        or superseded findings when a more recent session result already covers
        the same fact.

        Parameters
        ----------
        memory_items:
            Plain-text memory strings from the long-term ``memory`` MCP server.
        similarity_threshold:
            Cosine similarity cutoff.  Items scoring above this against ANY
            active session chunk are dropped.

        Returns
        -------
        List[str]
            Deduplicated list of memory strings that are not already covered
            by the current session.
        """
        if not memory_items or not self._chunks:
            return memory_items

        active_chunks = [c for c in self._chunks if not c.superseded]
        if not active_chunks:
            return memory_items

        kept: List[str] = []
        for mem_text in memory_items:
            # Use the shared TF-IDF scorer as a fast local filter.
            # If vector embeddings are available we also try cosine.
            corpus = [c.text for c in active_chunks]
            scores = tfidf_similarity(mem_text, corpus)
            max_score = max(scores) if scores else 0.0

            if max_score >= similarity_threshold:
                logger.debug(
                    "[SearchResultStore] Long-term memory item deduped "
                    "(score=%.3f ≥ threshold=%.3f): %.80s…",
                    max_score,
                    similarity_threshold,
                    mem_text,
                )
                continue
            kept.append(mem_text)

        return kept

    async def persist_to_long_term_memory(
        self,
        query: str,
        top_k: int = 20,
    ) -> int:
        """
        Persist the top-k semantically-ranked chunks from this session into
        the in-process ``AsyncLongTermMemory`` store.

        Called at the end of ``Orchestrator.synthesize()`` so that raw evidence
        that did not rise to the level of a named "claim" is still retained for
        future sessions.

        Parameters
        ----------
        query:
            The original research query — used to rank chunks before persisting.
        top_k:
            Maximum number of chunks to write to long-term memory.

        Returns
        -------
        int
            Number of chunks successfully persisted.
        """
        if self._long_term_memory is None:
            logger.debug(
                "[SearchResultStore] No long-term memory attached; skipping persist"
            )
            return 0

        active_chunks = [c for c in self._chunks if not c.superseded]
        if not active_chunks:
            return 0

        ranked = await self._rank_chunks(query, active_chunks)
        to_persist = ranked[:top_k]

        persisted = 0
        for chunk in to_persist:
            try:
                content = chunk.text
                if chunk.source_url:
                    content = f"[Source: {chunk.source_url}]\n{content}"
                result = await self._long_term_memory.store(
                    content=content,
                    category="raw_evidence",
                    importance=5,
                    tags=["orchestrator", "raw_evidence", f"step_{chunk.step_id}"],
                )
                if result.get("success"):
                    persisted += 1
            except Exception as exc:
                logger.debug(
                    "[SearchResultStore] Failed to persist chunk to memory: %s", exc
                )

        logger.debug(
            "[SearchResultStore] Persisted %d/%d chunks to long-term memory.",
            persisted,
            len(to_persist),
        )
        return persisted

    # ------------------------------------------------------------------
    # Text extraction
    # ------------------------------------------------------------------

    @staticmethod
    def _is_binary(text: str, sample_size: int = 512) -> bool:
        """Return True if *text* is predominantly binary / non-printable content."""
        sample = text[:sample_size]
        non_printable = sum(1 for c in sample if ord(c) < 32 and c not in "\n\r\t")
        return non_printable / max(len(sample), 1) > 0.1

    @staticmethod
    def _extract_text(result: Any) -> str:
        """
        Pull the most useful text out of a raw MCP tool-result dict.

        The file_handler and web_scraper servers wrap content in a list of
        content blocks; the web_search server may return a JSON string or a
        list of snippet dicts.  This method handles the common shapes.
        """
        if not result:
            return ""

        def _clean(text: str) -> str:
            """Strip, binary-check, and return text or empty string."""
            text = text.strip()
            if text and SearchResultStore._is_binary(text):
                logger.debug(
                    "[SearchResultStore] Rejecting binary/non-text content: %.80r",
                    text[:80],
                )
                return ""
            return text

        # MCP tool results typically come back as a list of content blocks
        # e.g. [{"type": "text", "text": "..."}]
        if isinstance(result, list):
            parts: List[str] = []
            for block in result:
                if isinstance(block, dict):
                    text_val = block.get("text") or block.get("content") or ""
                    if isinstance(text_val, str):
                        cleaned = _clean(text_val)
                        if cleaned:
                            parts.append(cleaned)
                elif isinstance(block, str):
                    cleaned = _clean(block)
                    if cleaned:
                        parts.append(cleaned)
            return "\n\n".join(parts)

        if isinstance(result, dict):
            # Some servers return {"text": "..."} or {"content": "..."}
            for key in ("text", "content", "result", "output", "data"):
                val = result.get(key)
                if isinstance(val, str) and val.strip():
                    return _clean(val)
                if isinstance(val, list):
                    return SearchResultStore._extract_text(val)
            # Last resort: JSON serialise the whole dict
            return _json.dumps(result)

        if isinstance(result, str):
            return _clean(result)

        return ""

    # ------------------------------------------------------------------
    # Chunking
    # ------------------------------------------------------------------

    @staticmethod
    def _split_into_chunks(
        text: str,
        chunk_size: int = _CHUNK_SIZE,
        overlap: int = _CHUNK_OVERLAP,
    ) -> List[str]:
        """
        Split *text* into overlapping fixed-size chunks.

        Chunks are split on sentence / paragraph boundaries where possible:
        the splitter first tries to break at double-newlines, then at
        single newlines, then at period+space, and finally falls back to
        hard character boundaries.
        """
        text = text.strip()
        if not text:
            return []
        if len(text) <= chunk_size:
            return [text]

        # Try to split on natural boundaries first
        def _find_break(s: str, max_pos: int) -> int:
            """Return the best break position at or before max_pos."""
            for sep in ("\n\n", "\n", ". ", " "):
                pos = s.rfind(sep, 0, max_pos)
                if pos > max_pos // 2:  # Don't create tiny leading chunks
                    return pos + len(sep)
            return max_pos  # hard break

        chunks: List[str] = []
        start = 0
        while start < len(text):
            end = min(start + chunk_size, len(text))
            if end < len(text):
                end = _find_break(text, end)
            chunk = text[start:end].strip()
            if chunk:
                chunks.append(chunk)
            # Advance with overlap
            start = max(start + 1, end - overlap)

        return chunks

    # ------------------------------------------------------------------
    # Embedding (in-process via embeddings.py)
    # ------------------------------------------------------------------

    async def _embed_chunks(self, chunks: List[Chunk]) -> List[Chunk]:
        """
        Embed all chunks in a single batched call to the in-process
        sentence-transformer model via ``embeddings.embed_texts()``.

        All N chunks cost exactly one ``ThreadPoolExecutor`` submission
        regardless of batch size — no MCP round-trip, no HTTP, no JSON
        parsing.  Falls back to returning chunks without embeddings if
        sentence-transformers is not installed; ``_rank_chunks`` will
        degrade to TF-IDF automatically.
        """
        try:
            texts = [c.text for c in chunks]
            vectors = await embed_texts(texts)
            for chunk, vector in zip(chunks, vectors):
                if vector:
                    chunk.embedding = vector
                    self._embeddings_ready = True
        except EmbeddingError as exc:
            logger.debug(
                "[SearchResultStore] Batch embedding unavailable (%s) — "
                "chunks stored without vectors; TF-IDF will be used for ranking.",
                exc,
            )
        except Exception as exc:
            logger.debug(
                "[SearchResultStore] Unexpected embedding error: %s — "
                "chunks stored without vectors.",
                exc,
            )

        return chunks

    # ------------------------------------------------------------------
    # Ranking
    # ------------------------------------------------------------------

    async def _rank_chunks(self, query: str, pool: List[Chunk]) -> List[Chunk]:
        """
        Rank *pool* chunks by relevance to *query*.

        Two-tier strategy:

        1. **In-process cosine similarity** — embed the query once via
           ``embeddings.embed_query()`` and rank with numpy dot-product
           against stored chunk vectors.  This is the primary path and
           is always available when sentence-transformers is installed.

        2. **Local TF-IDF** — shared numpy fallback from
           ``retrieval_utils``.  Used when embeddings are absent (e.g.
           sentence-transformers not installed, or chunks ingested before
           the model loaded).
        """
        # ── Tier 1: in-process cosine similarity ────────────────────────────
        if any(c.embedding is not None for c in pool):
            try:
                query_vec = await self._embed_query_cached(query)
                if query_vec is not None:
                    return self._cosine_rank(query_vec, pool)
            except Exception as exc:
                logger.debug(
                    "[SearchResultStore] Cosine rank failed (%s); falling back to TF-IDF",
                    exc,
                )

        # ── Tier 2: local TF-IDF (no external dependencies) ─────────────────
        return self._tfidf_rank(query, pool)

    async def _embed_query_cached(self, query: str) -> Optional[List[float]]:
        """
        Embed *query* using the in-process model, with an in-memory cache so
        the same query string is only encoded once per research session.
        """
        if query in self._query_embedding_cache:
            return self._query_embedding_cache[query]

        try:
            vector = await _embed_query_fn(query)
            if vector:
                self._query_embedding_cache[query] = vector
            return vector
        except Exception as exc:
            logger.debug("[SearchResultStore] _embed_query_cached failed: %s", exc)
            return None

    @staticmethod
    def _cosine_rank(query_vec: List[float], pool: List[Chunk]) -> List[Chunk]:
        """
        Rank *pool* by cosine similarity to *query_vec* using stored chunk
        embeddings.  Chunks without stored embeddings are placed at the end
        (score = 0).
        """
        q = np.array(query_vec, dtype=np.float32)
        q_norm = np.linalg.norm(q)
        if q_norm == 0:
            return pool

        q = q / q_norm
        scores: List[float] = []
        for chunk in pool:
            if chunk.embedding is not None:
                v = np.array(chunk.embedding, dtype=np.float32)
                v_norm = np.linalg.norm(v)
                score = float(np.dot(q, v / v_norm)) if v_norm > 0 else 0.0
            else:
                score = 0.0
            scores.append(score)

        ranked_indices = np.argsort(scores)[::-1].tolist()
        return [pool[i] for i in ranked_indices]

    @staticmethod
    def _tfidf_rank(query: str, pool: List[Chunk]) -> List[Chunk]:
        """
        Local TF-IDF cosine similarity ranking using the shared utility.
        Returns *pool* sorted best-first.
        """
        texts = [c.text for c in pool]
        scores = tfidf_similarity(query, texts)
        ranked_indices = sorted(range(len(pool)), key=lambda i: scores[i], reverse=True)
        return [pool[i] for i in ranked_indices]
