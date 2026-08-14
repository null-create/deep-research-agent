"""
orchestrator.py

Defines the Orchestrator and its five specialist sub-agents.

Agent roles
-----------
Root (Orchestrator)
    High-reasoning planner. Receives the user prompt, identifies knowledge gaps,
    and produces a high-level research plan that the sub-agents execute.

SearchAgent
    Optimized for high-recall web navigation.  Bypasses SEO-spam layers to
    surface raw, primary-source data.

AnalystAgent
    Trained for data extraction and cross-referencing across disparate sources.
    Resolves ambiguity by triangulating evidence.

LoopAgent  (Quality-Assurance / Critic)
    Sole responsibility: critique every finding.  If Source A says X and
    Source B says Y, it flags the contradiction and routes it back for further
    investigation before synthesis proceeds.

ReportComposer
    Terminal specialist.  Synthesizes vetted findings, surfaces new insights and
    actionable next steps, and produces a professional research document.
"""

import json
import asyncio
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, AsyncIterator, Dict, List, Optional, TYPE_CHECKING

from config import Config
from context import ResearchContext
from mcp_client import MCPServerRegistry, create_mcp_registry
from pipeline import PipelineRunner, MAX_CONCURRENT_PIPELINES
from search_result_store import SearchResultStore
from model_backend import (
    ModelBackend,
    AnthropicBackend,
    AzureOpenAIBackend,
    AWSOpenAIBackend,
    GCPVertexAIBackend,
    HuggingFaceBackend,
    OllamaBackend,
    OpenAIBackend,
    create_model_backend,
)
from models import (
    Message,
    ModelResponse,
    ResearchPlan,
    ResearchStep,
    ResponseMessage,
    StepStatus,
)

if TYPE_CHECKING:
    from long_term_memory import AsyncLongTermMemory

from observability import get_logger, record_event

# ---------------------------------------------------------------------------
# Global config and logger setup
# ---------------------------------------------------------------------------
config = Config()
logger = get_logger(__name__)

# Path to the research-methods playbook.  Resolved against this file's
# directory so it works regardless of the working directory.
_RESEARCH_METHODS_PATH = os.path.join(
    os.path.dirname(__file__), "instructions", "RESEARCH-METHODS.md"
)


def _load_research_methods() -> str:
    """Read RESEARCH-METHODS.md and return its content, or '' on any error."""
    try:
        with open(_RESEARCH_METHODS_PATH, "r", encoding="utf-8") as fh:
            return fh.read()
    except FileNotFoundError:
        logger.error("RESEARCH-METHODS.md not found at %s", _RESEARCH_METHODS_PATH)
        return ""
    except OSError as e:
        logger.error(
            "Error reading RESEARCH-METHODS.md at %s: %s", _RESEARCH_METHODS_PATH, e
        )
        return ""


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class AgentRole(str, Enum):
    """Canonical identifiers for each agent persona."""

    ROOT = "root"
    SEARCH = "search"
    ANALYST = "analyst"
    LOOP = "loop"
    REPORT = "report"


class AgentStatus(str, Enum):
    """Lifecycle state of a sub-agent instance."""

    IDLE = "idle"
    RUNNING = "running"
    WAITING = "waiting"  # blocked on another agent's output
    DONE = "done"
    ERROR = "error"


# ---------------------------------------------------------------------------
# Contradiction / flag raised by the LoopAgent
# ---------------------------------------------------------------------------


@dataclass
class Contradiction:
    """A factual conflict that the LoopAgent has flagged for resolution."""

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    source_a: str = ""
    claim_a: str = ""
    source_b: str = ""
    claim_b: str = ""
    context: str = ""
    resolved: bool = False
    resolution: Optional[str] = None
    flagged_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    # Specific search query the LoopAgent recommends to resolve this contradiction
    targeted_query: Optional[str] = None
    # Classification: "factual_error" | "temporal_mismatch" | "source_disagreement"
    # | "insufficient_evidence" | "unknown"
    # Only "factual_error" and "temporal_mismatch" trigger targeted re-search.
    # "source_disagreement" is passed through as an analyst tension (it is a
    # research finding, not a pipeline bug).  "insufficient_evidence" is added
    # to analyst recommendations for subsequent steps.
    contradiction_type: str = "unknown"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "source_a": self.source_a,
            "claim_a": self.claim_a,
            "source_b": self.source_b,
            "claim_b": self.claim_b,
            "context": self.context,
            "resolved": self.resolved,
            "resolution": self.resolution,
            "flagged_at": self.flagged_at.isoformat(),
            "targeted_query": self.targeted_query,
            "contradiction_type": self.contradiction_type,
        }


@dataclass
class AuditResult:
    """Structured output returned by ``LoopAgent.audit()``."""

    contradictions: List[Contradiction]
    verdict: str  # "clean" | "flagged"
    notes: str = ""


# ---------------------------------------------------------------------------
# Base sub-agent
# ---------------------------------------------------------------------------


