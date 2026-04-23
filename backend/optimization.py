import os
import json
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional, AsyncIterator

from models import ResponseMessage
from research_agent import ResearchAgent
from model_backend import Message
from observability import get_logger
from long_term_memory import AsyncLongTermMemory

logger = get_logger(__name__)

# Path to the agent's research-methods knowledge file, resolved relative to
# this module so it works regardless of the working directory.
RESEARCH_METHODS_PATH = os.path.join(
    os.path.dirname(__file__), "instructions", "RESEARCH-METHODS.md"
)


class SelfOptimizingAgent(ResearchAgent):
    """Agent that examines its memories to develop improved research methods.

    The primary workflow (``self_optimize``) follows six phases:
      1. Read the current RESEARCH-METHODS.md as baseline context.
      2. Retrieve all stored memories from the memory MCP server.
      3. Analyse the memories (with the current methods as context) to surface
         patterns, bottlenecks, and opportunities.
      4. Develop concrete new / updated research method descriptions.
      5. Rewrite RESEARCH-METHODS.md to incorporate the improvements, then
         persist the new methods to the memory server.
      6. (Conditional) Prune stale or low-value nodes from the knowledge graph
         — only if a dry-run reveals candidates AND the LLM determines that
         removal is warranted without sacrificing data quality.

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

    async def _assess_pruning_need(
        self,
        graph_stats: Dict[str, int],
        dry_run_results: Dict[str, int],
        insights: str,
    ) -> Dict[str, Any]:
        """Ask the LLM whether pruning the knowledge graph is warranted.

        Returns a dict with keys:
          - ``should_prune`` (bool): whether to proceed with actual pruning.
          - ``min_confidence`` (float): confidence threshold, clamped to [0.05, 0.5].
          - ``max_age_days`` (int): age threshold, clamped to [30, 365].
          - ``reasoning`` (str): brief justification.

        On any parse / LLM failure defaults to no-prune (safety-first).
        """
        _no_prune: Dict[str, Any] = {
            "should_prune": False,
            "min_confidence": 0.1,
            "max_age_days": 180,
            "reasoning": "Defaulting to no-prune due to assessment error.",
        }

        system_prompt = (
            "You are a knowledge-graph curator for a research agent. "
            "Your job is to decide whether the agent's knowledge graph should "
            "be pruned of stale or low-value data.\n\n"
            "You will receive:\n"
            "  1. Current graph statistics (entity/relationship counts).\n"
            "  2. A dry-run report: how many relationships, entities, dangling "
            "contradiction edges, and orphaned claims WOULD be removed under "
            "default thresholds (min_confidence=0.1, max_age_days=180).\n"
            "  3. Insights from the latest research-session analysis.\n\n"
            "Guiding principles:\n"
            "  • Be CONSERVATIVE. The graph is a long-term asset. Only recommend "
            "pruning when the candidates are clearly low-value noise.\n"
            "  • Consider the ratio of pruneable items to total graph size. "
            "If the dry-run would remove >30% of any category, be skeptical.\n"
            "  • If the graph is small (< 20 entities) prefer to keep everything.\n"
            "  • You may suggest tighter thresholds than the defaults to be safer "
            "(e.g. higher min_confidence or shorter max_age_days).\n"
            "  • If in doubt, do NOT prune.\n\n"
            "Return ONLY valid JSON with exactly these keys:\n"
            '{"should_prune": <bool>, "min_confidence": <float 0.05-0.5>, '
            '"max_age_days": <int 30-365>, "reasoning": "<one sentence>"}'
        )
        total_pruneable = sum(dry_run_results.values())
        user_prompt = (
            f"## Graph statistics\n{json.dumps(graph_stats, indent=2)}\n\n"
            f"## Dry-run pruning candidates\n{json.dumps(dry_run_results, indent=2)}\n"
            f"(Total pruneable items: {total_pruneable})\n\n"
            f"## Latest research-session insights\n{insights[:1000]}"
        )

        try:
            model = self._select_model_for_step("assess pruning need")
            messages = [
                Message(role="system", content=system_prompt),
                Message(role="user", content=user_prompt),
            ]
            response = await self.model.generate(model, messages)
            content = response.content.strip()
            if content.startswith("```"):
                content = content.split("\n", 1)[1].rsplit("```", 1)[0].strip()
            decision = json.loads(content)
            # Validate and clamp thresholds to safe bounds
            should_prune = bool(decision.get("should_prune", False))
            min_confidence = float(decision.get("min_confidence", 0.1))
            max_age_days = int(decision.get("max_age_days", 180))
            min_confidence = max(0.05, min(0.5, min_confidence))
            max_age_days = max(30, min(365, max_age_days))
            return {
                "should_prune": should_prune,
                "min_confidence": min_confidence,
                "max_age_days": max_age_days,
                "reasoning": str(decision.get("reasoning", "")),
            }
        except Exception as exc:
            logger.warning(
                "[SelfOptimizingAgent] Pruning assessment failed (%s) — defaulting to no-prune.",
                exc,
            )
            return _no_prune

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

        # ── Phase 3: analyze patterns ─────────────────────────────────────────
        yield ResponseMessage(
            type="optimize_progress",
            message="Analyzing patterns and identifying improvement opportunities…",
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

        # ── Phase 6: prune knowledge graph (conditional) ──────────────────────
        prune_results: Dict[str, Any] = {"skipped": True}
        graph = (
            self._long_term_memory.graph
            if self._long_term_memory and self._long_term_memory._available
            else None
        )
        if graph is None or not graph.available:
            yield ResponseMessage(
                type="optimize_progress",
                message="Knowledge graph unavailable — pruning phase skipped.",
                data={"phase": "prune_graph", "status": "skipped"},
            )
        else:
            yield ResponseMessage(
                type="optimize_progress",
                message="Surveying knowledge graph for stale or low-value data…",
                data={"phase": "prune_graph", "status": "running"},
            )
            try:
                graph_stats = await graph.stats()
                dry_run = await graph.prune(
                    min_confidence=0.1, max_age_days=180, dry_run=True
                )
                total_pruneable = sum(dry_run.values())

                if total_pruneable == 0:
                    prune_results = {
                        "skipped": False,
                        "pruned": False,
                        "reason": "No stale data detected.",
                    }
                    yield ResponseMessage(
                        type="optimize_progress",
                        message="Knowledge graph is clean — no stale data detected.",
                        data={
                            "phase": "prune_graph",
                            "status": "completed",
                            **prune_results,
                        },
                    )
                else:
                    decision = await self._assess_pruning_need(
                        graph_stats, dry_run, insights
                    )
                    if not decision["should_prune"]:
                        prune_results = {
                            "skipped": False,
                            "pruned": False,
                            "reason": decision["reasoning"],
                            "dry_run_candidates": dry_run,
                        }
                        yield ResponseMessage(
                            type="optimize_progress",
                            message=f"Pruning deferred: {decision['reasoning']}",
                            data={
                                "phase": "prune_graph",
                                "status": "completed",
                                **prune_results,
                            },
                        )
                    else:
                        actual = await graph.prune(
                            min_confidence=decision["min_confidence"],
                            max_age_days=decision["max_age_days"],
                            dry_run=False,
                        )
                        prune_results = {
                            "skipped": False,
                            "pruned": True,
                            "removed": actual,
                            "reasoning": decision["reasoning"],
                        }
                        removed_total = sum(actual.values())
                        yield ResponseMessage(
                            type="optimize_progress",
                            message=(
                                f"Knowledge graph pruned: {removed_total} item(s) removed "
                                f"(relationships={actual.get('relationships', 0)}, "
                                f"entities={actual.get('entities', 0)}, "
                                f"claims={actual.get('claims', 0)})."
                            ),
                            data={
                                "phase": "prune_graph",
                                "status": "completed",
                                **prune_results,
                            },
                        )
            except Exception as exc:
                logger.error("[SelfOptimizingAgent] Pruning phase failed: %s", exc)
                prune_results = {"skipped": False, "pruned": False, "error": str(exc)}
                yield ResponseMessage(
                    type="optimize_progress",
                    message=f"Pruning phase encountered an error and was skipped: {exc}",
                    data={"phase": "prune_graph", "status": "failed"},
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
                "prune_results": prune_results,
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
