import os
import time
import json
import hashlib
import asyncio
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import List, Dict, Any, Callable, Optional, AsyncIterator

from models import ResponseMessage
from research_agent import ResearchAgent, Message
from observability import get_logger
from long_term_memory import AsyncLongTermMemory

logger = get_logger(__name__)

# Path to the agent's research-methods knowledge file, resolved relative to
# this module so it works regardless of the working directory.
RESEARCH_METHODS_PATH = os.path.join(
    os.path.dirname(__file__), "instructions", "RESEARCH-METHODS.md"
)


@dataclass
class CacheEntry:
    data: Any
    timestamp: float
    ttl: float

    def is_expired(self) -> bool:
        return time.time() - self.timestamp > self.ttl


class AsyncCache:
    """Simple async cache for expensive operations"""

    def __init__(self, default_ttl: float = 3600):
        self.cache: Dict[str, CacheEntry] = {}
        self.default_ttl = default_ttl

    def _generate_key(self, *args, **kwargs) -> str:
        """Generate cache key from arguments"""
        key_data = json.dumps({"args": args, "kwargs": kwargs}, sort_keys=True)
        return hashlib.md5(key_data.encode()).hexdigest()

    async def get(
        self, key: str, compute_fn: Callable, ttl: Optional[float] = None
    ) -> Any:
        """Get from cache or compute"""
        # Check cache
        if key in self.cache:
            entry = self.cache[key]
            if not entry.is_expired():
                return entry.data
            else:
                del self.cache[key]

        # Compute and cache
        data = await compute_fn()
        self.cache[key] = CacheEntry(
            data=data, timestamp=time.time(), ttl=ttl or self.default_ttl
        )
        return data

    def invalidate(self, key: str):
        """Invalidate cache entry"""
        if key in self.cache:
            del self.cache[key]

    def clear(self):
        """Clear all cache"""
        self.cache.clear()


class BatchProcessor:
    """Process multiple items in batches for efficiency"""

    def __init__(self, batch_size: int = 5, max_concurrent: int = 3):
        self.batch_size = batch_size
        self.max_concurrent = max_concurrent
        self.semaphore = asyncio.Semaphore(max_concurrent)

    async def process_batch(
        self, items: List[Any], process_fn: Callable[[Any], Any]
    ) -> List[Any]:
        """Process items in batches with concurrency control"""
        results = []

        for i in range(0, len(items), self.batch_size):
            batch = items[i : i + self.batch_size]

            async def process_with_semaphore(item):
                async with self.semaphore:
                    return await process_fn(item)

            batch_results = await asyncio.gather(
                *[process_with_semaphore(item) for item in batch],
                return_exceptions=True,
            )

            results.extend(batch_results)

        return results


class OptimizedResearchAgent:
    """Research agent with performance optimizations"""

    def __init__(
        self,
        model_backend,
        mcp_registry,
        enable_cache: bool = True,
        enable_batching: bool = True,
    ):
        self.model_backend = model_backend
        self.mcp_registry = mcp_registry
        self.enable_cache = enable_cache
        self.enable_batching = enable_batching

        if enable_cache:
            self.cache = AsyncCache(default_ttl=3600)

        if enable_batching:
            self.batch_processor = BatchProcessor(batch_size=5, max_concurrent=3)

    async def search_with_cache(
        self, query: str, max_results: int = 5
    ) -> List[Dict[str, Any]]:
        """Search with caching"""
        if not self.enable_cache:
            return await self._search(query, max_results)

        cache_key = f"search_{query}_{max_results}"

        async def compute():
            return await self._search(query, max_results)

        return await self.cache.get(cache_key, compute, ttl=1800)

    async def _search(self, query: str, max_results: int) -> List[Dict[str, Any]]:
        """Actual search implementation"""
        search_client = self.mcp_registry.get("search")
        if not search_client:
            return []

        result = await search_client.call_tool(
            "search", {"query": query, "max_results": max_results}
        )
        return result.get("results", [])

    async def scrape_urls_parallel(self, urls: List[str]) -> List[Dict[str, Any]]:
        """Scrape multiple URLs in parallel with batching"""
        if not self.enable_batching:
            return await asyncio.gather(*[self._scrape_url(url) for url in urls])

        async def scrape_fn(url):
            return await self._scrape_url(url)

        return await self.batch_processor.process_batch(urls, scrape_fn)

    async def _scrape_url(self, url: str) -> Dict[str, Any]:
        """Scrape a single URL"""
        scraper_client = self.mcp_registry.get("scraper")
        if not scraper_client:
            return {"url": url, "content": "", "error": "No scraper available"}

        try:
            result = await scraper_client.call_tool("scrape", {"url": url})
            return result
        except Exception as e:
            return {"url": url, "content": "", "error": str(e)}

    async def prefetch_common_queries(self, queries: List[str]):
        """Prefetch and cache common queries"""
        if not self.enable_cache:
            return

        tasks = [self.search_with_cache(query) for query in queries]
        await asyncio.gather(*tasks, return_exceptions=True)


