"""
pipeline.py

Concurrency control and cross-pipeline deduplication for the
Search → Analyst → QA research pipeline.

The ``PipelineRunner`` is created once per ``Orchestrator.execute()`` call and
shared across all parallel step workers.  It provides:

* **Semaphore** — limits how many step pipelines run their LLM / MCP calls
  concurrently, preventing resource exhaustion.
* **Query deduplication** — an atomic check-and-register set that prevents two
  parallel steps (or QA re-search cycles) from issuing the same web-search
  query twice.

Usage in the Orchestrator
-------------------------
1. ``execute()`` creates a ``PipelineRunner``.
2. Each parallel ``_worker`` acquires the runner's semaphore before iterating
   ``_run_step``.
3. ``_run_step`` calls ``runner.is_query_new(...)`` before issuing any search
   to skip duplicates.
"""

import asyncio
from typing import Set

from config import Config
from observability import get_logger

configs = Config()
logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Recommended defaults (the Orchestrator may override via constructor args)
# ---------------------------------------------------------------------------

# Maximum step pipelines executing their Search→Analyst→QA chain at the same
# time.  Keeps MCP server and LLM API load bounded.
MAX_CONCURRENT_PIPELINES: int = configs.max_workers or 10

# Hard ceiling on QA-triggered re-search cycles *per step*.  The Orchestrator
# also carries its own ``max_qa_retries`` attribute; whichever is lower wins.
MAX_QA_RETRIES: int = configs.max_qa_retries or 2


# ---------------------------------------------------------------------------
# Pipeline runner
# ---------------------------------------------------------------------------


class PipelineRunner:
    """Shared concurrency primitives for parallel step pipelines.

    One instance is created per ``execute()`` invocation and passed to every
    ``_run_step`` call and parallel worker within that invocation.

    Thread-safety note: all operations are ``asyncio.Lock``-protected and safe
    for concurrent use within a single event loop.
    """

    def __init__(self, max_concurrent: int = MAX_CONCURRENT_PIPELINES) -> None:
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self._searched_queries: Set[str] = set()
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Query deduplication
    # ------------------------------------------------------------------

    async def is_query_new(self, query: str) -> bool:
        """Atomically check-and-register a search query.

        Returns ``True`` if *query* has not been seen before (caller should
        proceed with the search).  Returns ``False`` if the query was already
        registered by another pipeline (caller should skip).
        """
        normalized = query.strip().lower()
        if not normalized:
            return True  # empty / whitespace-only → always run
        async with self._lock:
            if normalized in self._searched_queries:
                logger.debug("Skipping duplicate query: %.80s…", normalized)
                return False
            self._searched_queries.add(normalized)
            return True

    @property
    def searched_count(self) -> int:
        """Number of unique queries registered so far."""
        return len(self._searched_queries)