class SubAgent:
    """
    Lightweight wrapper that pairs an agent *role* with a model backend and an
    optional dedicated system prompt.  Sub-agents do not own long-lived state;
    the Orchestrator manages all shared context.
    """

    def __init__(
        self,
        role: AgentRole,
        model_backend: ModelBackend,
        mcp_servers: MCPServerRegistry,
        system_prompt: str,
        agent_id: Optional[str] = None,
    ) -> None:
        self.role = role
        self.model = model_backend
        self.mcp_servers = mcp_servers
        self.system_prompt = system_prompt
        self.agent_id: str = agent_id or str(uuid.uuid4())
        self.status: AgentStatus = AgentStatus.IDLE

        model_name = (
            model_backend.model if hasattr(model_backend, "model") else "unknown-model"
        )
        logger.debug(
            "Initialized %s agent with model %s and id %s",
            self.role,
            model_name,
            self.agent_id,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_messages(
        self, user_content: str, extra: Optional[List[Message]] = None
    ) -> List[Message]:
        msgs: List[Message] = [
            Message(role="system", content=self.system_prompt),
            Message(role="user", content=user_content),
        ]
        if extra:
            msgs.extend(extra)
        return msgs

    async def run(
        self,
        prompt: str,
        model_override: Optional[str] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        extra_messages: Optional[List[Message]] = None,
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> ModelResponse:
        """
        Send *prompt* to the underlying model and return the raw ModelResponse.

        Parameters
        ----------
        prompt:
            The task description or question for this agent.
        model_override:
            Optional model name to use instead of the backend default.
        tools:
            MCP tool specs to expose during this call.
        extra_messages:
            Additional conversation turns to inject between the system prompt
            and the user turn (e.g. prior tool results).
        temperature:
            Optional sampling temperature override.
        top_p:
            Optional nucleus-sampling top-p override.
        max_tokens:
            Optional max-tokens override.
        """
        from model_backend import TEMPERATURE, MAX_TOKENS

        self.status = AgentStatus.RUNNING
        try:
            messages = self._build_messages(prompt, extra_messages)
            generate_kwargs: Dict[str, Any] = {
                "model": model_override,
                "messages": messages,
                "tools": tools or [],
            }
            # temperature and max_tokens are accepted by all backends;
            # top_p is stored in config for future backend support.
            if temperature is not None:
                generate_kwargs["temperature"] = temperature
            if max_tokens is not None:
                generate_kwargs["max_tokens"] = max_tokens
            response = await self.model.generate(**generate_kwargs)
            self.status = AgentStatus.DONE
            return response
        except Exception as exc:
            self.status = AgentStatus.ERROR
            logger.exception("[%s] run() failed: %s", self.role.value, exc)
            return ModelResponse(
                content=str(exc), tool_calls=None, finish_reason="error"
            )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<SubAgent role={self.role.value} id={self.agent_id[:8]} status={self.status.value}>"


# ---------------------------------------------------------------------------
# Concrete sub-agents
# ---------------------------------------------------------------------------


class SearchAgent(SubAgent):
    """
    Optimized for high-recall web navigation.

    Strategy
    --------
    * Prefers broad, diversified queries over narrow keyword matching.
    * Explicitly instructed to discount SEO-heavy, low-signal pages.
    * Returns structured JSON with ``{"sources": [...], "raw_data": "..."}`` so
      the AnalystAgent can ingest results without extra parsing.
    """

    _DEFAULT_SYSTEM_PROMPT = """\
You are a specialist web-research agent optimized for HIGH RECALL and HIGH PRECISION.

Your workflow for EVERY source you retrieve:

STEP 1 — RELEVANCE PASS (internal filter)
  Ask yourself: "Does this source directly answer my assigned sub-question?"
  If NO → discard the source, do not include it in output.
  If YES → continue to Step 2.

STEP 2 — KNOWLEDGE SNIPPET EXTRACTION
  From the relevant source, extract ONLY the specific facts that answer
  the sub-question.  Condense them into a high-density bulleted snippet
  of 3–7 bullet points.  Each bullet must be a discrete, verifiable fact.
  Target: 150–300 tokens per snippet.  Do NOT paraphrase vaguely — be
  precise and cite numbers, dates, names, and quotes where available.

STEP 3 — SOURCE RECORD
  Record the URL, a concise title, and your knowledge snippet.
  The raw excerpt is stored separately by the system; you do NOT need to
  reproduce it verbatim in your output.

Rules:
- You have real-time web search and scraping tools available to you.  You MUST
  use them to retrieve current information — never answer from training data
  alone.  If a topic relates to recent or ongoing events, SEARCH for it
  regardless of your training cutoff date.
- Prefer primary sources: academic papers, official documentation, government
  data, reputable journalism, and direct expert testimony.
- Actively bypass SEO-spam, link farms, and thin content pages.
- Never paraphrase or editorialize beyond the snippet.

Return a JSON object:
{
  "sources": [
    {
      "url": "...",
      "title": "...",
      "knowledge_snippet": "• Fact 1\\n• Fact 2\\n• Fact 3"
    },
    ...
  ],
  "coverage_notes": "Brief note on any obvious gaps in coverage."
}
"""

    def __init__(
        self,
        model_backend: ModelBackend,
        mcp_servers: MCPServerRegistry,
        agent_id: Optional[str] = None,
    ) -> None:
        super().__init__(
            role=AgentRole.SEARCH,
            model_backend=model_backend,
            mcp_servers=mcp_servers,
            system_prompt=self._DEFAULT_SYSTEM_PROMPT,
            agent_id=agent_id,
        )


class AnalystAgent(SubAgent):
    """
    Trained for data extraction and cross-referencing across disparate sources.

    Strategy
    --------
    * Receives raw source data from one or more SearchAgent runs.
    * Extracts structured claims and tags each with a provenance reference.
    * Identifies corroborating evidence across sources.
    * Surfaces tensions or ambiguities *without* resolving them — resolution is
      the LoopAgent's responsibility.
    * Does NOT make coverage-gating decisions; that authority belongs to the
      LoopAgent, which owns the retry loop.
    """

    _DEFAULT_SYSTEM_PROMPT = """\
You are a specialist data-analyst agent trained for rigorous information
extraction and cross-source triangulation.

Your goals:
1. Parse every source provided and extract discrete factual claims.
2. For each claim, record the originating source URL.
3. Group claims by topic and note which sources agree or disagree.
4. Flag any internal inconsistencies you notice, but do NOT resolve them.
5. Produce a structured analysis that the QA agent can audit.
6. For EVERY claim, set "confidence" based on source corroboration:
   - "corroborated": 2+ independent sources confirm the claim.
   - "partially_corroborated": sources partially agree, or one is lower-quality.
   - "single_source": only one source supports this claim.

Return a JSON object:
{
  "claims": [
    {
      "claim": "...",
      "sources": ["url1", "url2"],
      "confidence": "corroborated" | "partially_corroborated" | "single_source"
    },
    ...
  ],
  "tensions": [
    {
      "topic": "...",
      "source_a": "url1",
      "position_a": "...",
      "source_b": "url2",
      "position_b": "..."
    },
    ...
  ],
  "analyst_notes": "..."
}
"""

    def __init__(
        self,
        model_backend: ModelBackend,
        mcp_servers: MCPServerRegistry,
        agent_id: Optional[str] = None,
    ) -> None:
        super().__init__(
            role=AgentRole.ANALYST,
            model_backend=model_backend,
            mcp_servers=mcp_servers,
            system_prompt=self._DEFAULT_SYSTEM_PROMPT,
            agent_id=agent_id,
        )


class LoopAgent(SubAgent):
    """
    Quality-Assurance / Critic agent and retry gatekeeper.

    Two responsibilities:
    1. Find contradictions between sources and flag them.
    2. For each contradiction, generate a ``targeted_query`` — a precise search
       instruction that the SearchAgent can execute to find a primary source
       capable of settling the dispute.

    The Orchestrator's retry loop is *entirely* driven by this agent's verdict:
    ``"clean"`` means proceed to synthesis; ``"flagged"`` means re-investigate
    using the provided targeted queries before synthesis proceeds.
    """

    _DEFAULT_SYSTEM_PROMPT = """\
You are a Quality-Assurance critic agent and retry gatekeeper.

Your responsibilities:
1. Find contradictions and inconsistencies in the provided research findings.
2. Classify EVERY contradiction into exactly one of the four types below.
3. For contradictions of type "factual_error" or "temporal_mismatch", produce
   a PRECISE targeted_query — a specific search instruction that a web-search
   agent can use to find a primary source that settles the disputed fact
   (e.g. "Official WHO data on X as of 2024").
4. Do not generate new information, opinions, summaries, or synthesis.
5. Quote conflicting claims verbatim (or as close as possible).
6. If you find NO contradictions, set verdict to "clean" and return an empty
   contradictions list.

Contradiction types:
- "factual_error": A specific, objectively verifiable fact is stated differently
  across sources (e.g., different numeric values, conflicting dates, wrong names).
  These CAN be resolved by finding a primary source. ALWAYS provide targeted_query.
- "temporal_mismatch": Sources from different time periods make incompatible claims
  that were each true at the time but conflict when mixed (e.g., 2024 stats cited
  as 2026 evidence). Provide a targeted_query scoped to the correct time period.
- "source_disagreement": Sources genuinely assess the same situation differently
  based on differing perspectives, methodologies, or criteria (e.g., one calls a
  technology "production-ready", another calls it "experimental"). This is a
  RESEARCH FINDING, not a pipeline error — do not provide targeted_query for these.
- "insufficient_evidence": A claim lacks any corroborating source but is not
  directly contradicted. Leave targeted_query as null.

Return a JSON object:
{
  "contradictions": [
    {
      "source_a": "url or identifier",
      "claim_a": "exact claim from source A",
      "source_b": "url or identifier",
      "claim_b": "exact claim from source B",
      "context": "what topic or fact the contradiction concerns",
      "contradiction_type": "factual_error" | "temporal_mismatch" | "source_disagreement" | "insufficient_evidence",
      "targeted_query": "specific search query (only for factual_error/temporal_mismatch, else null)"
    },
    ...
  ],
  "verdict": "clean" | "flagged",
  "notes": "optional reviewer notes"
}
"""

    def __init__(
        self,
        model_backend: ModelBackend,
        mcp_servers: MCPServerRegistry,
        agent_id: Optional[str] = None,
    ) -> None:
        super().__init__(
            role=AgentRole.LOOP,
            model_backend=model_backend,
            mcp_servers=mcp_servers,
            system_prompt=self._DEFAULT_SYSTEM_PROMPT,
            agent_id=agent_id,
        )

    async def audit(
        self,
        analyst_output: Dict[str, Any],
        model_override: Optional[str] = None,
    ) -> AuditResult:
        """
        Audit the AnalystAgent's output for contradictions and return an
        ``AuditResult`` that includes both the contradiction list and a verdict.

        Each ``Contradiction`` carries a ``targeted_query`` — a precise search
        instruction the Orchestrator will pass back to SearchAgent if a retry
        is warranted.

        Parameters
        ----------
        analyst_output:
            The structured dict produced by ``AnalystAgent.run()``.
        model_override:
            Optional model name override.

        Returns
        -------
        AuditResult
            ``verdict="clean"`` means findings passed QA; ``"flagged"`` means
            at least one contradiction was found and targeted re-search is
            recommended.
        """
        prompt = (
            "Audit the following analyst output for contradictions:\n\n"
            + json.dumps(analyst_output, indent=2)
        )
        response = await self.run(prompt, model_override=model_override)

        contradictions: List[Contradiction] = []
        verdict = "clean"
        notes = ""
        try:
            content = response.content
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0].strip()
            elif "```" in content:
                content = content.split("```")[1].split("```")[0].strip()

            data = json.loads(content)
            verdict = data.get("verdict", "clean")
            notes = data.get("notes", "")
            for item in data.get("contradictions", []):
                contradictions.append(
                    Contradiction(
                        source_a=item.get("source_a", ""),
                        claim_a=item.get("claim_a", ""),
                        source_b=item.get("source_b", ""),
                        claim_b=item.get("claim_b", ""),
                        context=item.get("context", ""),
                        targeted_query=item.get("targeted_query"),
                        contradiction_type=item.get("contradiction_type", "unknown"),
                    )
                )
        except Exception as exc:
            logger.warning("[LoopAgent] Failed to parse audit response: %s", exc)

        return AuditResult(contradictions=contradictions, verdict=verdict, notes=notes)


class ReportComposer(SubAgent):
    """
    Terminal synthesis specialist.

    Receives all vetted findings (post QA) and produces a professional,
    structured research document that includes:
    * Executive summary
    * Key findings and evidence
    * Novel insights derived from cross-source analysis
    * Actionable next steps / recommendations
    * Unresolved knowledge gaps
    """

    _DEFAULT_SYSTEM_PROMPT = """\
You are a professional research-report author producing a document for PDF
output.  You receive fully vetted, QA-approved findings AND a numbered list
of every source collected during the research.

Your output MUST follow this EXACT section structure.  Use plain text only —
NO Markdown syntax (no **, no #, no -, no ```, no ---):

RESEARCH REPORT: [derive a concise title from the research query]

EXECUTIVE SUMMARY
[2-4 sentence overview of the research objective, methodology, and primary
conclusion.]

KEY FINDINGS
[Numbered list of the most important, evidence-backed facts.  After each
finding cite relevant sources with bracket notation, e.g. [3] [7].]

NOVEL INSIGHTS
[Non-obvious conclusions drawn from cross-source analysis.  Cite sources.]

RECOMMENDATIONS
[Concrete, actionable next steps for the reader.  Cite supporting evidence.]

KNOWLEDGE GAPS
[Open questions that remain unanswered and should be addressed in future
research.]

REFERENCES
[1] <source title> — <URL>
[2] <source title> — <URL>
... (include every source from the provided source list)

Rules:
- Use [N] in-text citations linking claims to the References section.
- Every source in the provided list MUST appear in the References section.
- Do NOT use Markdown syntax of any kind.
- Use numbered lists (1. 2. 3.) for multi-item sections.
- Sections must appear in the order shown above.
- Tone: authoritative, professional, and accessible.
"""

    def __init__(
        self,
        model_backend: ModelBackend,
        mcp_servers: MCPServerRegistry,
        agent_id: Optional[str] = None,
    ) -> None:
        super().__init__(
            role=AgentRole.REPORT,
            model_backend=model_backend,
            mcp_servers=mcp_servers,
            system_prompt=self._DEFAULT_SYSTEM_PROMPT,
            agent_id=agent_id,
        )


# ---------------------------------------------------------------------------
# Agent pool — per-role model selection
# ---------------------------------------------------------------------------


@dataclass
class AgentPool:
    """
    Holds exactly one instance of each sub-agent role.

    All five agents are created from the *same* shared ModelBackend instance by
    default; callers may optionally supply separate backends per role (e.g. to
    run the Root on a high-reasoning model while the SearchAgent uses a cheaper
    model).
    """

    root_backend: ModelBackend
    search_backend: ModelBackend
    analyst_backend: ModelBackend
    loop_backend: ModelBackend
    report_backend: ModelBackend

    # Populated in async_init() since SubAgents require MCP registry access, which is async to set up.
    search: SearchAgent = field(init=False)
    analyst: AnalystAgent = field(init=False)
    loop: LoopAgent = field(init=False)
    report: ReportComposer = field(init=False)

    async def async_init(self, mcp_registry: MCPServerRegistry) -> None:
        """Asynchronous initialization to set up sub-agents with the shared MCP registry."""
        # For simplicity, all agents share the same MCP registry since only
        # Search needs tool access at this stage.  If needed, this can be
        # refactored later to allow per-agent registries.
        self.search = SearchAgent(
            model_backend=self.search_backend,
            mcp_servers=mcp_registry,
        )
        self.analyst = AnalystAgent(
            model_backend=self.analyst_backend,
            mcp_servers=mcp_registry,
        )
        self.loop = LoopAgent(
            model_backend=self.loop_backend,
            mcp_servers=mcp_registry,
        )
        self.report = ReportComposer(
            model_backend=self.report_backend,
            mcp_servers=mcp_registry,
        )

    @classmethod
    def from_single_backend(cls, backend: ModelBackend) -> "AgentPool":
        """
        Convenience constructor: all agents share the same model backend.
        Useful for local/Ollama setups or when using a single API key.
        """
        return cls(
            root_backend=backend,
            search_backend=backend,
            analyst_backend=backend,
            loop_backend=backend,
            report_backend=backend,
        )

    async def close(self) -> None:
        """Close all sub-agents' model backends."""
        for agent in [self.search, self.analyst, self.loop, self.report]:
            del agent.model


# ---------------------------------------------------------------------------
# Orchestrator (Root agent)
# ---------------------------------------------------------------------------


class Orchestrator:
    """
    The Root orchestrator.

    Responsibilities
    ----------------
    1. **Plan** — Analyse the user's prompt, identify knowledge gaps, and
       decompose the problem into a ``ResearchPlan`` with discrete steps.
    2. **Dispatch** — Route each plan step to the appropriate specialist
       sub-agent (Search → Analyst → LoopAgent → ReportComposer).
    3. **QA loop** — After the LoopAgent audits findings, either flag
       contradictions back to the Search/Analyst pair for resolution, or
       clear the findings for synthesis.
    4. **Synthesize** — Invoke the ReportComposer to produce the final document.
    5. **Emit** — Yield ``ResponseMessage`` events throughout so callers
       (e.g. the WebSocket handler in ``api_server.py``) can stream progress to
       the frontend.

    Parameters
    ----------
    config:
        Global application config (model backend, MCP URLs, limits, …).
    mcp_registry:
        Pre-connected MCP server registry.  The Orchestrator passes the
        registry to sub-agents that need tool access (primarily Search).
    agent_pool:
        Optional pre-built ``AgentPool``.  If omitted, one is created from the
        backend derived from *config*.
    research_depth:
        Controls pipeline behaviour.  One of ``"shallow"``, ``"moderate"``
        (default), or ``"deep"``.

        - ``"shallow"`` — Search → Analyst only; the QA (LoopAgent) pass is
          skipped entirely.
        - ``"moderate"`` — standard Search → Analyst → QA with up to 2 retries.
        - ``"deep"`` — Search uses in-depth query hints; Analyst uses rigorous
          extraction instructions; QA allows up to 3 retries.
    """

    def __init__(
        self,
        config: Config,
        mcp_registry: MCPServerRegistry,
        agent_pool: Optional[AgentPool] = None,
        long_term_memory: Optional["AsyncLongTermMemory"] = None,
        research_depth: str = "shallow",
    ) -> None:
        self.config = config
        self.mcp_registry = mcp_registry
        self.research_depth = research_depth
        # Derive max_qa_retries from depth; shallow skips QA entirely so the
        # value is irrelevant, but we set 0 for clarity.
        _retries_by_depth = {"shallow": 0, "moderate": 2, "deep": 3}
        self.max_qa_retries: int = _retries_by_depth.get(research_depth, 2)
        self._long_term_memory = long_term_memory
        # Set by api_server after session creation so record_event() calls
        # are filterable by session in log dumps.
        self.session_id: Optional[str] = None

        # Build the root (planning) backend
        self._root_backend: ModelBackend = create_model_backend(config)

        # Build the agent pool (all sharing the same backend unless overridden)
        self.agents: AgentPool = agent_pool or AgentPool.from_single_backend(
            self._root_backend
        )

        # Shared research context — survives the full plan→execute→synthesise lifecycle
        self.context: ResearchContext = ResearchContext()

        # Per-session RAG store for raw search/scrape tool results
        self._search_store: SearchResultStore = SearchResultStore(long_term_memory)

        # Mutable orchestration state
        self._pending_plan: Optional[ResearchPlan] = None
        self._raw_findings: List[Dict[str, Any]] = []  # per-step SearchAgent output
        self._analyst_output: Optional[Dict[str, Any]] = None
        self._contradictions: List[Contradiction] = []
        self._qa_retries: int = 0
        self._step_sources: Dict[int, List[Dict[str, Any]]] = (
            {}
        )  # step_id → source list
        # Per-step short summaries (≤config.step_summary_max_chars each).
        # These form the Orchestrator's "active context" — the CEO's weekly
        # briefing.  Updated after every step's QA pass and used as the sole
        # input to the multi-pass synthesis Outline Phase.
        self._step_summaries: Dict[int, str] = {}
        # Analyst follow-up recommendations accumulated across completed steps.
        # Forwarded to subsequent SearchAgent calls so search focus can be
        # refined based on gaps identified by prior steps' analysts.
        self._analyst_recommendations: List[Dict[str, Any]] = []
        # Long-term memory recalled once per execution batch and shared across
        # that batch's (possibly parallel) steps so every Search/Analyst agent
        # can build on prior-session knowledge without paying the recall cost
        # per step.  Refreshed at the start of each batch in execute().
        self._batch_memory: str = ""
        # Internal registry of live parallel-worker tasks so they can be
        # canceled atomically when the outer session task is canceled.
        self._active_tasks: List[asyncio.Task] = []
        # Tracks entity + relationship writes since the last community-detection
        # run.  Community update is skipped when fewer than
        # config.graph_community_min_mutations graph elements have been added,
        # preventing redundant LLM calls when a session adds little new data.
        self._graph_mutations_since_community_update: int = 0

    # ------------------------------------------------------------------
    # Public API — mirrors the phased interface of ResearchAgent so that
    # existing callers (api_server, cli, …) can adopt the Orchestrator with
    # minimal changes.
    # ------------------------------------------------------------------

    async def plan(
        self,
        query: str,
        local_documents: Optional[List[Dict[str, str]]] = None,
    ) -> AsyncIterator[ResponseMessage]:
        """
        Phase 1: analyse the query and emit a ``ResearchPlan``.

        Yields ``ResponseMessage`` events; the final event carries
        ``type="plan"`` with the plan dict in ``plan``.

        Parameters
        ----------
        query:
            The user's research question.
        local_documents:
            Optional list of dicts with ``"name"`` and ``"content"`` keys
            representing locally discovered documents to treat as primary
            source material.  When provided, their text is injected into
            the planning prompt so the root agent can incorporate them.
        """
        self._reset_state()

        yield ResponseMessage(type="status", message="Orchestrator: analyzing query…")

        # ── Root agent: identify knowledge gaps and produce a plan ──────────
        plan_prompt = self._build_planning_prompt(query)
        root_model = self._select_model(AgentRole.ROOT, "plan")

        # Seed the planning prompt with relevant memories from prior sessions
        # so the root agent avoids re-investigating already-covered ground.
        prior_memories = await self._recall_memories(query)

        # Incorporate user-uploaded reference files so the root agent knows
        # what primary-source material is already available locally.
        file_context = await self._gather_uploaded_file_context(query)
        if file_context:
            yield ResponseMessage(
                type="status",
                message="Root agent: reading uploaded reference files…",
            )

        yield ResponseMessage(
            type="status", message="Root agent: generating research plan…"
        )

        if file_context:
            plan_prompt += (
                "\n\nUser-uploaded reference files (treat as primary sources; "
                "avoid re-researching content already present here):\n" + file_context
            )

        if prior_memories:
            plan_prompt += (
                "\n\nRelevant prior research context (from long-term memory — "
                "use to avoid re-investigating already-covered ground):\n"
                + prior_memories
                + "\n\n"
            )

        # Inject locally-discovered documents provided by the user so the root
        # agent can treat them as primary sources and avoid redundant searches.
        if local_documents:
            doc_blocks = []
            for doc in local_documents:
                name = doc.get("name", "unnamed")
                content = doc.get("content", "").strip()
                if content:
                    doc_blocks.append(f"[Document: {name}]\n{content}")
            if doc_blocks:
                plan_prompt += (
                    "\n\nLocal context documents (treat as primary sources; "
                    "prefer these over re-fetching the same information):\n\n"
                    + "\n\n".join(doc_blocks)
                    + "\n\n"
                )
                yield ResponseMessage(
                    type="status",
                    message=f"Root agent: incorporating {len(doc_blocks)} local document(s)…",
                )

        # Inject the current date to aid root agent with its temporal reasoning and source evaluation
        # (e.g. "if your training cutoff is in 2021 but today's date is 2024, you should prioritize
        #  current sources and be skeptical of outdated info in your training data").
        current_time = datetime.now(timezone.utc).today().strftime("%m:%d:%Y")
        plan_prompt += f"\n\nCurrent date: {current_time}\n\n"

        root_messages = [
            Message(role="system", content=self._root_system_prompt()),
            Message(role="user", content=plan_prompt),
        ]
        response: ModelResponse = await self._root_backend.generate(
            model=root_model,
            messages=root_messages,
            temperature=self.config.root_temperature,
            max_tokens=self.config.root_max_tokens,
        )

        plan = self._parse_plan(response.content, query=query)

        self._pending_plan = plan
        self.context.save_step("query", query)
        self.context.save_step("plan", json.dumps(plan.to_dict()))

        yield ResponseMessage(
            type="plan",
            message="Research plan ready. Awaiting approval.",
            plan=plan.to_dict(),
        )

    async def execute(self) -> AsyncIterator[ResponseMessage]:
        """
        Phase 2: dispatch sub-agents to execute the approved plan.

        Steps are executed in *execution batches* derived from the plan:

        * Steps that share the same ``parallel_group`` label are dispatched
          concurrently via ``asyncio.gather`` — each one runs its own
          Search → Analyst → QA chain simultaneously.
        * Steps with ``parallel_group=None`` are treated as sequential
          checkpoints and run one at a time, in plan order, after any
          preceding parallel batch has fully completed.

        The ordering guarantee is:
          batch 1 (parallel) → batch 2 (sequential) → batch 3 (parallel) → …

        Yields ``ResponseMessage`` events throughout.
        """
        if not self._pending_plan:
            yield ResponseMessage(
                type="error",
                message="No approved plan found. Call plan() first.",
            )
            return

        plan = self._pending_plan
        query = self.context.get_step_result("query") or ""
        tools = await self.mcp_registry.get_all_tools()

        # Shared concurrency control + dedup across all parallel step workers
        pipeline_runner = PipelineRunner(max_concurrent=MAX_CONCURRENT_PIPELINES)

        # ── Group steps into ordered execution batches ──────────────────────
        batches = self._group_steps(plan.steps)

        parallel_count = sum(1 for b in batches if len(b) > 1)
        sequential_count = sum(1 for b in batches if len(b) == 1)
        yield ResponseMessage(
            type="status",
            message=(
                f"Execution plan: {len(batches)} batch(es) — "
                f"{parallel_count} parallel, {sequential_count} sequential."
            ),
            data={
                "batches": [
                    {
                        "parallel": len(b) > 1,
                        "step_ids": [s.id for s in b],
                    }
                    for b in batches
                ]
            },
        )

        # ── Execute each batch ───────────────────────────────────────────────
        self._batch_memory = ""
        for batch_idx, batch in enumerate(batches, start=1):
            # ── Batch-level long-term memory recall ─────────────────────────
            # Recall once per batch (cached across the batch's parallel steps)
            # so each step's Search and Analyst agents can build on knowledge
            # from prior research sessions.  Failures are swallowed inside
            # _recall_memories, so this never breaks execution.
            batch_recall_query = " ".join(s.description for s in batch).strip()
            self._batch_memory = await self._recall_memories(
                batch_recall_query or query, limit=5
            )
            if self._batch_memory:
                yield ResponseMessage(
                    type="status",
                    message=(
                        f"Batch {batch_idx}: recalled prior-session knowledge from "
                        "long-term memory to inform step research."
                    ),
                )

            is_parallel = len(batch) > 1

            if is_parallel:
                group_label = batch[0].parallel_group
                yield ResponseMessage(
                    type="status",
                    message=(
                        f"Batch {batch_idx}: running {len(batch)} steps in parallel "
                        f"(group '{group_label}'): "
                        + ", ".join(f"Step {s.id}" for s in batch)
                    ),
                )
                for step in batch:
                    step.status = StepStatus.IN_PROGRESS
                    logger.info(
                        "[Step %d] starting (parallel): %s",
                        step.id,
                        step.description[:120],
                    )
                    yield ResponseMessage(
                        type="step_start",
                        message=f"Step {step.id}: {step.description}",
                        data={"step": step.to_dict()},
                    )

                # Collect streamed messages from all parallel workers into a
                # shared queue so we can yield them from this single async
                # generator without interleaving issues.
                msg_queue: asyncio.Queue[Optional[ResponseMessage]] = asyncio.Queue()

                # Worker coroutine for a single step — runs the Search → Analyst → QA loop
                async def _worker(step: ResearchStep) -> None:
                    async with pipeline_runner.semaphore:
                        try:
                            async for msg in self._run_step(
                                step, query, tools, pipeline_runner
                            ):
                                await msg_queue.put(msg)
                        except Exception as worker_exc:
                            # _run_step normally catches its own errors, but guard
                            # here too so the sentinel is always placed.
                            logger.exception(
                                "[Orchestrator] Unhandled exception in parallel worker for step %d: %s",
                                step.id,
                                worker_exc,
                            )
                            step.status = StepStatus.FAILED
                            step.error = str(worker_exc)
                            await msg_queue.put(
                                ResponseMessage(
                                    type="step_failed",
                                    message=f"Step {step.id} failed: {worker_exc}",
                                    error=str(worker_exc),
                                    data={"step": step.to_dict()},
                                )
                            )
                        finally:
                            msg_queue.put_nowait(None)  # sentinel — cancellation-safe

                tasks = [
                    asyncio.create_task(_worker(s), name=f"worker-step-{s.id}")
                    for s in batch
                ]
                self._active_tasks.extend(tasks)
                sentinels_remaining = len(tasks)

                # Yield messages from workers as they arrive, until all workers have placed their sentinel (None)
                # in the queue to signal completion.
                try:
                    while sentinels_remaining > 0:
                        msg = await msg_queue.get()
                        if msg is None:
                            sentinels_remaining -= 1
                        else:
                            yield msg
                except asyncio.CancelledError:
                    # Propagate cancellation to every worker before re-raising.
                    for t in tasks:
                        if not t.done():
                            t.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
                    raise

                # Ensure all tasks are truly done and log any unexpected exceptions
                results = await asyncio.gather(*tasks, return_exceptions=True)
                for task_result in results:
                    if isinstance(task_result, BaseException) and not isinstance(
                        task_result, asyncio.CancelledError
                    ):
                        logger.error(
                            "[Orchestrator] Parallel worker task raised an unexpected exception: %s",
                            task_result,
                        )
                # Remove finished tasks from the active registry
                self._active_tasks = [t for t in self._active_tasks if not t.done()]

            else:
                # Single-step sequential batch
                step = batch[0]
                step.status = StepStatus.IN_PROGRESS
                logger.info(
                    "[Step %d] starting (sequential): %s",
                    step.id,
                    step.description[:120],
                )
                yield ResponseMessage(
                    type="step_start",
                    message=f"Batch {batch_idx} — Step {step.id}: {step.description}",
                    data={"step": step.to_dict()},
                )
                async with pipeline_runner.semaphore:
                    async for msg in self._run_step(
                        step, query, tools, pipeline_runner
                    ):
                        yield msg

        logger.info("[Orchestrator] research_complete — all steps executed")
        yield ResponseMessage(
            type="research_complete",
            message="All steps executed. Proceeding to synthesis…",
        )

    # ------------------------------------------------------------------
    # Step execution helper (Search → Analyst → QA loop for one step)
    # ------------------------------------------------------------------

    async def _run_step(
        self,
        step: ResearchStep,
        query: str,
        tools: List[Dict[str, Any]],
        pipeline_runner: Optional[PipelineRunner] = None,
    ) -> AsyncIterator[ResponseMessage]:
        """
        Execute the Search → Analyst → QA-gated retry loop for a single step.

        Pipeline
        --------
        1. SearchAgent gathers raw data with MCP tools.
        2. AnalystAgent extracts structured claims (pure extraction — no
           coverage-gating decisions).
        3. LoopAgent audits for contradictions and, if found, provides a
           ``targeted_query`` per contradiction:
           - verdict = "clean"  → step is done, proceed to synthesis.
           - verdict = "flagged" + retry budget remains → SearchAgent runs
             the targeted queries, AnalystAgent re-extracts over the
             supplemental findings (merged with prior), LoopAgent re-audits.
             Repeats up to ``max_qa_retries`` times.
           - Budget exhausted   → proceed with remaining contradictions flagged.

        The synthesis step is a separate, single-agent phase that runs AFTER
        all research steps have completed — it is NOT part of this pipeline.
        """
        # ── Dedup: skip this step if an identical query already ran ──────────
        if pipeline_runner:
            is_new = await pipeline_runner.is_query_new(step.description)
            if not is_new:
                logger.info(
                    "[Pipeline] Step %d skipped — duplicate query: %.80s",
                    step.id,
                    step.description,
                )
                step.status = StepStatus.COMPLETED
                step.result = (
                    "Skipped — duplicate query already executed by another pipeline."
                )
                yield ResponseMessage(
                    type="step_complete",
                    message=f"Step {step.id} skipped (duplicate query).",
                    data={"step": step.to_dict(), "skipped_duplicate": True},
                )
                return

        # ── Synthesis guard: skip steps that are report/synthesis tasks ──────
        # The synthesize() phase handles all final report assembly automatically
        # after execute() completes.  If the Root planning agent included a
        # synthesis/compilation step in the plan despite the CRITICAL CONSTRAINT
        # in its system prompt, skip it here to prevent the full
        # Search→Analyst→QA pipeline from running on a non-research step.
        if self._is_synthesis_step(step):
            logger.info(
                "[Step %d] skipped — synthesis/report step detected; "
                "ReportComposer handles this automatically.",
                step.id,
            )
            step.status = StepStatus.COMPLETED
            step.result = (
                "Skipped — report synthesis is handled automatically by the "
                "ReportComposer after all research steps complete."
            )
            yield ResponseMessage(
                type="step_complete",
                message=(
                    f"Step {step.id} skipped: synthesis and report generation is "
                    "handled automatically by the ReportComposer."
                ),
                data={"step": step.to_dict(), "skipped_synthesis": True},
            )
            return

        try:
            # ── Phase 1: SearchAgent ────────────────────────────────────────
            yield ResponseMessage(
                type="status",
                message=f"[Search] Gathering data for step {step.id}…",
            )
            # Use the RAG store for cross-step prior context when available
            # (it queries *all* chunks except the current step, so it only
            # surfaces findings from already-completed steps).
            prior_context = await self._search_store.retrieve(
                query=step.description,
                top_k=3,
            )
            # Also pull structured analyst notes from ResearchContext as a
            # lightweight supplement (they are much smaller than raw chunks).
            if not prior_context:
                prior_context = self.context.retrieve_relevant(
                    step.description, top_k=2
                )
            # Forward analyst recommendations from recently completed steps so
            # the SearchAgent can refine its search focus based on identified gaps.
            recent_recs = self._analyst_recommendations[-3:]
            rec_text = ""
            if recent_recs:
                rec_text = (
                    "\n\nPrior analyst recommendations for follow-up:\n"
                    + "\n".join(
                        f"- Step {r['step_id']}: {r['recommendations']}"
                        for r in recent_recs
                    )
                )
            _search_status: List[ResponseMessage] = []
            search_result = await self._run_search(
                step,
                query,
                tools,
                extra_context=(
                    f"Relevant findings from prior steps:\n{prior_context}{rec_text}"
                    if prior_context or rec_text
                    else ""
                ),
                status_sink=_search_status,
                ltm_context=getattr(self, "_batch_memory", "") or None,
            )
            for _msg in _search_status:
                yield _msg
            all_tools_used: List[str] = list(search_result.pop("_tools_used", []) or [])
            self.context.save_step(f"search_step_{step.id}", json.dumps(search_result))

            # ── Phase 2: AnalystAgent — pure extraction ─────────────────────
            yield ResponseMessage(
                type="status",
                message=f"[Analyst] Extracting claims for step {step.id}…",
            )
            analyst_result = await self._run_analyst(
                step,
                query,
                search_result,
                tools,
                ltm_context=getattr(self, "_batch_memory", "") or None,
            )
            self.context.save_step(
                f"analyst_step_{step.id}", json.dumps(analyst_result)
            )
            self.context.add_result(
                step.id, step.description, json.dumps(analyst_result)
            )

            # ── Phase 3: LoopAgent — QA-gated retry loop ────────────────────
            # Skipped entirely for "shallow" research depth.
            qa_retry_count: int = 0
            if self.research_depth == "shallow":
                yield ResponseMessage(
                    type="status",
                    message=f"[QA] Skipped (shallow mode) for step {step.id}.",
                )
            else:
                # Contradiction types that warrant targeted re-search.
                # "source_disagreement" and "insufficient_evidence" do NOT
                # trigger re-search — they are handled separately below.
                _RESEARCHABLE_TYPES = {"factual_error", "temporal_mismatch", "unknown"}
                # Tracks the actionable count from the previous audit pass.
                # None on the first pass so the convergence check is skipped
                # (we always attempt at least one retry before checking).
                prev_actionable_count: Optional[int] = None

                while True:
                    yield ResponseMessage(
                        type="status",
                        message=f"[QA] Auditing findings for step {step.id}…",
                    )
                    audit = await self.agents.loop.audit(
                        analyst_result,
                        model_override=self._select_model(
                            AgentRole.LOOP, step.description
                        ),
                    )

                    if audit.verdict == "clean" or not audit.contradictions:
                        break  # QA approved — nothing to chase, exit the loop

                    # Record all contradictions for the session log
                    self._contradictions.extend(audit.contradictions)

                    # ── Split by type ─────────────────────────────────────────
                    # Actionable: can be improved by targeted re-search
                    actionable = [
                        c
                        for c in audit.contradictions
                        if c.contradiction_type in _RESEARCHABLE_TYPES
                    ]
                    # Source disagreements: genuine multi-perspective conflict;
                    # inject as analyst tensions rather than triggering re-search
                    source_disagreements = [
                        c
                        for c in audit.contradictions
                        if c.contradiction_type == "source_disagreement"
                    ]
                    # Insufficient evidence: forward as analyst recommendations
                    insufficient_evidence = [
                        c
                        for c in audit.contradictions
                        if c.contradiction_type == "insufficient_evidence"
                    ]

                    # Inject source disagreements as tensions — they are
                    # research findings, not pipeline errors.
                    if source_disagreements:
                        new_tensions = [
                            {
                                "topic": c.context or "Source disagreement",
                                "source_a": c.source_a,
                                "position_a": c.claim_a,
                                "source_b": c.source_b,
                                "position_b": c.claim_b,
                            }
                            for c in source_disagreements
                        ]
                        analyst_result = self._merge_analyst_outputs(
                            analyst_result,
                            {
                                "claims": [],
                                "tensions": new_tensions,
                                "analyst_notes": "",
                            },
                        )

                    # Forward insufficient_evidence items as analyst recommendations
                    for c in insufficient_evidence:
                        if c.context:
                            self._analyst_recommendations.append(
                                {
                                    "step_id": step.id,
                                    "recommendations": (
                                        f"Insufficient evidence for: {c.context}"
                                    ),
                                }
                            )

                    yield ResponseMessage(
                        type="status",
                        message=(
                            f"[QA] {len(audit.contradictions)} contradiction(s) in "
                            f"step {step.id}: {len(actionable)} actionable, "
                            f"{len(source_disagreements)} source disagreements "
                            "recorded as tensions."
                        ),
                        data={
                            "contradictions": [
                                c.to_dict() for c in audit.contradictions
                            ]
                        },
                    )

                    # No actionable contradictions — source disagreements have
                    # been captured as tensions; nothing left to re-search for.
                    if not actionable:
                        yield ResponseMessage(
                            type="status",
                            message=(
                                f"[QA] No actionable contradictions for step {step.id} "
                                "— proceeding."
                            ),
                        )
                        break

                    # Convergence check: if actionable count has not decreased
                    # since the last retry, further searching is making things
                    # worse (or flat) — break to avoid wasted compute.
                    if (
                        prev_actionable_count is not None
                        and len(actionable) >= prev_actionable_count
                    ):
                        logger.info(
                            "[QA] Step %d not converging (%d\u2192%d actionable) — accepting findings",
                            step.id,
                            prev_actionable_count,
                            len(actionable),
                        )
                        yield ResponseMessage(
                            type="status",
                            message=(
                                f"[QA] Actionable contradictions not decreasing "
                                f"({prev_actionable_count}\u2192{len(actionable)}) for "
                                f"step {step.id} \u2014 not converging; accepting findings."
                            ),
                        )
                        break
                    prev_actionable_count = len(actionable)

                    if qa_retry_count >= self.max_qa_retries:
                        logger.info(
                            "[QA] Step %d retry budget exhausted after %d retry(s)",
                            step.id,
                            qa_retry_count,
                        )
                        yield ResponseMessage(
                            type="status",
                            message=(
                                f"[QA] Retry budget exhausted after {qa_retry_count} "
                                f"retry(s) for step {step.id} \u2014 proceeding with caveats."
                            ),
                        )
                        break

                    # Retry budget remains — targeted re-investigation on
                    # actionable contradictions only.
                    qa_retry_count += 1
                    yield ResponseMessage(
                        type="status",
                        message=(
                            f"[QA retry {qa_retry_count}/{self.max_qa_retries}] "
                            f"Targeted re-investigation for step {step.id}…"
                        ),
                    )

                    # Build targeted search context from actionable queries only
                    targeted_queries = [
                        c.targeted_query for c in actionable if c.targeted_query
                    ]

                    # Dedup: skip targeted queries already searched by other pipelines
                    if pipeline_runner and targeted_queries:
                        unique_queries = []
                        for tq in targeted_queries:
                            if await pipeline_runner.is_query_new(tq):
                                unique_queries.append(tq)
                        if not unique_queries:
                            yield ResponseMessage(
                                type="status",
                                message=(
                                    f"[QA] All targeted queries for step {step.id} "
                                    "already searched — accepting current findings."
                                ),
                            )
                            break
                        targeted_queries = unique_queries

                    targeted_ctx = (
                        (
                            "Targeted investigation — resolve the following factual "
                            "contradictions by finding primary-source evidence:\n"
                            + "\n".join(f"- {q}" for q in targeted_queries)
                        )
                        if targeted_queries
                        else (
                            "Find additional primary sources to corroborate or refute "
                            "the flagged factual claims."
                        )
                    )

                    yield ResponseMessage(
                        type="status",
                        message=f"[Search] Targeted re-search for step {step.id}…",
                    )
                    # Track chunk count before/after supplemental search. If no
                    # new content is found, further retries won't improve quality
                    # (data poverty condition — primary sources simply don't exist
                    # yet for the queried time period / topic).
                    chunks_before = self._search_store.chunk_count(
                        include_superseded=False
                    )
                    _supp_status: List[ResponseMessage] = []
                    supplemental_search = await self._run_search(
                        step,
                        query,
                        tools,
                        extra_context=targeted_ctx,
                        status_sink=_supp_status,
                    )
                    for _msg in _supp_status:
                        yield _msg
                    chunks_after = self._search_store.chunk_count(
                        include_superseded=False
                    )
                    supp_tools = list(supplemental_search.pop("_tools_used", []) or [])
                    all_tools_used.extend(supp_tools)

                    if chunks_after == chunks_before:
                        yield ResponseMessage(
                            type="status",
                            message=(
                                f"[QA] Targeted re-search found no new content for step {step.id} "
                                "— data poverty; accepting current findings."
                            ),
                        )
                        break

                    yield ResponseMessage(
                        type="status",
                        message=f"[Analyst] Re-extracting claims for step {step.id}…",
                    )
                    prior_analyst_str = json.dumps(analyst_result, indent=2)
                    supplemental_analyst = await self._run_analyst(
                        step,
                        query,
                        supplemental_search,
                        tools,
                        extra_context=f"Prior analysis to build upon:\n{prior_analyst_str}",
                    )

                    # Merge supplemental findings and loop back to QA
                    analyst_result = self._merge_analyst_outputs(
                        analyst_result, supplemental_analyst
                    )
                    self.context.save_step(
                        f"analyst_step_{step.id}_retry_{qa_retry_count}",
                        json.dumps(analyst_result),
                    )

                    # Mark the actionable contradictions just re-investigated as
                    # resolved.  These are the same Contradiction instances
                    # recorded in self._contradictions, so the report's
                    # "unresolved contradictions" caveats and observability now
                    # accurately reflect that targeted re-search addressed them.
                    _new_chunk_count = max(chunks_after - chunks_before, 0)
                    for _c in actionable:
                        _c.resolved = True
                        _c.resolution = (
                            f"Targeted re-search gathered {_new_chunk_count} new "
                            "source chunk(s); claims re-extracted and reconciled."
                        )

            # Persist QA-vetted findings to long-term memory for future sessions
            await self._store_analyst_findings(step, analyst_result)

            # Persist this step's raw evidence incrementally so learnings survive
            # a mid-session crash and are available to later steps/sessions —
            # rather than only writing everything after synthesis completes.
            try:
                await self._search_store.persist_to_long_term_memory(
                    query=step.description,
                    top_k=10,
                    step_id_filter=step.id,
                )
            except Exception as exc:
                logger.debug(
                    "[Orchestrator] Incremental evidence persist skipped for "
                    "step %d: %s",
                    step.id,
                    exc,
                )

            # Deduplicate the tools invoked across the initial search and any QA
            # re-search passes, preserving first-seen order, for both the
            # observability event and the step_complete payload below.
            _seen_tools: set = set()
            tools_used = [
                t
                for t in all_tools_used
                if not (t in _seen_tools or _seen_tools.add(t))  # type: ignore[func-returns-value]
            ]

            # Emit a structured event so analyst notes for every step are
            # captured in per-session log dumps for pipeline diagnostics.
            record_event(
                "analyst_step_complete",
                session_id=self.session_id,
                step_id=step.id,
                step_description=step.description,
                claim_count=len(analyst_result.get("claims", [])),
                tension_count=len(analyst_result.get("tensions", [])),
                qa_retries=qa_retry_count,
                tools_used=tools_used,
                analyst_notes=str(analyst_result.get("analyst_notes") or ""),
                claims=[
                    {
                        "claim": c.get("claim", ""),
                        "confidence": c.get("confidence", ""),
                        # Analyst system prompt uses "sources" (list); normalize
                        # here so logging always has a non-empty string when URLs
                        # were actually provided.
                        "source": (
                            ", ".join(c["sources"])
                            if isinstance(c.get("sources"), list) and c["sources"]
                            else c.get("source", "")
                        ),
                    }
                    for c in analyst_result.get("claims", [])
                ],
                tensions=[
                    t.get("topic", "") or str(t)
                    for t in analyst_result.get("tensions", [])
                ],
            )

            # ── Contradiction resolution bookkeeping ─────────────────────────
            # Contradictions are detected WITHIN a single step's analyst output,
            # so their source_a/source_b fields are source URLs/labels from this
            # step — never cross-step references.  Resolution is therefore
            # reflected via the Contradiction.resolved flag (set in the QA loop
            # after a successful targeted re-search), which excludes them from
            # the report's unresolved-contradiction caveats.  We do not supersede
            # whole-step chunks here because that would discard this step's valid
            # evidence along with the contradicted claim.

            # ── Generate per-step summary (Active Context briefing) ──────────
            # This ≤config.step_summary_max_chars bullet summary is stored in
            # _step_summaries and forms the Orchestrator's "active context".
            # The Outline Phase of multi-pass synthesis reads ONLY these
            # summaries — never the full analyst JSON — keeping the
            # noise-to-signal ratio low.
            step_summary = await self._generate_step_summary(step, analyst_result)
            self._step_summaries[step.id] = step_summary

            # ── Extract analyst recommendations for subsequent SearchAgent ───
            # Pull follow-up search suggestions out of analyst_notes and store
            # them. They are forwarded to the next step's SearchAgent so it can
            # target known coverage gaps identified by prior analysts.
            notes_str = str(analyst_result.get("analyst_notes") or "")
            if notes_str:
                rec_lines = [
                    ln.strip()
                    for ln in notes_str.splitlines()
                    if ln.strip()
                    and any(
                        kw in ln.lower()
                        for kw in (
                            "suggest",
                            "recommend",
                            "additional search",
                            "further search",
                            "search for",
                            "investigate",
                            "look for",
                            "find ",
                            "query",
                        )
                    )
                ]
                if rec_lines:
                    self._analyst_recommendations.append(
                        {
                            "step_id": step.id,
                            "recommendations": " | ".join(rec_lines[:4]),
                        }
                    )

            # ── Final bookkeeping ────────────────────────────────────────────
            self._step_sources[step.id] = self._extract_sources(search_result)

            self._raw_findings.append(search_result)
            self._analyst_output = self._merge_analyst_outputs(
                self._analyst_output, analyst_result
            )

            step.status = StepStatus.COMPLETED
            step.result = json.dumps(analyst_result)
            logger.info(
                "[Step %d] complete (qa_retries=%d, contradictions=%d)",
                step.id,
                qa_retry_count,
                len(self._contradictions),
            )
            yield ResponseMessage(
                type="step_complete",
                message=f"Step {step.id} complete.",
                data={
                    "step": step.to_dict(),
                    "tools_used": tools_used,
                    "qa_retries": qa_retry_count,
                    "step_summary": step_summary,
                },
            )

        except Exception as exc:
            logger.exception("[Orchestrator] Step %d failed: %s", step.id, exc)
            step.status = StepStatus.FAILED
            step.error = str(exc)
            yield ResponseMessage(
                type="step_failed",
                message=f"Step {step.id} failed: {exc}",
                error=str(exc),
                data={"step": step.to_dict()},
            )

    # ------------------------------------------------------------------
    # Step grouping + synthesis detection helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _is_synthesis_step(step: ResearchStep) -> bool:
        """Return True if the step describes synthesis or report generation.

        Such steps must NOT pass through the Search→Analyst→QA pipeline.
        The ``synthesize()`` method handles all final report assembly after
        ``execute()`` completes.  This guard catches cases where the Root
        planning agent ignores the CRITICAL CONSTRAINT in its system prompt
        and includes a synthesis/compilation step in the research plan anyway.
        """
        desc_lower = step.description.lower()
        _SYNTHESIS_SIGNALS = (
            "synthesize all",
            "synthesise all",
            "compile all findings",
            "compile all results",
            "compile and synthesize",
            "compile and synthesise",
            "integrate all findings",
            "integrate all results",
            "integrate all research",
            "consolidate all findings",
            "consolidate all results",
            "final synthesis",
            "generate a final report",
            "generate the final report",
            "write a final report",
            "write the final report",
            "create a final report",
            "create the final report",
            "produce a final report",
            "produce the final report",
            "formulate a final report",
        )
        return any(signal in desc_lower for signal in _SYNTHESIS_SIGNALS)

    @staticmethod
    def _group_steps(steps: List[ResearchStep]) -> List[List[ResearchStep]]:
        """
        Partition the ordered step list into *execution batches*.

        Rules
        -----
        * Steps that share the same non-None ``parallel_group`` value within a
          sequential section (i.e. not separated by a ``parallel_group=None``
          barrier) are collected into a single batch and run concurrently.
        * A step with ``parallel_group=None`` always forms its own singleton
          batch and is run sequentially.  It also flushes any accumulated
          parallel groups from the preceding section.

        Non-adjacent steps that share a group label within the same section are
        coalesced into one batch (insertion-order preserved), so a plan where
        the LLM interleaves steps from the same group still dispatches all
        tasks together rather than serializing them.

        Example (adjacent groups — normal case)
        ----------------------------------------
        Steps:  [A(g1), B(g1), C(None), D(g2), E(g2), F(None)]
        Result: [[A, B], [C], [D, E], [F]]

        Example (non-adjacent same-group — defensive case)
        ---------------------------------------------------
        Steps:  [A(g1), B(g2), C(g1), D(None)]
        Result: [[A, C], [B], [D]]
        """
        batches: List[List[ResearchStep]] = []
        # pending_groups accumulates parallel steps between None barriers,
        # keyed by group label in first-seen order (Python 3.7+ dict ordering).
        pending_groups: Dict[str, List[ResearchStep]] = {}

        def _flush_pending() -> None:
            for group_steps in pending_groups.values():
                batches.append(group_steps)
            pending_groups.clear()

        for step in steps:
            if step.parallel_group is None:
                _flush_pending()
                batches.append([step])
            else:
                label = step.parallel_group
                if label not in pending_groups:
                    pending_groups[label] = []
                else:
                    logger.debug(
                        "[Orchestrator] Non-adjacent steps share parallel_group '%s'; "
                        "coalescing into a single batch.",
                        label,
                    )
                pending_groups[label].append(step)

        _flush_pending()
        return batches

    async def synthesize(self) -> AsyncIterator[ResponseMessage]:
        """
        Phase 3 — Multi-Pass Synthesis.

        Implements the four-pillar "CEO briefing" pattern:

        Phase A — Outline
            The ReportComposer reads ONLY the per-step summaries (the Active
            Context / "weekly briefing") and produces a section outline.
            Input: ≤config.step_summary_max_chars × N steps — deliberately small.

        Phase B — Section Drafting (loop)
            For each section in the outline, a targeted RAG query retrieves
            only the relevant chunks from ``SearchResultStore``.  The
            ReportComposer drafts that section in isolation.  Each drafted
            section is emitted as a ``section_draft`` WebSocket event so the
            frontend can stream the report progressively.

        Phase C — Cross-Reference Pass
            All drafted sections are fed to the LoopAgent to detect
            inter-section contradictions.  Any flagged contradictions are
            included as caveats in the final report.

        Finally, the assembled report is emitted as a ``report`` event, and
        the top-k session RAG chunks are persisted to long-term memory.

        Yields ``ResponseMessage`` events throughout; the terminal event
        carries ``type="report"`` with ``data["document"]``.
        """
        if not self._analyst_output:
            yield ResponseMessage(
                type="error",
                message="No analyst output available. Run execute() first.",
            )
            return

        query = self.context.get_step_result("query")
        if not query:
            yield ResponseMessage(
                type="error",
                message="Original query not found in context. Cannot synthesize.",
            )
            return

        report_model = self._select_model(AgentRole.REPORT, "synthesis")
        loop_model = self._select_model(AgentRole.LOOP, "cross-reference")

        # Compile all sources gathered across every research step
        all_sources: List[Dict[str, Any]] = []
        for step_id in sorted(self._step_sources.keys()):
            all_sources.extend(self._step_sources[step_id])

        # ── Phase A: Outline ─────────────────────────────────────────────────
        # The Composer reads ONLY the step summaries — the CEO's weekly
        # briefing.  This keeps the outline call small and focused.
        yield ResponseMessage(
            type="status",
            message="[Synthesis Phase A] Generating report outline from step summaries…",
        )

        summaries_block = (
            "\n\n".join(
                f"Step {sid}: {summary}"
                for sid, summary in sorted(self._step_summaries.items())
            )
            if self._step_summaries
            else json.dumps(self._analyst_output, indent=2)[:6000]
        )

        outline_prompt = (
            f"Research Query: {query}\n\n"
            f"You have completed {len(self._step_summaries)} research steps.  Below are the high-level "
            "summaries of what each step found (the 'weekly briefing'):\n\n"
            f"{summaries_block}\n\n"
            "Using ONLY this briefing, produce a structured outline for a "
            "professional research report.  For each section, write:\n"
            "- A short section title\n"
            "- One sentence describing what the section will cover\n\n"
            "Return a JSON object:\n"
            '{"report_title": "...", "sections": [{"title": "...", "description": "..."}, ...]}'
        )

        outline_response = await self.agents.report.run(
            prompt=outline_prompt,
            model_override=report_model,
            temperature=self.config.root_temperature,
            max_tokens=self.config.root_max_tokens,
        )

        # Retry once if the model returned an empty string — 65/67 observed
        # outline parse failures are empty responses, not malformed JSON.
        if not (outline_response.content or "").strip():
            logger.warning("[Orchestrator] Outline response was empty; retrying once…")
            outline_response = await self.agents.report.run(
                prompt=outline_prompt,
                model_override=report_model,
                temperature=self.config.root_temperature,
                max_tokens=self.config.root_max_tokens,
            )

        # Parse the outline; fall back to a sensible default structure.
        # Guard against None/empty content — both produce a JSONDecodeError or
        # TypeError that the except block would swallow silently.
        sections: List[Dict[str, str]] = []
        report_title: str = query
        try:
            raw = outline_response.content or ""
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
            elif "```json" in raw:
                raw = raw.split("```json")[1].split("```")[0].strip()
            elif "```" in raw:
                raw = raw.split("```")[1].split("```")[0].strip()
            if raw:
                parsed = json.loads(raw)
                sections = parsed.get("sections", [])
                report_title = parsed.get("report_title", query)
            else:
                logger.warning(
                    "[Orchestrator] Outline response was empty after retry; using defaults"
                )
        except Exception as exc:
            logger.warning(
                "[Orchestrator] Outline parse failed (%s); using defaults", exc
            )

        # If the LLM did not provide a report title (fell back to raw query),
        # attempt a small model call to generate a proper title.
        if report_title == query:
            try:
                title_response = await self.agents.report.model.generate(
                    model=report_model,
                    messages=[
                        Message(
                            role="system",
                            content=(
                                "You generate concise research report titles. "
                                "Respond with ONLY the title text, no quotes, no formatting."
                            ),
                        ),
                        Message(
                            role="user",
                            content=(
                                f"Generate a concise title (under 80 characters) "
                                f"for a research report about: {query}"
                            ),
                        ),
                    ],
                    temperature=0.3,
                    max_tokens=40,
                )
                if (
                    title_response.finish_reason != "error"
                    and title_response.content.strip()
                ):
                    report_title = title_response.content.strip()
            except Exception as _t_exc:
                logger.debug("[Orchestrator] Title generation failed: %s", _t_exc)

            # Heuristic fallback if the LLM call also failed.
            if report_title == query:
                _t = query.strip().strip("\"'").strip()
                report_title = (
                    (_t[0].upper() + _t[1:] if len(_t) > 1 else _t.upper())
                    if _t
                    else "Research Report"
                )

        if not sections:
            sections = [
                {"title": "Executive Summary", "description": "Overview of findings"},
                {"title": "Key Findings", "description": "Primary research results"},
                {"title": "Analysis", "description": "In-depth analysis"},
                {"title": "Recommendations", "description": "Actionable next steps"},
                {"title": "Knowledge Gaps", "description": "Open questions"},
            ]

        yield ResponseMessage(
            type="status",
            message=f"[Synthesis Phase A] Outline ready — {len(sections)} section(s).",
            data={"outline": sections},
        )

        # ── Phase B: Section Drafting ────────────────────────────────────────
        # For each section, targeted RAG retrieval → draft that section alone.
        # Each draft is emitted as a ``section_draft`` event.
        yield ResponseMessage(
            type="status",
            message="[Synthesis Phase B] Drafting report sections…",
        )

        # Recall long-term memories once; deduplicate against session chunks
        raw_memories = await self._recall_memories(query, limit=8)
        memory_lines: List[str] = []
        if raw_memories:
            for line in raw_memories.splitlines():
                line = line.strip()
                if line:
                    memory_lines.append(line)
        # Filter out memory lines that duplicate current session findings
        memory_lines = await self._search_store.filter_long_term_memories(memory_lines)
        deduped_memory = "\n".join(memory_lines) if memory_lines else ""

        drafted_sections: List[Dict[str, str]] = []

        # Pre-compute structured analysis context once for all section drafts.
        # This gives the ReportComposer direct access to curated analytical
        # work (claims, tensions, contradictions) — not just raw evidence
        # chunks.  It's the key bridge for generating novel insights.
        structured_claims_block = ""
        structured_tensions_block = ""
        if self._analyst_output:
            claims = self._analyst_output.get("claims", [])
            if claims:
                claim_lines = []
                for c in claims:
                    ct = c.get("claim", "")
                    conf = c.get("confidence", "")
                    srcs = c.get("sources", [])
                    src_str = (
                        f" [{', '.join(srcs[:3])}]"
                        if isinstance(srcs, list) and srcs
                        else ""
                    )
                    if ct:
                        claim_lines.append(
                            f"- {ct}{src_str}" + (f" ({conf})" if conf else "")
                        )
                if claim_lines:
                    structured_claims_block = (
                        "Structured Research Claims (curated by analyst agents):\n"
                        + "\n".join(claim_lines)
                    )
            tensions = self._analyst_output.get("tensions", [])
            if tensions:
                tension_lines = [
                    f"- {t.get('topic', 'Unknown')}: "
                    f"{t.get('position_a', '')} (from {t.get('source_a', '?')}) "
                    f"vs {t.get('position_b', '')} (from {t.get('source_b', '?')})"
                    for t in tensions
                    if t.get("topic")
                ]
                if tension_lines:
                    structured_tensions_block = (
                        "Source Tensions and Disagreements (use for nuanced analysis):\n"
                        + "\n".join(tension_lines)
                    )

        unresolved_contradictions_block = ""
        unresolved = [c for c in self._contradictions if not c.resolved]
        if unresolved:
            contra_lines = [
                f'- {c.context}: "{c.claim_a}" ({c.source_a}) vs "{c.claim_b}" ({c.source_b}) [{c.contradiction_type}]'
                for c in unresolved[:10]
            ]
            unresolved_contradictions_block = (
                "Unresolved Contradictions (acknowledge these explicitly):\n"
                + "\n".join(contra_lines)
            )

        for sec_idx, section in enumerate(sections, start=1):
            sec_title = section.get("title", f"Section {sec_idx}")
            sec_desc = section.get("description", "")

            # Targeted RAG retrieval for this section only
            rag_query = f"{sec_title}: {sec_desc} — {query}"
            section_evidence = await self._search_store.retrieve(
                query=rag_query,
                top_k=config.section_draft_top_k,
            )

            # Build a focused drafting prompt (swap context in/out per section)
            draft_prompt_parts = [
                f"Research Query: {query}",
                f"Report Section to Draft: {sec_title}",
                f"Section Purpose: {sec_desc}",
            ]
            if section_evidence:
                draft_prompt_parts.append(
                    f"Evidence for this section (semantically retrieved):\n{section_evidence}"
                )
            if structured_claims_block:
                draft_prompt_parts.append(structured_claims_block)
            if not section_evidence and not structured_claims_block:
                logger.warning(
                    "[Synthesis] No RAG evidence or structured claims for section "
                    "'%s' — falling back to raw analyst output.",
                    sec_title,
                )
                # Last-resort fallback: pull relevant subset from analyst output
                analyst_str = json.dumps(self._analyst_output, indent=2)
                draft_prompt_parts.append(f"Research Findings:\n{analyst_str[:6000]}")
            if structured_tensions_block:
                draft_prompt_parts.append(structured_tensions_block)
            if unresolved_contradictions_block:
                draft_prompt_parts.append(unresolved_contradictions_block)
            if deduped_memory:
                draft_prompt_parts.append(
                    f"Prior Research Context (use to broaden perspective):\n{deduped_memory}"
                )
            if all_sources:
                source_lines = [
                    f"[{i}] {s.get('title', s.get('url', ''))} — {s.get('url', '')}"
                    for i, s in enumerate(all_sources, start=1)
                ]
                draft_prompt_parts.append(
                    "Available Sources (use [N] citations):\n" + "\n".join(source_lines)
                )
            draft_prompt_parts.append(
                f"Draft ONLY the '{sec_title}' section now.  Use plain text only — "
                "no Markdown.  Be authoritative, concise, and evidence-backed."
            )

            yield ResponseMessage(
                type="synthesis_progress",
                message=f"Drafting section {sec_idx} of {len(sections)}: {sec_title}…",
                data={
                    "section_index": sec_idx,
                    "section_title": sec_title,
                    "total_sections": len(sections),
                },
            )

            # Use the model backend directly with a minimal section-specific
            # system prompt.  The ReportComposer._DEFAULT_SYSTEM_PROMPT instructs
            # the LLM to always emit a full multi-section report, which causes
            # every drafted section to contain all section headers — duplicating
            # them in the assembled document.  Calling generate() directly with a
            # focused single-section prompt prevents that.
            draft_messages = [
                Message(
                    role="system",
                    content=(
                        "You are a research report section writer. "
                        "Draft ONLY the specific section requested. "
                        "Do NOT output any other section headers or a full report structure. "
                        "Use plain text only — no Markdown syntax. "
                        "You have access to raw evidence AND curated analytical claims. "
                        "Synthesize both to produce insights that go beyond simple summarization — "
                        "identify patterns, draw non-obvious conclusions, and connect findings "
                        "across sources to derive new understanding."
                    ),
                ),
                Message(role="user", content="\n\n".join(draft_prompt_parts)),
            ]

            # Retry section drafting once if the model returns empty/error
            # (e.g. transient rate limiting or server timeout).
            for _draft_attempt in range(2):
                draft_response = await self.agents.report.model.generate(
                    model=report_model,
                    messages=draft_messages,
                    temperature=self.config.root_temperature,
                    max_tokens=self.config.root_max_tokens,
                )
                drafted_text = draft_response.content.strip()
                if drafted_text and draft_response.finish_reason != "error":
                    break
                if _draft_attempt == 0:
                    logger.warning(
                        "[Synthesis] Section '%s' draft was empty/error; retrying once…",
                        sec_title,
                    )
                    await asyncio.sleep(2.0)
            else:
                logger.warning(
                    "[Synthesis] Section '%s' draft still empty after retry; using placeholder.",
                    sec_title,
                )
                drafted_text = (
                    f"[Content for the '{sec_title}' section could not be generated "
                    f"due to a transient model error. Re-run research to regenerate.]"
                )

            drafted_sections.append({"title": sec_title, "content": drafted_text})

            # Stream the drafted section to the frontend immediately
            yield ResponseMessage(
                type="section_draft",
                message=f"Section drafted: {sec_title}",
                data={
                    "section_index": sec_idx,
                    "section_title": sec_title,
                    "section_content": drafted_text,
                    "total_sections": len(sections),
                },
            )

        yield ResponseMessage(
            type="status",
            message="[Synthesis Phase B] All sections drafted.",
        )

        # ── Phase C: Cross-Reference Pass ────────────────────────────────────
        # Feed all drafted sections to the LoopAgent to find inter-section
        # contradictions.  This is separate from the per-step QA that already
        # happened during execute().
        yield ResponseMessage(
            type="status",
            message="[Synthesis Phase C] Cross-referencing sections for contradictions…",
        )

        sections_text = "\n\n".join(
            f"=== {s['title']} ===\n{s['content']}" for s in drafted_sections
        )
        cross_ref_prompt = (
            "You are reviewing a draft research report for inter-section "
            "contradictions.  Below are all drafted sections:\n\n"
            f"{sections_text}\n\n"
            "Identify any claims in one section that directly contradict claims "
            "in another section.  Return your findings as JSON:\n"
            '{"contradictions": [{"source_a": "...", "claim_a": "...", '
            '"source_b": "...", "claim_b": "...", "context": "..."}], '
            '"verdict": "clean" | "flagged"}'
        )

        cross_ref_response = await self.agents.loop.run(
            prompt=cross_ref_prompt,
            model_override=loop_model,
            temperature=self.config.qa_temperature,
            max_tokens=self.config.qa_max_tokens,
        )

        cross_ref_contradictions: List[Dict[str, str]] = []
        try:
            raw_cr = cross_ref_response.content
            if "```json" in raw_cr:
                raw_cr = raw_cr.split("```json")[1].split("```")[0].strip()
            elif "```" in raw_cr:
                raw_cr = raw_cr.split("```")[1].split("```")[0].strip()
            cr_data = json.loads(raw_cr)
            cross_ref_contradictions = cr_data.get("contradictions", [])
            cr_verdict = cr_data.get("verdict", "clean")
        except Exception as exc:
            logger.warning("[Orchestrator] Cross-reference parse failed: %s", exc)
            cr_verdict = "clean"

        if cross_ref_contradictions:
            yield ResponseMessage(
                type="status",
                message=(
                    f"[Synthesis Phase C] {len(cross_ref_contradictions)} "
                    "inter-section contradiction(s) flagged — adding caveats to report."
                ),
                data={"cross_ref_contradictions": cross_ref_contradictions},
            )
        else:
            yield ResponseMessage(
                type="status",
                message="[Synthesis Phase C] No inter-section contradictions found.",
            )

        # ── Assemble final document ──────────────────────────────────────────
        # Separate genuine multi-perspective source disagreements (legitimate
        # research findings) from unresolved factual contradictions (gaps or
        # errors) so the report frames each appropriately instead of lumping
        # valid disagreements under "unresolved contradictions".
        source_perspective_disagreements = [
            c.to_dict()
            for c in self._contradictions
            if not c.resolved and c.contradiction_type == "source_disagreement"
        ]
        unresolved_step_contradictions = [
            c.to_dict()
            for c in self._contradictions
            if not c.resolved and c.contradiction_type != "source_disagreement"
        ]

        doc_parts: List[str] = [
            f"RESEARCH REPORT: {report_title}",
            f"Query: {query}",
            "",
        ]
        for sec in drafted_sections:
            doc_parts.append(sec["title"].upper())
            doc_parts.append(sec["content"])
            doc_parts.append("")

        if cross_ref_contradictions:
            doc_parts.append("CROSS-REFERENCE CAVEATS")
            for i, cr in enumerate(cross_ref_contradictions, start=1):
                src_a = cr.get("source_a") or cr.get("section_a")
                src_b = cr.get("source_b") or cr.get("section_b")
                doc_parts.append(
                    f"{i}. Contradiction between '{src_a}' and "
                    f"'{src_b}': {cr.get('context', '')}"
                )
            doc_parts.append("")

        if unresolved_step_contradictions:
            doc_parts.append("UNRESOLVED SOURCE CONTRADICTIONS")
            for i, uc in enumerate(unresolved_step_contradictions, start=1):
                doc_parts.append(
                    f"{i}. {uc.get('context', '')} "
                    f"(Source A: {uc.get('source_a', '')} — {uc.get('claim_a', '')}; "
                    f"Source B: {uc.get('source_b', '')} — {uc.get('claim_b', '')})"
                )
            doc_parts.append("")

        if source_perspective_disagreements:
            doc_parts.append("SOURCE PERSPECTIVES AND DISAGREEMENTS")
            doc_parts.append(
                "The following reflect genuine differences in how credible sources "
                "assess the same question — not factual errors. They are presented "
                "so readers can weigh the competing perspectives."
            )
            for i, sd in enumerate(source_perspective_disagreements, start=1):
                doc_parts.append(
                    f"{i}. {sd.get('context', '')} "
                    f"(Perspective A: {sd.get('source_a', '')} — {sd.get('claim_a', '')}; "
                    f"Perspective B: {sd.get('source_b', '')} — {sd.get('claim_b', '')})"
                )
            doc_parts.append("")

        if all_sources:
            doc_parts.append("REFERENCES")
            for i, src in enumerate(all_sources, start=1):
                title = src.get("title") or src.get("url", "Unknown")
                url = src.get("url", "")
                doc_parts.append(f"[{i}] {title} — {url}")

        document = "\n".join(doc_parts)
        self.context.save_step("final_report", document)

        # ── Export PDF to data/reports/ ──────────────────────────────────────
        try:
            from report_exporter import export_report_pdf

            await export_report_pdf(document, query, report_title, self.session_id)
        except Exception as _pdf_exc:
            logger.warning("[Orchestrator] PDF export error: %s", _pdf_exc)

        # ── Extract structured metadata for downstream consumers ────────────
        # title was derived from the outline prompt during Phase A; falls back
        # to the original query if the LLM did not supply one.

        # key_findings: content of the first section whose title contains
        # "finding" (case-insensitive), empty string if no such section exists.
        key_findings_content = next(
            (s["content"] for s in drafted_sections if "finding" in s["title"].lower()),
            "",
        )

        # sources: normalized list so consumers don't have to dig into raw dicts.
        report_sources = [
            {"title": s.get("title") or s.get("url", ""), "url": s.get("url", "")}
            for s in all_sources
        ]

        yield ResponseMessage(
            type="report",
            message="Research report complete.",
            data={
                "document": document,
                "sections": drafted_sections,
                "cross_ref_verdict": cr_verdict,
                "title": report_title,
                "sources": report_sources,
                "key_findings": key_findings_content,
            },
        )

        # ── Post-synthesis: persist top-k session chunks to long-term memory ─
        yield ResponseMessage(
            type="status",
            message="[Post-Synthesis] Persisting session evidence to long-term memory…",
        )
        try:
            persisted = await self._search_store.persist_to_long_term_memory(
                query=query, top_k=20
            )
            if persisted:
                logger.info(
                    "[Orchestrator] Persisted %d RAG chunks to long-term memory.",
                    persisted,
                )
        except Exception as exc:
            logger.warning("[Orchestrator] Long-term memory persist failed: %s", exc)

        # ── Post-synthesis: update knowledge graph communities ────────────────
        # Only run if enough new graph data has been written this session to
        # justify the sequential LLM calls community detection requires.
        _min_mutations = getattr(self.config, "graph_community_min_mutations", 3)
        if (
            self._long_term_memory is not None
            and self._long_term_memory.graph.available
            and self._graph_mutations_since_community_update >= _min_mutations
        ):
            yield ResponseMessage(
                type="status",
                message="[Post-Synthesis] Updating knowledge graph communities…",
            )
            try:

                async def _summarize(prompt: str) -> str:
                    model = self._select_model(AgentRole.ROOT, "community_summary")
                    resp = await self._root_backend.generate(
                        model=model,
                        messages=[
                            Message(
                                role="system",
                                content="You are a concise research summarizer.",
                            ),
                            Message(role="user", content=prompt),
                        ],
                    )
                    return resp.content

                communities_updated = (
                    await self._long_term_memory.graph.update_communities(_summarize)
                )
                if communities_updated:
                    logger.info(
                        "[Orchestrator] Updated %d knowledge graph communities.",
                        communities_updated,
                    )
                self._graph_mutations_since_community_update = 0
            except Exception as exc:
                logger.debug("[Orchestrator] Community detection skipped: %s", exc)
        elif (
            self._long_term_memory is not None
            and self._long_term_memory.graph.available
        ):
            logger.debug(
                "[Orchestrator] Community update skipped (%d mutations < threshold %d).",
                self._graph_mutations_since_community_update,
                _min_mutations,
            )

        # ── Post-synthesis: confidence decay ─────────────────────────────────
        if (
            self._long_term_memory is not None
            and self._long_term_memory.graph.available
        ):
            try:
                half_life = getattr(self.config, "confidence_decay_half_life", 30)
                updated = await self._long_term_memory.graph.decay_confidence(
                    half_life_days=half_life
                )
                if updated:
                    logger.debug(
                        "[Orchestrator] Confidence decay applied to %d relationships.",
                        updated,
                    )
            except Exception as exc:
                logger.debug("[Orchestrator] Confidence decay skipped: %s", exc)

    async def run(self, query: str) -> AsyncIterator[ResponseMessage]:
        """
        Convenience method: run the full pipeline in one call.

        plan() → execute() → synthesize()

        Yields all ``ResponseMessage`` events from every phase.
        """
        async for msg in self.plan(query):
            yield msg
        async for msg in self.execute():
            yield msg
        async for msg in self.synthesize():
            yield msg

    async def modify_plan(self, plan_id: str, feedback: str) -> ResponseMessage:
        """
        Regenerate the pending plan incorporating user feedback.

        Mirrors ``ResearchAgent.modify_plan`` so the v2 WebSocket handler can
        use the same message-type contract as v1.

        Parameters
        ----------
        plan_id:
            The ``id`` of the plan the user wants to modify (must match the
            current ``_pending_plan``).
        feedback:
            Free-text feedback from the user describing the desired changes.

        Returns
        -------
        ResponseMessage
            ``type="plan"`` on success, ``type="error"`` otherwise.
        """
        if not self._pending_plan:
            return ResponseMessage(
                type="error",
                message="No pending plan to modify. Please submit a query first.",
            )

        if self._pending_plan.id != plan_id:
            return ResponseMessage(
                type="error",
                message=f"No pending plan found with id '{plan_id}'.",
            )

        original_plan = self._pending_plan
        query = self.context.get_step_result("query") or ""

        try:
            # Ask the root backend to regenerate the plan with feedback injected
            plan_prompt = (
                self._build_planning_prompt(query)
                + f"\n\nUser feedback on the previous plan:\n{feedback}"
                + f"\n\nPrevious plan for reference:\n{json.dumps(original_plan.to_dict(), indent=2)}"
            )
            root_model = self._select_model(AgentRole.ROOT, "plan")
            root_messages = [
                Message(role="system", content=self._root_system_prompt()),
                Message(role="user", content=plan_prompt),
            ]
            response = await self._root_backend.generate(
                model=root_model,
                messages=root_messages,
                temperature=self.config.root_temperature,
                max_tokens=self.config.root_max_tokens,
            )
            modified_plan = self._parse_plan(
                response.content, query=self.context.get_step_result("query") or ""
            )
            self._pending_plan = modified_plan
            self.context.save_step("plan", json.dumps(modified_plan.to_dict()))

            # Generate a brief human-readable summary of what changed
            summary_messages = [
                Message(
                    role="system",
                    content=(
                        "You are a research planning assistant. "
                        "Briefly explain the changes made to a research plan based on user feedback. "
                        "Be concise — two or three sentences at most."
                    ),
                ),
                Message(
                    role="user",
                    content=(
                        f"Original plan:\n{json.dumps(original_plan.to_dict(), indent=2)}\n\n"
                        f"Updated plan:\n{json.dumps(modified_plan.to_dict(), indent=2)}\n\n"
                        f"User feedback: {feedback}"
                    ),
                ),
            ]
            summary_model = self._select_model(AgentRole.ROOT, "update message")
            summary_response = await self._root_backend.generate(
                model=summary_model,
                messages=summary_messages,
                temperature=self.config.root_temperature,
                max_tokens=self.config.root_max_tokens,
            )

            return ResponseMessage(
                type="plan",
                message=summary_response.content,
                plan=modified_plan.to_dict(),
            )

        except Exception as exc:
            logger.exception("Orchestrator.modify_plan failed: %s", exc)
            return ResponseMessage(
                type="error",
                message=f"Failed to modify the plan: {exc}",
                error=str(exc),
            )

    async def deny_plan(self, plan_id: str) -> ResponseMessage:
        """
        Reject the current pending plan and clear orchestrator state.

        Mirrors ``ResearchAgent.deny_plan``.

        Parameters
        ----------
        plan_id:
            Must match the id of the current ``_pending_plan``.

        Returns
        -------
        ResponseMessage
            ``type="plan_denied"`` with a helpful follow-up message.
        """
        if not self._pending_plan or self._pending_plan.id != plan_id:
            return ResponseMessage(
                type="error",
                message="No pending plan found with the given id. Cannot deny.",
            )

        original_query = self.context.get_step_result("query") or ""
        previous_plan = self._pending_plan
        self._reset_state()

        try:
            messages = [
                Message(
                    role="system",
                    content="You are a research planning assistant.",
                ),
                Message(
                    role="user",
                    content=(
                        "The research plan below was rejected by the user. "
                        "Provide a brief acknowledgement and suggest how they might "
                        "refine their query or approach.\n\n"
                        f"Original query: {original_query}\n\n"
                        f"Rejected plan:\n{json.dumps(previous_plan.to_dict(), indent=2)}"
                    ),
                ),
            ]
            model = self._select_model(AgentRole.ROOT, "plan denial message")
            response = await self._root_backend.generate(
                model=model,
                messages=messages,
                temperature=self.config.root_temperature,
                max_tokens=self.config.root_max_tokens,
            )
            return ResponseMessage(type="plan_denied", message=response.content)

        except Exception as exc:
            logger.exception("Orchestrator.deny_plan failed: %s", exc)
            return ResponseMessage(
                type="plan_denied",
                message="Plan denied. You may submit a new query at any time.",
            )

    def get_contradictions(self) -> List[Contradiction]:
        """Return all contradictions flagged during the last run."""
        return list(self._contradictions)

    def synthesis_checkpoint(self) -> Dict[str, Any]:
        """Return serializable synthesis state for persistence across restarts.

        Captures the minimum state needed to re-run synthesize() if the server
        is restarted between execute() and synthesize().
        """
        return {
            "query": self.context.get_step_result("query") or "",
            "analyst_output": self._analyst_output or {},
            "step_summaries": {str(k): v for k, v in self._step_summaries.items()},
            "step_sources": {str(k): v for k, v in self._step_sources.items()},
            "contradictions": [c.to_dict() for c in self._contradictions],
        }

    def restore_synthesis_state(self, checkpoint: Dict[str, Any]) -> None:
        """Restore synthesis inputs from a persisted checkpoint.

        Called when recovering a session from disk after a restart.  Only the
        fields consumed by synthesize() are restored — no RAG store chunks
        (synthesize() falls back to analyst_output if the store is empty).
        """
        self._analyst_output = checkpoint.get("analyst_output") or {}
        self._step_summaries = {
            int(k): v for k, v in checkpoint.get("step_summaries", {}).items()
        }
        self._step_sources = {
            int(k): v for k, v in checkpoint.get("step_sources", {}).items()
        }
        self._contradictions = []
        for c_dict in checkpoint.get("contradictions", []):
            self._contradictions.append(
                Contradiction(
                    id=c_dict.get("id", str(uuid.uuid4())),
                    source_a=c_dict.get("source_a", ""),
                    claim_a=c_dict.get("claim_a", ""),
                    source_b=c_dict.get("source_b", ""),
                    claim_b=c_dict.get("claim_b", ""),
                    context=c_dict.get("context", ""),
                    resolved=c_dict.get("resolved", False),
                    resolution=c_dict.get("resolution"),
                    targeted_query=c_dict.get("targeted_query"),
                    contradiction_type=c_dict.get("contradiction_type", "unknown"),
                )
            )
        query = checkpoint.get("query", "")
        if query:
            self.context.store_step_result("query", query)

    def get_agent_pool(self) -> AgentPool:
        """Expose the agent pool for inspection or reconfiguration."""
        return self.agents

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _reset_state(self) -> None:
        self._pending_plan = None
        self._raw_findings = []
        self._analyst_output = None
        self._contradictions = []
        self._qa_retries = 0
        self._step_sources = {}
        self._step_summaries = {}
        self._analyst_recommendations = []
        self._active_tasks = []
        self.context = ResearchContext()
        self._search_store = SearchResultStore(self._long_term_memory)

    def release_memory(self) -> None:
        """Free heavy intermediate data structures after the session is fully complete.

        Called by the session cleanup path to reclaim memory from completed
        sessions that are still held in the SessionStore (pending TTL eviction).
        The replay_log in SessionStore retains the final serialized events so
        reconnecting clients can still replay them; this method only clears the
        Orchestrator's internal working buffers.
        """
        self._raw_findings = []
        self._analyst_output = None
        self._contradictions = []
        self._step_sources = {}
        self._step_summaries = {}
        self._analyst_recommendations = []
        self._active_tasks = []
        self._search_store = SearchResultStore(self._long_term_memory)
        self.context = ResearchContext()

    # ── Prompt builders ─────────────────────────────────────────────────────

    def _root_system_prompt(self) -> str:
        base = """\
You are the Root Orchestrator — a high-reasoning research planning agent.

Your responsibilities:
1. Analyse the user's query deeply to understand the true information need.
2. Identify existing knowledge gaps that must be filled.
3. Decompose the problem into a numbered list of discrete, actionable research
   steps that specialist sub-agents will execute.
4. Each step should be independently executable and produce a verifiable output.
5. Identify which steps can safely run IN PARALLEL (no data dependency on each
   other) and assign them the same "parallel_group" string (e.g. "group_1").
   Steps that depend on the results of earlier steps must have a null
   parallel_group and will be executed sequentially after all parallel groups
   that precede them have finished.

Parallelism rules:
- Assign the same "parallel_group" value to any set of steps whose inputs do
  NOT depend on each other's outputs (e.g. independent web searches on
  different sub-topics).
- Leave "parallel_group" as null for steps that depend on results from prior
  steps (e.g. a cross-comparison step that needs data collected earlier).
- Use short, stable group labels like "group_1", "group_2", etc.

CRITICAL CONSTRAINT: Do NOT include any synthesis, report writing, report
generation, or result-compilation steps in this plan. The pipeline
automatically runs a dedicated ReportComposer agent that synthesizes ALL
research findings into a final structured report after every step completes.
Every step in this plan MUST be an active information-gathering or analysis
task that searches for and retrieves new data.

Return ONLY a JSON object:
{
  "goal": "one-sentence statement of the research objective",
  "knowledge_gaps": ["gap 1", "gap 2", ...],
  "steps": [
    {"id": 1, "description": "...", "parallel_group": "group_1"},
    {"id": 2, "description": "...", "parallel_group": "group_1"},
    {"id": 3, "description": "...", "parallel_group": null}
  ]
}
"""
        research_methods = _load_research_methods()
        if research_methods:
            base += (
                "\n\n## Established Research Methods Playbook\n"
                "Apply the strategies and patterns in this playbook when designing "
                "the research plan:\n\n" + research_methods
            )
        return base

    @staticmethod
    def _build_planning_prompt(query: str) -> str:
        return (
            f"Research Query: {query}\n\n"
            "Analyse this query, identify knowledge gaps, and produce a research plan."
        )

    @staticmethod
    def _build_report_prompt(
        query: str,
        analyst_output: Dict[str, Any],
        contradictions: List[Contradiction],
        all_sources: Optional[List[Dict[str, Any]]] = None,
        rag_evidence: Optional[str] = None,
        long_term_context: Optional[str] = None,
    ) -> str:
        unresolved = [c.to_dict() for c in contradictions if not c.resolved]

        # Full analyst output — no truncation; the RAG store manages context.
        analyst_str = json.dumps(analyst_output, indent=2)

        # Build a numbered source list for the ReportComposer to reference
        source_section = ""
        if all_sources:
            lines = ["Numbered Source List (use these for in-text [N] citations):"]
            for i, src in enumerate(all_sources, start=1):
                title = src.get("title") or src.get("url", "Unknown Source")
                url = src.get("url", "")
                lines.append(f"[{i}] {title} — {url}")
            source_section = "\n".join(lines)

        # RAG evidence block: raw chunks semantically retrieved from the store
        rag_section = (
            f"Supporting Raw Evidence (semantically retrieved — use to enrich "
            f"claims and citations):\n{rag_evidence}\n\n"
            if rag_evidence
            else ""
        )

        # Long-term memory block: findings from prior research sessions
        memory_section = (
            f"Prior Research Context (from long-term memory — use to broaden "
            f"perspective and avoid repeating known findings):\n{long_term_context}\n\n"
            if long_term_context
            else ""
        )

        return (
            f"Research Query: {query}\n\n"
            f"Verified Findings:\n{analyst_str}\n\n"
            + (
                f"Unresolved Contradictions (flag explicitly in the report):\n"
                f"{json.dumps(unresolved, indent=2)}\n\n"
                if unresolved
                else ""
            )
            + rag_section
            + memory_section
            + (f"{source_section}\n\n" if source_section else "")
            + "Compose the final research report following the required PDF-ready format."
        )

    def _extract_sources(self, search_result: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Collect source references from the SearchAgent's output.

        Strategy (two-tier, merged + deduped):
        1. Parse the ``final_message`` JSON for the ``sources`` list if the
           SearchAgent returned valid JSON.
        2. Collect unique source URLs from RAG store chunks stored during this
           step's search phase — these are always populated regardless of how
           the SearchAgent formats its final message.

        Returns an empty list only when neither tier produces results.
        """
        sources: List[Dict[str, Any]] = []
        seen_urls: set = set()

        # Tier 1: parse SearchAgent's structured final_message
        final_msg = search_result.get("final_message", "")
        if final_msg:
            try:
                content = final_msg
                if "```json" in content:
                    content = content.split("```json")[1].split("```")[0].strip()
                elif "```" in content:
                    content = content.split("```")[1].split("```")[0].strip()
                data = json.loads(content)
                for src in data.get("sources", []):
                    url = src.get("url", "")
                    if url and url not in seen_urls:
                        seen_urls.add(url)
                        sources.append(src)
            except Exception:
                pass

        # Tier 2: collect source URLs from RAG store chunks (always populated)
        for chunk in self._search_store._chunks:
            url = chunk.source_url
            if url and url not in seen_urls:
                seen_urls.add(url)
                sources.append({"url": url, "title": url})

        return sources

    # ── Agent dispatch helpers ───────────────────────────────────────────────

    async def _run_search(
        self,
        step: ResearchStep,
        query: str,
        tools: List[Dict[str, Any]],
        extra_context: Optional[str] = None,
        status_sink: Optional[List] = None,
        ltm_context: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Run the SearchAgent for a single plan step.

        Parameters
        ----------
        status_sink:
            Optional list to accumulate ``ResponseMessage`` status events as
            each search iteration completes.  The caller can flush this list
            with ``yield`` after the function returns to surface per-iteration
            progress to the WebSocket client.
        """
        prompt_parts = [
            f"Overall Research Goal: {query}",
            f"Current Step: {step.description}",
        ]
        if self.research_depth == "deep":
            prompt_parts.append(
                "Depth instruction: This is a DEEP research session. "
                "Use highly specific, targeted queries. Prefer primary sources, "
                "academic papers, official documentation, and authoritative data over "
                "general summaries. Retrieve multiple independent sources per claim "
                "and explore sub-topics thoroughly before concluding."
            )
        if ltm_context:
            prompt_parts.append(
                "Relevant knowledge from prior research sessions (long-term memory). "
                "Use it to avoid redundant searching and to build on what is already "
                "known — treat it as prior context to verify, not as ground truth:\n"
                + ltm_context
            )
        if extra_context:
            prompt_parts.append(f"Additional Context:\n{extra_context}")
        prompt_parts.append(
            "Search for and retrieve all relevant raw data for this step."
        )

        # Select model for SearchAgent (typically a faster, cheaper one since it may need to make multiple calls in the tool loop)
        model = self._select_model(AgentRole.SEARCH, step.description)
        if not model:
            return {
                "final_message": "No suitable model available for SearchAgent.",
                "_tools_used": [],
            }

        # Run the search tool loop: iterate until the agent stops requesting tools
        # or the iteration cap is reached. Tool results are fed back into
        # execution_messages so each subsequent LLM call has full context.
        result = {}
        iteration = 0
        tool_calls = []
        execution_messages: List[Message] = []
        while iteration < config.max_iterations:
            # Sliding window: evict the oldest messages when the history grows
            # too large so that prompt size stays bounded on every iteration.
            # Full content is preserved in the SearchResultStore RAG store and
            # will be surfaced via in-process cosine ranking for downstream agents.
            if len(execution_messages) > config.max_search_history_messages:
                execution_messages = execution_messages[
                    -config.max_search_history_messages :
                ]

            if status_sink is not None:
                status_sink.append(
                    ResponseMessage(
                        type="status",
                        message=(
                            f"[Search] Step {step.id} — "
                            f"iteration {iteration + 1}/{config.max_iterations}…"
                        ),
                    )
                )

            response = await self.agents.search.run(
                prompt="\n\n".join(prompt_parts),
                model_override=model,
                tools=tools,
                extra_messages=execution_messages or None,
                temperature=self.config.search_temperature,
                max_tokens=self.config.search_max_tokens,
            )
            if response.finish_reason == "error":
                execution_messages.append(
                    Message(
                        role="user", content=f"SearchAgent error: {response.content}"
                    )
                )
                iteration += (
                    1  # always advance to prevent infinite retry on persistent errors
                )
                continue

            if not response.tool_calls:
                result["final_message"] = response.content
                break  # No more tool calls, assume the agent is done

            # Record the assistant turn so the model sees its own prior calls
            execution_messages.append(
                Message(
                    role="assistant",
                    content=response.content or "[tool calls]",
                )
            )

            tool_calls.extend(response.tool_calls)
            for tool_call in response.tool_calls:
                try:
                    execution_messages.append(
                        Message(
                            role="user",
                            content=f"Tool call: {tool_call.name} with args {tool_call.parameters}",
                        )
                    )

                    # Call tool and feed result back into message history
                    tool_result = await self.mcp_registry.call_tool(
                        tool_call.name, tool_call.parameters
                    )
                    if "error" in tool_result:
                        execution_messages.append(
                            Message(
                                role="user",
                                content=f"Tool error: {tool_result['error']}",
                            )
                        )
                        continue

                    # ── Store full result in RAG store BEFORE truncating ──
                    # First run the distillation filter: strip navigation chrome,
                    # ads, and off-topic boilerplate so that only step-relevant
                    # facts are chunked into the RAG store.  The filter uses a
                    # fast/cheap model and targets config.distill_max_chars output.
                    source_url = (
                        tool_call.parameters.get("url")
                        or tool_call.parameters.get("query")
                        or ""
                    )
                    distilled_result = await self._distill_tool_result(
                        tool_result, step.description
                    )
                    await self._search_store.add(
                        tool_name=tool_call.name,
                        result=distilled_result,
                        step_id=step.id,
                        source_url=source_url,
                    )

                    # Build the full string first so we know its length, then
                    # truncate the copy injected into execution_messages.  The
                    # RAG store above already holds every byte losslessly.
                    tool_result_raw = (
                        f"Tool result for {tool_call.name}: {json.dumps(tool_result)}"
                    )
                    if len(tool_result_raw) > config.max_tool_result_chars_in_message:
                        tool_result_str = (
                            tool_result_raw[: config.max_tool_result_chars_in_message]
                            + f"\n[... {len(tool_result_raw) - config.max_tool_result_chars_in_message}"
                            " additional chars stored in RAG store for retrieval ...]"
                        )
                    else:
                        tool_result_str = tool_result_raw
                    execution_messages.append(
                        Message(
                            role="user",
                            content=tool_result_str,
                        )
                    )

                except Exception as e:
                    logger.error(
                        "Error executing tool call '%s': %s",
                        getattr(tool_call, "name", "<unknown>"),
                        e,
                    )
                    continue  # Skip this tool call and continue with the next one

            iteration += 1

        # Collect the names of MCP tools invoked by the search agent
        tool_call_names: List[str] = [tc.name for tc in tool_calls]

        # Remove any duplicate tool names while preserving order
        seen = set()
        tool_call_names = [x for x in tool_call_names if not (x in seen or seen.add(x))]
        result["_tools_used"] = tool_call_names
        # Note: result["final_message"] is set inside the loop when the agent
        # finishes; do not overwrite it here.
        return result

    async def _run_analyst(
        self,
        step: ResearchStep,
        query: str,
        search_output: Dict[str, Any],
        tools: List[Dict[str, Any]],
        extra_context: Optional[str] = None,
        ltm_context: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Run the AnalystAgent over the search results for a single step.

        Context strategy
        ----------------
        1. Step-scoped RAG retrieval: top-k chunks from THIS step's search
           results only (step_id_filter), semantically ranked.
        2. Cross-step corroboration: top-3 chunks from OTHER completed steps
           (exclude_step_id) so the analyst can cross-reference claims across
           independent searches.
        3. Fallback: if the RAG store has no chunks for this step, extract
           only source knowledge_snippets from the SearchAgent's final_message
           (stripping coverage_notes meta-commentary that pollutes extraction).

        Source excerpts are numbered so the analyst can cite them precisely.
        """
        # ── 1. Step-scoped RAG retrieval ─────────────────────────────────────
        rag_context = await self._search_store.retrieve(
            query=f"{step.description} {query}",
            top_k=config.analyst_top_k,
            step_id_filter=step.id,
        )

        if rag_context:
            # Number each chunk block so the analyst can cite [Source N]
            blocks = rag_context.split("\n\n---\n\n")
            numbered_blocks = [
                f"[Source {i + 1}] {block}" for i, block in enumerate(blocks)
            ]
            search_context = (
                "Relevant source excerpts for this step (semantically ranked).\n"
                "Cite sources using [Source N] notation in each claim's 'sources' field:\n\n"
                + "\n\n---\n\n".join(numbered_blocks)
            )
        else:
            # Fallback: extract only source knowledge_snippets from the
            # SearchAgent's structured final_message, explicitly excluding the
            # coverage_notes field which causes ~40% meta-commentary pollution.
            fallback_sources: List[str] = []
            final_msg = search_output.get("final_message", "")
            if final_msg:
                try:
                    content = final_msg
                    if "```json" in content:
                        content = content.split("```json")[1].split("```")[0].strip()
                    elif "```" in content:
                        content = content.split("```")[1].split("```")[0].strip()
                    parsed = json.loads(content)
                    for src in parsed.get("sources", [])[:10]:
                        url = src.get("url", "")
                        snippet = src.get("knowledge_snippet", "") or src.get(
                            "title", ""
                        )
                        if snippet:
                            label = f"[{url}]" if url else ""
                            fallback_sources.append(
                                f"[Source {len(fallback_sources) + 1}] {label}\n{snippet}"
                            )
                except Exception:
                    pass

            if fallback_sources:
                search_context = (
                    "Source knowledge excerpts (cite using [Source N] notation):\n\n"
                    + "\n\n---\n\n".join(fallback_sources)
                )
            else:
                # Last resort: dump the search_output but strip coverage_notes
                # and internal bookkeeping keys that pollute claim extraction.
                stripped = {
                    k: v
                    for k, v in search_output.items()
                    if k not in ("coverage_notes", "_tools_used")
                }
                raw_str = json.dumps(stripped, indent=2)
                if len(raw_str) > config.max_analyst_fallback_chars:
                    raw_str = (
                        raw_str[: config.max_analyst_fallback_chars]
                        + f"\n[... {len(raw_str) - config.max_analyst_fallback_chars} chars stored in RAG store ...]"
                    )
                search_context = "Search Results:\n" + raw_str

        # ── Surface the SearchAgent's self-identified coverage gaps ──────────
        # so the analyst can flag under-covered claims instead of silently
        # extracting over them.  The search phase records these gaps in the
        # "coverage_notes" field, which the primary RAG path would otherwise
        # drop entirely.
        coverage_note = ""
        _final_msg = search_output.get("final_message", "")
        if _final_msg:
            try:
                _cov = _final_msg
                if "```json" in _cov:
                    _cov = _cov.split("```json")[1].split("```")[0].strip()
                elif "```" in _cov:
                    _cov = _cov.split("```")[1].split("```")[0].strip()
                coverage_note = str(
                    json.loads(_cov).get("coverage_notes", "") or ""
                ).strip()
            except Exception:
                coverage_note = ""

        # ── 2. Cross-step corroboration context ──────────────────────────────
        # Retrieve top-5 chunks from OTHER completed steps so the analyst can
        # triangulate claims against independently-gathered evidence.
        cross_step_context = await self._search_store.retrieve(
            query=f"{step.description} {query}",
            top_k=5,
            exclude_step_id=step.id,
        )

        # ── 3. Structured claims from prior steps ────────────────────────────
        # Pass curated analytical output (claims + tensions) from already-
        # completed steps so the analyst can cross-reference against prior
        # structured findings — not just raw text chunks.
        prior_claims_block = ""
        if self._analyst_output:
            prior_claims = self._analyst_output.get("claims", [])
            prior_tensions = self._analyst_output.get("tensions", [])
            if prior_claims or prior_tensions:
                parts_pc: List[str] = []
                if prior_claims:
                    claim_lines = []
                    for c in prior_claims[-20:]:  # last 20 claims
                        claim_text = c.get("claim", "")
                        confidence = c.get("confidence", "")
                        if claim_text:
                            src_list = c.get("sources", [])
                            src_str = (
                                f" [{', '.join(src_list[:2])}]"
                                if isinstance(src_list, list) and src_list
                                else ""
                            )
                            claim_lines.append(
                                f"- {claim_text}{src_str}"
                                + (f" ({confidence})" if confidence else "")
                            )
                    if claim_lines:
                        parts_pc.append(
                            "Claims from prior steps:\n" + "\n".join(claim_lines)
                        )
                if prior_tensions:
                    tension_lines = [
                        f"- {t.get('topic', '')}: {t.get('position_a', '')} vs {t.get('position_b', '')}"
                        for t in prior_tensions[-5:]
                        if t.get("topic")
                    ]
                    if tension_lines:
                        parts_pc.append("Known tensions:\n" + "\n".join(tension_lines))
                prior_claims_block = "\n\n".join(parts_pc)

        prompt_parts = [
            f"Overall Research Goal: {query}",
            f"Current Step: {step.description}",
            search_context,
        ]
        if coverage_note:
            prompt_parts.append(
                "Search-phase coverage notes (gaps the search agent flagged — "
                "treat affected claims as lower-confidence and note the missing "
                "evidence rather than overstating):\n" + coverage_note
            )
        if cross_step_context:
            prompt_parts.append(
                "Corroborating evidence from prior steps "
                "(use to cross-reference and verify claims):\n\n" + cross_step_context
            )
        if prior_claims_block:
            prompt_parts.append(
                "Structured findings from prior steps "
                "(use to corroborate, extend, or identify conflicts with your new claims):\n\n"
                + prior_claims_block
            )
        if ltm_context:
            prompt_parts.append(
                "Relevant knowledge from prior research sessions (long-term memory). "
                "Corroborate your new claims against it or extend it — do not blindly "
                "trust it; flag conflicts with prior knowledge as tensions:\n"
                + ltm_context
            )
        if extra_context:
            prompt_parts.append(f"Additional Context:\n{extra_context}")
        if self.research_depth == "deep":
            prompt_parts.append(
                "Depth instruction: This is a DEEP research session. "
                "Apply rigorous extraction methods: require at least two independent "
                "sources per claim, explicitly flag any claim supported by only a single "
                "source, distinguish primary evidence from secondary commentary, and "
                "surface ALL tensions or contradictions between sources rather than "
                "resolving them silently."
            )
        prompt_parts.append(
            "Extract claims, cross-reference sources, and surface any tensions."
        )

        model = self._select_model(AgentRole.ANALYST, step.description)
        response = await self.agents.analyst.run(
            prompt="\n\n".join(prompt_parts),
            model_override=model,
            temperature=self.config.analyst_temperature,
            max_tokens=self.config.analyst_max_tokens,
        )

        if response.finish_reason == "error":
            raise RuntimeError(
                f"AnalystAgent LLM call failed for step {step.id} (finish_reason='error'). "
                "Check model backend logs for details."
            )

        try:
            content = response.content
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0].strip()
            elif "```" in content:
                content = content.split("```")[1].split("```")[0].strip()
            return json.loads(content)
        except Exception:
            return {"claims": [], "tensions": [], "analyst_notes": response.content}

    # ── Output aggregation ───────────────────────────────────────────────────

    @staticmethod
    def _merge_analyst_outputs(
        existing: Optional[Dict[str, Any]],
        new: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Merge incremental AnalystAgent outputs into a single aggregate.

        Claims are deduplicated by lowercased claim text; tensions are
        deduplicated by (topic, source_a, source_b) tuple.  No hard caps are
        applied — the RAG store handles context management instead.
        """
        if existing is None:
            return new

        combined_claims = existing.get("claims", []) + new.get("claims", [])
        combined_tensions = existing.get("tensions", []) + new.get("tensions", [])

        # Deduplicate claims by lowercased claim text
        seen_claims: set = set()
        deduped_claims: List[Dict[str, Any]] = []
        for c in combined_claims:
            key = str(c.get("claim", "")).lower().strip()
            if key and key not in seen_claims:
                seen_claims.add(key)
                deduped_claims.append(c)

        # Deduplicate tensions by (topic, source_a, source_b) tuple
        seen_tensions: set = set()
        deduped_tensions: List[Dict[str, Any]] = []
        for t in combined_tensions:
            key = (
                str(t.get("topic", "")).lower().strip(),
                str(t.get("source_a", "")).lower().strip(),
                str(t.get("source_b", "")).lower().strip(),
            )
            if key not in seen_tensions:
                seen_tensions.add(key)
                deduped_tensions.append(t)

        return {
            "claims": deduped_claims,
            "tensions": deduped_tensions,
            "analyst_notes": (
                str(existing.get("analyst_notes") or "")
                + "\n\n"
                + str(new.get("analyst_notes") or "")
            ).strip(),
        }

    # ── Plan parsing ─────────────────────────────────────────────────────────

    @staticmethod
    def _parse_plan(raw: str, query: str = "") -> ResearchPlan:
        """Parse the Root agent's JSON output into a ``ResearchPlan``."""
        content = raw
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0].strip()
        elif "```" in content:
            content = content.split("```")[1].split("```")[0].strip()

        try:
            data = json.loads(content)
            steps = [
                ResearchStep(
                    id=s["id"],
                    name=f"Step {s['id']}",
                    description=s["description"],
                    status=StepStatus.PENDING,
                    parallel_group=s.get("parallel_group") or None,
                )
                for s in data.get("steps", [])
            ]
            return ResearchPlan(goal=data.get("goal", "Research goal"), steps=steps)
        except Exception as exc:
            logger.warning(
                "Could not parse plan JSON (%s) — using query-based fallback. "
                "Raw response head: %.300s",
                exc,
                raw,
            )
            # Fallback: a single step that still reflects the user's query so a
            # parse failure does not silently discard the research topic.
            topic = (query or "the research topic").strip()
            return ResearchPlan(
                goal=f"Research: {topic[:200]}",
                steps=[
                    ResearchStep(
                        id=1,
                        name="Step 1",
                        description=f"Gather comprehensive information on: {topic[:300]}",
                        status=StepStatus.PENDING,
                    )
                ],
            )

    # ── Long-term memory helpers ─────────────────────────────────────────────

    async def _recall_memories(self, query: str, limit: int = 5) -> str:
        """
        Query the in-process long-term memory store for context relevant to *query*.

        Combines two sources:
        1. Flat vector search over stored memories (raw evidence + claims)
        2. Graph-aware recall from the knowledge graph (entities, relationships,
           community summaries)

        Returns a plain-text block, or an empty string if the store is
        unavailable or returns no results.  All exceptions are swallowed so a
        missing memory store never breaks the research pipeline.
        """
        if self._long_term_memory is None:
            return ""

        parts: List[str] = []

        # ── Graph-aware recall (entities + relationships + communities) ──────
        try:
            # Scale entity_limit by graph size so larger graphs surface more
            # context without overloading small or empty graphs.
            graph_stats = await self._long_term_memory.graph.stats()
            entity_count = graph_stats.get("entities", 0)
            dynamic_entity_limit = min(15, max(5, entity_count // 20))

            graph_context = await self._long_term_memory.graph.recall_graph_context(
                query,
                entity_limit=dynamic_entity_limit,
                max_hops=2,
                min_confidence=0.3,
                include_contradictions=True,
            )
            if graph_context:
                parts.append(graph_context)
        except Exception as exc:
            logger.debug("[Orchestrator] Graph recall skipped: %s", exc)

        # ── Flat vector search (raw memories) ────────────────────────────────
        try:
            memories = await self._long_term_memory.find_similar(
                query, limit=limit, min_similarity=0.5
            )
            if memories:
                lines = []
                for m in memories:
                    content = m.get("content", "")
                    category = m.get("category", "")
                    if content:
                        lines.append(f"[{category}] {content}" if category else content)
                if lines:
                    parts.append("PRIOR FINDINGS:\n" + "\n".join(lines))
        except Exception as exc:
            logger.debug("[Orchestrator] Flat memory recall skipped: %s", exc)

        if not parts:
            logger.debug(
                "[Orchestrator] No relevant memories found for query: %s", query
            )
            return ""

        return "\n\n".join(parts)

    async def _gather_uploaded_file_context(self, query: str) -> str:
        """
        Query the file handler MCP server for user-uploaded files and return
        a formatted text block of their contents for use during plan generation.

        Returns an empty string if no files are present, the file handler is
        unavailable, or an error occurs — never raises.
        """
        _MAX_FILES = 5
        _MAX_CHARS_PER_FILE = 10_000

        logger.debug(
            "[Orchestrator] Checking for uploaded files (query: %.60s…)", query
        )
        try:
            raw = await self.mcp_registry.call_tool("list_files", {})
        except Exception as exc:
            logger.debug("[Orchestrator] File handler list_files unavailable: %s", exc)
            return ""

        # MCP tool results come back as a list of TextContent envelopes:
        # [{"type": "text", "text": "<json-serialised return value>"}]
        text = ""
        if isinstance(raw, list) and raw:
            first = raw[0]
            text = first.get("text", "") if isinstance(first, dict) else str(first)

        if not text:
            return ""

        try:
            files = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return ""

        # MCP SDK ≥1.26 with outputSchema wraps return values as {"result": value}
        if isinstance(files, dict):
            files = files.get("result", [])

        if not isinstance(files, list) or not files:
            return ""

        parts: list = []
        for file_info in files[:_MAX_FILES]:
            abs_path = file_info.get("absolute_path", "")
            name = file_info.get("name", abs_path)
            if not abs_path:
                continue
            try:
                raw_content = await self.mcp_registry.call_tool(
                    "read_file", {"file_path": abs_path}
                )
                content_text = ""
                if isinstance(raw_content, list) and raw_content:
                    first = raw_content[0]
                    content_text = (
                        first.get("text", "") if isinstance(first, dict) else str(first)
                    )
                # MCP SDK ≥1.26 with outputSchema wraps string results as {"result": "..."}
                if content_text:
                    try:
                        parsed = json.loads(content_text)
                        if isinstance(parsed, dict) and isinstance(
                            parsed.get("result"), str
                        ):
                            content_text = parsed["result"]
                    except (json.JSONDecodeError, TypeError, ValueError):
                        pass  # content_text is plain text, use as-is
                if not content_text or not content_text.strip():
                    continue
                if len(content_text) > _MAX_CHARS_PER_FILE:
                    content_text = content_text[:_MAX_CHARS_PER_FILE] + "\n[truncated]"
                parts.append(f"=== {name} ===\n{content_text.strip()}")
            except Exception as exc:
                logger.debug(
                    "[Orchestrator] Could not read uploaded file '%s': %s", name, exc
                )

        return "\n\n".join(parts)

    async def _store_analyst_findings(
        self, step: ResearchStep, analyst_result: Dict[str, Any]
    ) -> None:
        """
        Persist the most important QA-vetted claims from *analyst_result* to
        the in-process long-term memory store so they are available in future
        research sessions.

        Also extracts entities and relationships from the claims and stores them
        in the knowledge graph for cross-session structural recall.

        Only the top 8 claims are stored to keep each memory entry concise.
        Exceptions are swallowed so a memory-store error never blocks the pipeline.
        """
        if self._long_term_memory is None:
            return
        try:
            claims = analyst_result.get("claims", [])
            key_claims = [c.get("claim", "") for c in claims[:8] if c.get("claim")]
            if not key_claims:
                return
            content = (
                f"Research step: {step.description}\n"
                "Key findings:\n" + "\n".join(f"- {c}" for c in key_claims)
            )
            await self._long_term_memory.store(
                content=content,
                category="research_finding",
                importance=7,
                tags=["orchestrator", "research"],
            )
            logger.debug(
                "[Orchestrator] Stored %d claims to long-term memory for step %d",
                len(key_claims),
                step.id,
            )
        except Exception as exc:
            logger.debug("[Orchestrator] Memory store skipped: %s", exc)

        # ── Extract entities and relationships into the knowledge graph ──────
        await self._extract_graph_triples(step, analyst_result)

    async def _extract_graph_triples(
        self, step: ResearchStep, analyst_result: Dict[str, Any]
    ) -> None:
        """
        Use a single LLM call to extract entities and relationships from
        QA-vetted analyst claims, then store them in the knowledge graph.

        Only called from ``_store_analyst_findings`` — i.e. only on claims that
        have already passed the QA loop.  One LLM call per step, not per claim.
        """
        if self._long_term_memory is None or not self._long_term_memory.graph.available:
            return

        claims = analyst_result.get("claims", [])
        claim_texts = [c.get("claim", "") for c in claims[:8] if c.get("claim")]
        if not claim_texts:
            return

        # Optionally inject a brief graph context so the LLM can flag
        # when a new claim contradicts something already in the graph.
        existing_ctx = ""
        try:
            existing_ctx = await self._long_term_memory.graph.recall_graph_context(
                step.description, entity_limit=3, max_hops=1
            )
        except Exception:
            pass

        graph_hint = (
            f"\n\nExisting knowledge graph context (flag contradictions with "
            f"the 'contradicts' field when a new finding conflicts with these):\n"
            f"{existing_ctx}"
            if existing_ctx
            else ""
        )

        extraction_prompt = (
            "Extract entities, relationships, and claims from these research findings.\n"
            "Return ONLY valid JSON with this exact structure:\n"
            '{"entities": [{"name": "<entity name>", '
            '"type": "<person|organization|technology|concept|event|location|metric>", '
            '"description": "<one-sentence description>", '
            '"parent_type": "<broader category name, or empty string>", '
            '"properties": {"<type-specific key>": "<value>"}}], '
            '"relationships": [{"source": "<source entity name>", '
            '"target": "<target entity name>", '
            '"relation": "<verb phrase describing the relationship>", '
            '"label": "<CAUSES|ENABLES|PREVENTS|REQUIRES|PART_OF|USES|PRODUCES|'
            "COMPETES_WITH|AFFILIATED_WITH|AUTHORED_BY|FUNDED_BY|PRECEDED_BY|"
            'OCCURRED_AT|SUPPORTS|REFUTES|MENTIONS|RELATES_TO>", '
            '"evidence": "<brief supporting evidence>", '
            '"source_url": "<source URL if explicitly mentioned, or empty string>", '
            '"contradicts_prior": false}], '
            '"claims": [{"text": "<assertion extracted from findings>", '
            '"confidence": 0.8, '
            '"entity_names": ["<entity mentioned in claim>"], '
            '"source_url": "<URL if available, or empty string>"}]}\n\n'
            "Type-specific properties by entity type:\n"
            "  person: affiliation, role, expertise_areas (comma-separated)\n"
            "  organization: org_type (company/academic/govt/ngo), industry, headquarters\n"
            "  technology: tech_category (language/framework/db/tool/platform), version, license, maturity (emerging/stable/deprecated)\n"
            "  event: start_date, end_date, event_type (conference/release/incident/discovery), location\n"
            "  location: geo_type (city/country/region), parent_location\n"
            "  metric: value, unit, measurement_date, trend (increasing/decreasing/stable)\n"
            "  concept: domain, abstraction_level (specific/general/abstract)\n\n"
            "Findings:\n" + "\n".join(f"- {c}" for c in claim_texts) + graph_hint
        )

        try:
            model = self._select_model(AgentRole.ROOT, "graph_extraction")
            response: ModelResponse = await self._root_backend.generate(
                model=model,
                messages=[
                    Message(
                        role="system",
                        content=(
                            "You are a precise entity/relationship extractor. "
                            "Output ONLY valid JSON. Do not wrap in markdown code blocks."
                        ),
                    ),
                    Message(role="user", content=extraction_prompt),
                ],
            )

            # Parse the LLM's JSON response
            raw = response.content.strip()
            # Strip markdown code fences if present
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[-1]
                if raw.endswith("```"):
                    raw = raw[: raw.rfind("```")]
                raw = raw.strip()
            triples = json.loads(raw)

            session_id = self.session_id or ""

            # Store entities
            entities = triples.get("entities", [])
            stored_entities = 0
            for entity in entities:
                name = entity.get("name", "").strip()
                if not name:
                    continue
                # Extract type-specific properties from the LLM response
                type_props = entity.get("properties", {})
                if isinstance(type_props, dict):
                    # Clean empty string values
                    type_props = {k: v for k, v in type_props.items() if v}
                else:
                    type_props = {}
                result = await self._long_term_memory.graph.upsert_entity(
                    name=name,
                    entity_type=entity.get("type", "concept"),
                    description=entity.get("description", ""),
                    session_id=session_id,
                    properties=type_props,
                )
                if result.get("success"):
                    stored_entities += 1
                    # Store IS_A hierarchy if the LLM provided a parent category
                    parent_type = entity.get("parent_type", "").strip()
                    if parent_type and parent_type.lower() != name.lower():
                        # Ensure parent entity exists
                        await self._long_term_memory.graph.upsert_entity(
                            name=parent_type,
                            entity_type="concept",
                            description=f"Broader category: {parent_type}",
                            session_id=session_id,
                        )
                        await self._long_term_memory.graph.store_hierarchy(
                            child_name=name, parent_name=parent_type
                        )

            # Store relationships
            relationships = triples.get("relationships", [])
            stored_rels = 0
            for rel in relationships:
                source = rel.get("source", "").strip()
                target = rel.get("target", "").strip()
                relation = rel.get("relation", "").strip()
                if not source or not target or not relation:
                    continue
                # Pass explicit label if the LLM provided one
                rel_label = rel.get("label", "").strip() or None
                rel_result = await self._long_term_memory.graph.store_relationship(
                    source=source,
                    target=target,
                    relation=relation,
                    evidence=rel.get("evidence", ""),
                    session_id=session_id,
                    step_id=step.id,
                    relationship_label=rel_label,
                )
                if rel_result.get("success"):
                    stored_rels += 1
                    # Store document and link if the LLM identified a source URL
                    source_url = rel.get("source_url", "").strip()
                    if source_url and source_url.startswith("http"):
                        await self._long_term_memory.graph.store_document(
                            url=source_url, session_id=session_id
                        )
                        await self._long_term_memory.graph.link_document_to_entity(
                            document_url=source_url,
                            entity_name=source,
                            relationship_label="SOURCED_FROM",
                        )

            # Store claims
            extracted_claims = triples.get("claims", [])
            stored_claims = 0
            for claim_obj in extracted_claims:
                claim_text = claim_obj.get("text", "").strip()
                if not claim_text:
                    continue
                claim_result = await self._long_term_memory.graph.store_claim(
                    text=claim_text,
                    confidence=claim_obj.get("confidence", 0.5),
                    source_session=session_id,
                    step_id=step.id,
                    entity_names=claim_obj.get("entity_names", []),
                )
                if claim_result.get("success"):
                    stored_claims += 1
                    # Link claim to source document if URL provided
                    claim_url = claim_obj.get("source_url", "").strip()
                    if claim_url and claim_url.startswith("http"):
                        await self._long_term_memory.graph.store_document(
                            url=claim_url, session_id=session_id
                        )

            if stored_entities or stored_rels or stored_claims:
                self._graph_mutations_since_community_update += (
                    stored_entities + stored_rels + stored_claims
                )
                logger.debug(
                    "[Orchestrator] Graph extraction for step %d: "
                    "%d entities, %d relationships, %d claims.",
                    step.id,
                    stored_entities,
                    stored_rels,
                    stored_claims,
                )

        except json.JSONDecodeError as exc:
            logger.debug(
                "[Orchestrator] Graph extraction JSON parse failed for step %d: %s",
                step.id,
                exc,
            )
        except Exception as exc:
            logger.debug(
                "[Orchestrator] Graph extraction skipped for step %d: %s",
                step.id,
                exc,
            )

    # ── Hierarchical distillation helpers ───────────────────────────────────

    async def _distill_tool_result(
        self,
        tool_result: Any,
        step_description: str,
    ) -> Dict[str, Any]:
        """
        **Pillar 1 — Hierarchical Distillation filter.**

        Strip navigation chrome, ads, boilerplate, and off-topic content from
        a raw MCP tool result so that only facts relevant to *step_description*
        are stored in the ``SearchResultStore``.

        The filter makes a single cheap/fast LLM call and targets
        ``config.distill_max_chars`` of output — so 10,000 tokens of raw web text
        may be reduced to ~500 tokens of step-relevant facts before chunking.

        Falls back to the original *tool_result* if the LLM call fails so the
        pipeline is never blocked by a distillation error.

        Parameters
        ----------
        tool_result:
            The raw dict / list returned by ``mcp_registry.call_tool()``.
        step_description:
            The current step's description — used as the relevance filter.

        Returns
        -------
        Dict[str, Any]
            A ``{"text": "<distilled content>"}`` dict suitable for passing
            directly to ``SearchResultStore.add()``.  On failure the original
            result is returned unchanged.
        """
        raw_text = SearchResultStore._extract_text(tool_result)
        if not raw_text or len(raw_text) <= config.distill_max_chars:
            # Already small enough — no distillation needed
            return tool_result

        fast_model = self._select_model(AgentRole.SEARCH, "filter extract")
        prompt = (
            f"Sub-question / research step: {step_description}\n\n"
            f"Raw source text (may contain boilerplate, ads, navigation):\n"
            f"{raw_text[:12000]}\n\n"
            "TASK: Perform a relevance pass.  Extract ONLY the specific facts, "
            "numbers, quotes, and claims that directly answer the sub-question "
            "above.  Discard everything else (navigation, ads, cookie notices, "
            "unrelated content).  Output a tight bulleted list.  "
            f"Maximum output: {config.distill_max_chars} characters."
        )
        try:
            response = await self.agents.search.run(
                prompt=prompt,
                model_override=fast_model,
                temperature=self.config.search_temperature,
                max_tokens=self.config.search_max_tokens,
            )
            distilled = response.content.strip()
            if distilled:
                logger.debug(
                    "[Distill] step=%s: %d → %d chars",
                    step_description[:50],
                    len(raw_text),
                    len(distilled),
                )
                return {"text": distilled}
        except Exception as exc:
            logger.debug("[Distill] Distillation failed (%s); using raw result", exc)

        return tool_result

    async def _generate_step_summary(
        self,
        step: ResearchStep,
        analyst_result: Dict[str, Any],
    ) -> str:
        """
        **Pillar 2 — Active Context / Scratchpad.**

        Generate a concise (≤``config.step_summary_max_chars``) bullet-point summary
        for a completed step.  This summary is stored in ``_step_summaries``
        and forms the Orchestrator's "active context" — the CEO's weekly
        briefing.

        The Outline Phase of multi-pass synthesis reads ONLY these summaries,
        keeping the noise-to-signal ratio low.  Full analyst data stays in the
        ``SearchResultStore`` and ``ResearchContext`` (the "scratchpad") and is
        recalled on demand during Section Drafting.

        Uses the fast model to minimize latency.
        """
        claims = analyst_result.get("claims", [])
        tensions = analyst_result.get("tensions", [])
        notes = analyst_result.get("analyst_notes", "")

        if not claims and not notes:
            return f"Step {step.id}: {step.description} — no findings recorded."

        fast_model = self._select_model(AgentRole.SEARCH, "summarize")
        claim_bullets = "\n".join(
            f"- {c.get('claim', '')}" for c in claims[:15] if c.get("claim")
        )
        tension_bullets = (
            "\nTensions: "
            + "; ".join(t.get("topic", "") for t in tensions[:5] if t.get("topic"))
            if tensions
            else ""
        )
        input_text = f"{claim_bullets}{tension_bullets}"
        if notes:
            input_text += f"\nNotes: {str(notes)[:500]}"

        prompt = (
            f"Step: {step.description}\n\n"
            f"Findings:\n{input_text}\n\n"
            "Write a high-density bullet-point summary of the key facts found "
            "in this step.  Maximum 8 bullets.  Each bullet must be a discrete, "
            "verifiable fact.  Include any tensions or disagreements between sources. "
            f"Maximum total length: {config.step_summary_max_chars} characters."
        )
        try:
            response = await self.agents.search.run(
                prompt=prompt,
                model_override=fast_model,
                temperature=self.config.search_temperature,
                max_tokens=self.config.search_max_tokens,
            )
            summary = response.content.strip()
            if summary:
                # Hard-cap at step_summary_max_chars
                return summary[: config.step_summary_max_chars]
        except Exception as exc:
            logger.debug("[StepSummary] Generation failed (%s); using fallback", exc)

        # Fallback: first 3 claims as bullets
        fallback_bullets = "\n".join(
            f"• {c.get('claim', '')}" for c in claims[:3] if c.get("claim")
        )
        return fallback_bullets[: config.step_summary_max_chars] or (
            f"Step {step.id}: {step.description}"
        )

    # ── Model selection ──────────────────────────────────────────────────────

    def _select_model(self, role: AgentRole, task_hint: str = "") -> Optional[str]:
        """
        Return the most appropriate model name for a given agent role.

        Per-agent model overrides (root_model_override, search_model_override,
        analyst_model_override, qa_model_override) on Config take precedence over
        the heavy/light defaults.  Leave them None to use the backend defaults.
        """
        cfg = self.config
        backend = self._root_backend  # used only to identify the provider type

        # ── Per-agent explicit overrides (set via UI settings) ────────────
        _role_override = {
            AgentRole.ROOT: cfg.root_model_override,
            AgentRole.REPORT: cfg.root_model_override,  # report uses root/heavy model
            AgentRole.SEARCH: cfg.search_model_override,
            AgentRole.ANALYST: cfg.analyst_model_override,
            AgentRole.LOOP: cfg.qa_model_override,
        }
        override = _role_override.get(role)
        if override:
            return override

        # ── Fallback: heavy / light model selection ───────────────────────
        # Roles that always warrant the highest-capability model
        high_reasoning_roles = {AgentRole.ROOT, AgentRole.REPORT, AgentRole.LOOP}

        # Task-level hint overrides
        heavy_keywords = {
            "plan",
            "synthesize",
            "insight",
            "analyze",
            "summarize",
            "develop",
            "assess",
            "recommendation",
            "document",
            "report",
        }
        light_keywords = {
            "search",
            "find",
            "gather",
            "retrieve",
            "scrape",
            "navigate",
        }

        hint = task_hint.lower()
        is_heavy = role in high_reasoning_roles or any(
            kw in hint for kw in heavy_keywords
        )
        is_light = role == AgentRole.SEARCH or any(kw in hint for kw in light_keywords)

        if isinstance(backend, OpenAIBackend):
            return (
                cfg.openai_heavy_model
                if is_heavy
                else (cfg.openai_light_model if is_light else cfg.openai_heavy_model)
            )
        elif isinstance(backend, AzureOpenAIBackend):
            return (
                cfg.azure_heavy_model
                if is_heavy
                else (cfg.azure_light_model if is_light else cfg.azure_heavy_model)
            )
        elif isinstance(backend, AWSOpenAIBackend):
            return cfg.aws_heavy_model if is_heavy else cfg.aws_light_model
        elif isinstance(backend, GCPVertexAIBackend):
            return cfg.gcp_heavy_model if is_heavy else cfg.gcp_light_model
        elif isinstance(backend, OllamaBackend):
            return cfg.ollama_heavy_model if is_heavy else cfg.ollama_light_model
        elif isinstance(backend, HuggingFaceBackend):
            return None  # HuggingFace backends use a single configured model
        elif isinstance(backend, AnthropicBackend):
            return cfg.anthropic_heavy_model if is_heavy else cfg.anthropic_light_model
        else:
            return None  # Let the backend use its own default


async def main() -> None:
    """
    Simple usage of the Orchestrator for testing.
    """
    model_backend = create_model_backend(config)
    mcp_registry = await create_mcp_registry(config)
    agent_pool = AgentPool.from_single_backend(model_backend)
    await agent_pool.async_init(mcp_registry)

    orchestrator = Orchestrator(config, mcp_registry, agent_pool)

    query = input("Enter your research query > ")
    async for message in orchestrator.run(query):
        print(f"{message.type.upper()}: {message.message}")
        if message.data:
            print(f"Data: {json.dumps(message.data, indent=2)}")
        if message.error:
            print(f"Error: {message.error}")


if __name__ == "__main__":
    asyncio.run(main())