class SelfOptimizingAgent(ResearchAgent):
    """Agent that examines its memories to develop improved research methods.

    The primary workflow (``self_optimize``) follows five phases:
      1. Read the current RESEARCH-METHODS.md as baseline context.
      2. Retrieve all stored memories from the memory MCP server.
      3. Analyse the memories (with the current methods as context) to surface
         patterns, bottlenecks, and opportunities.
      4. Develop concrete new / updated research method descriptions.
      5. Rewrite RESEARCH-METHODS.md to incorporate the improvements, then
         persist the new methods to the memory server.

    Each phase emits an ``optimize_progress`` WebSocket event so the frontend
    can track progress in the graph view.  The final event is
    ``optimize_complete``.
    """

    def __init__(
        self,
        model_backend,
        mcp_registry,
        long_term_memory: Optional[AsyncLongTermMemory] = None,
    ):
        super().__init__(model_backend, mcp_registry)
        self._long_term_memory = long_term_memory

    # ── File I/O helpers ──────────────────────────────────────────────────────

    def _read_research_methods(self) -> str:
        """Return the current content of RESEARCH-METHODS.md, or '' if absent."""
        try:
            with open(RESEARCH_METHODS_PATH, "r", encoding="utf-8") as fh:
                return fh.read()
        except FileNotFoundError:
            logger.warning(
                "RESEARCH-METHODS.md not found at %s — starting from blank.",
                RESEARCH_METHODS_PATH,
            )
            return ""
        except OSError as exc:
            logger.error("Error reading RESEARCH-METHODS.md: %s", exc)
            return ""

    def _write_research_methods(self, content: str) -> None:
        """Write *content* to RESEARCH-METHODS.md, creating the file if needed."""
        os.makedirs(os.path.dirname(RESEARCH_METHODS_PATH), exist_ok=True)
        with open(RESEARCH_METHODS_PATH, "w", encoding="utf-8") as fh:
            fh.write(content)
        logger.info("RESEARCH-METHODS.md updated (%d chars).", len(content))

    # ── LLM helpers ───────────────────────────────────────────────────────────

    async def _analyze_memories(self, memories: dict, current_methods: str) -> str:
        """Analyse stored memories alongside the current research methods."""
        system_prompt = (
            "You are a research-methodology analyst. Your task is to review an "
            "agent's stored memories from past research sessions and the agent's "
            "current documented research methods, then identify:\n"
            "  • Recurring success patterns worth reinforcing.\n"
            "  • Systematic failure modes or inefficiencies to eliminate.\n"
            "  • Gaps in the documented methods that past sessions revealed.\n"
            "  • Emerging strategies that appear effective but are not yet documented.\n\n"
            "If a knowledge graph section is provided, pay special attention to:\n"
            "  • Entity clusters that appear repeatedly across sessions (high mention counts).\n"
            "  • Relationship patterns that reveal structural strengths or weaknesses in research coverage.\n"
            "  • Thematic clusters that suggest the agent has deep expertise vs. shallow coverage.\n\n"
            "Be specific and actionable. Avoid vague generalities."
        )
        methods_section = (
            f"\n\n## Current Research Methods\n{current_methods}"
            if current_methods
            else ""
        )

        # Build memory sections — separate flat entries from graph context
        memory_parts: List[str] = []
        graph_context = memories.pop("knowledge_graph", None)
        graph_stats = memories.pop("graph_stats", None)
        if memories:
            memory_parts.append("## Stored Memories\n" + json.dumps(memories, indent=2))
        if graph_context:
            stats_line = ""
            if graph_stats:
                stats_line = (
                    f"\n(Graph contains {graph_stats.get('entities', 0)} entities, "
                    f"{graph_stats.get('relationships', 0)} relationships, "
                    f"{graph_stats.get('communities', 0)} communities)"
                )
            memory_parts.append(f"## Knowledge Graph{stats_line}\n{graph_context}")

        user_prompt = (
            "Analyze the following memories from past research sessions"
            + methods_section
            + "\n\n"
            + "\n\n".join(memory_parts)
        )

        model = self._select_model_for_step("analyze memories")
        messages = [
            Message(role="system", content=system_prompt),
            Message(role="user", content=user_prompt),
        ]
        response = await self.model.generate(model, messages)
        return response.content

    async def _develop_new_research_methods(self, insights: str) -> List[str]:
        """Derive actionable new research method descriptions from the insights."""
        system_prompt = (
            "You are a research-methodology designer. Given analytical insights "
            "about past performance, produce a JSON array of concise, actionable "
            "research method descriptions. Each description should state WHAT to "
            "do and WHY it improves outcomes. Focus on methods novel enough to "
            "warrant addition to the documented playbook — do not restate methods "
            "that already exist.\n\n"
            "Return ONLY a valid JSON array of strings. Example:\n"
            '["Method: Before issuing parallel search steps, draft 3–5 '
            "alternative phrasings of each sub-question and pick the 2 most "
            'distinct. This reduces redundant results across parallel workers."]'
        )
        user_prompt = (
            "Based on the following insights, develop new research methods:\n\n"
            + insights
        )

        model = self._select_model_for_step("develop new research methods")
        messages = [
            Message(role="system", content=system_prompt),
            Message(role="user", content=user_prompt),
        ]
        response = await self.model.generate(model, messages)
        try:
            content = response.content.strip()
            if content.startswith("```"):
                content = content.split("\n", 1)[1].rsplit("```", 1)[0].strip()
            methods = json.loads(content)
            if isinstance(methods, list) and all(isinstance(m, str) for m in methods):
                return methods
            logger.warning(
                "Unexpected format for new research methods: %s", response.content
            )
            return []
        except json.JSONDecodeError:
            logger.warning(
                "Failed to parse new research methods as JSON: %s", response.content
            )
            return []

    async def _generate_updated_methods_doc(
        self,
        current_methods: str,
        insights: str,
        new_methods: List[str],
    ) -> str:
        """Ask the LLM to rewrite RESEARCH-METHODS.md incorporating the new findings."""
        system_prompt = (
            "You are a technical writer maintaining a research-methodology playbook. "
            "You will receive:\n"
            "  1. The current playbook content.\n"
            "  2. Analytical insights from recent research sessions.\n"
            "  3. A list of newly developed method descriptions.\n\n"
            "Produce an updated version of the playbook that:\n"
            "  • Retains all valuable existing content.\n"
            "  • Integrates the new methods naturally into the appropriate sections.\n"
            "  • Appends a dated entry to the '## Optimization Log' section.\n"
            "  • Does NOT remove the standard structural headers.\n"
            "Return the complete updated Markdown document — nothing else."
        )
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        user_prompt = (
            f"## Current RESEARCH-METHODS.md\n{current_methods}\n\n"
            f"## Insights from recent sessions\n{insights}\n\n"
            f"## New methods to integrate\n"
            + "\n".join(f"- {m}" for m in new_methods)
            + f"\n\nToday's date for the optimization log entry: {today}"
        )

        model = self._select_model_for_step("update research methods document")
        messages = [
            Message(role="system", content=system_prompt),
            Message(role="user", content=user_prompt),
        ]
        response = await self.model.generate(model, messages)
        return response.content

    # ── Memory helpers ────────────────────────────────────────────────────────

    async def get_all_memories(self) -> dict:
        if self._long_term_memory is None or not self._long_term_memory._available:
            logger.warning(
                "[SelfOptimizingAgent] Long-term memory not available — memory retrieval skipped."
            )
            return {}

        result: Dict[str, Any] = {}

        # Flat memory entries (raw evidence + claims)
        memories = await self._long_term_memory.recall(limit=100)
        if memories:
            result["entries"] = memories

        # Knowledge graph context (entities, relationships, communities)
        graph = self._long_term_memory.graph
        if graph.available:
            graph_stats = await graph.stats()
            if graph_stats.get("entities", 0) > 0:
                # Retrieve all entities via a broad query
                graph_context = await graph.recall_graph_context(
                    "research methods patterns strategies effectiveness",
                    entity_limit=20,
                    max_hops=2,
                )
                if graph_context:
                    result["knowledge_graph"] = graph_context
                result["graph_stats"] = graph_stats

        if not result:
            logger.info("[SelfOptimizingAgent] No memories found in long-term store.")
            return {}
        return result

    # ── Main workflow ─────────────────────────────────────────────────────────

    async def self_optimize(self) -> AsyncIterator[ResponseMessage]:
        """Run the five-phase self-optimization workflow.

        Emits ``optimize_progress`` events (with a ``phase`` field in ``data``)
        as each phase begins and completes, then a final ``optimize_complete``
        event carrying the updated methods document.
        """
        # ── Phase 1: read current research methods ────────────────────────────
        yield ResponseMessage(
            type="optimize_progress",
            message="Reading current research methods…",
            data={"phase": "read_methods", "status": "running"},
        )
        current_methods = self._read_research_methods()
        yield ResponseMessage(
            type="optimize_progress",
            message=f"Research methods loaded ({len(current_methods)} chars).",
            data={"phase": "read_methods", "status": "completed"},
        )

        # ── Phase 2: retrieve memories ────────────────────────────────────────
        yield ResponseMessage(
            type="optimize_progress",
            message="Retrieving memories from prior research sessions…",
            data={"phase": "retrieve_memories", "status": "running"},
        )
        all_memories = await self.get_all_memories()
        yield ResponseMessage(
            type="optimize_progress",
            message=f"Retrieved {len(all_memories)} memory entries.",
            data={"phase": "retrieve_memories", "status": "completed"},
        )

        # ── Phase 3: analyse patterns ─────────────────────────────────────────
        yield ResponseMessage(
            type="optimize_progress",
            message="Analysing patterns and identifying improvement opportunities…",
            data={"phase": "analyze", "status": "running"},
        )
        insights = await self._analyze_memories(all_memories, current_methods)
        if not insights:
            yield ResponseMessage(
                type="optimize_progress",
                message="No significant insights found from memory analysis.",
                data={"phase": "analyze", "status": "completed"},
            )
        else:
            yield ResponseMessage(
                type="optimize_progress",
                message="Pattern analysis complete.",
                data={"phase": "analyze", "status": "completed", "insights": insights},
            )

        # ── Phase 4: develop new methods ──────────────────────────────────────
        yield ResponseMessage(
            type="optimize_progress",
            message="Developing new research method recommendations…",
            data={"phase": "develop", "status": "running"},
        )
        new_methods = await self._develop_new_research_methods(insights)
        if not new_methods:
            yield ResponseMessage(
                type="optimize_progress",
                message="No new research methods generated.",
                data={"phase": "develop", "status": "completed"},
            )
            # Still proceed to update the log even with no new methods
            new_methods = []
        else:
            yield ResponseMessage(
                type="optimize_progress",
                message=f"Developed {len(new_methods)} new research method(s).",
                data={
                    "phase": "develop",
                    "status": "completed",
                    "new_methods": new_methods,
                },
            )

        # ── Phase 5: update RESEARCH-METHODS.md ──────────────────────────────
        yield ResponseMessage(
            type="optimize_progress",
            message="Updating RESEARCH-METHODS.md with new insights…",
            data={"phase": "update_methods", "status": "running"},
        )
        updated_doc = await self._generate_updated_methods_doc(
            current_methods, insights, new_methods
        )
        try:
            self._write_research_methods(updated_doc)
        except OSError as exc:
            logger.error("Failed to write RESEARCH-METHODS.md: %s", exc)
            yield ResponseMessage(
                type="error",
                message=f"Failed to write updated research methods: {exc}",
            )
            return

        # Persist optimization insights to long-term memory for future sessions
        if self._long_term_memory and new_methods:
            try:
                content = (
                    f"Self-optimization ({datetime.now(timezone.utc).strftime('%Y-%m-%d')}):\n"
                    f"Insights: {insights[:500]}\n"
                    "New methods:\n" + "\n".join(f"- {m}" for m in new_methods)
                )
                await self._long_term_memory.store(
                    content=content,
                    category="optimization_insight",
                    importance=8,
                    tags=["self_optimize", "methods"],
                )
            except Exception as exc:
                logger.warning(
                    "Could not persist optimization insight to memory: %s", exc
                )

        yield ResponseMessage(
            type="optimize_progress",
            message="RESEARCH-METHODS.md updated successfully.",
            data={"phase": "update_methods", "status": "completed"},
        )

        yield ResponseMessage(
            type="optimize_complete",
            message=(
                f"Self-optimization complete. "
                f"{len(new_methods)} new method(s) integrated into RESEARCH-METHODS.md."
            ),
            data={
                "new_methods_count": len(new_methods),
                "updated_methods": updated_doc,
            },
        )


class ConnectionPool:
    """Connection pool for HTTP requests"""

    def __init__(self, max_connections: int = 10):
        self.max_connections = max_connections
        self._session = None

    async def __aenter__(self):
        import aiohttp

        connector = aiohttp.TCPConnector(limit=self.max_connections)
        self._session = aiohttp.ClientSession(connector=connector)
        return self._session

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._session:
            await self._session.close()
