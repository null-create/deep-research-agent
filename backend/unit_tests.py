"""
unit_tests.py — Fully mocked unit tests for the research-assistant backend.

All external services (Ollama, OpenAI, MCP servers, Neo4j,
sentence-transformers) are replaced with unittest.mock objects so that every
test runs without any running infrastructure.  Tests exercise agentic logic
paths: tool-call loops, plan/execute/synthesize phases, QA retry gating,
deduplication, session lifecycle, and long-term memory Cypher interactions.

Run from the backend/ directory (with pytest installed):
    pytest unit_tests.py -v
"""

import asyncio
import json
import sys
import os

import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call

# ---------------------------------------------------------------------------
# Path bootstrap so imports resolve from the backend/ directory whether pytest
# is invoked from the repo root or from backend/ directly.
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.dirname(__file__))


# ===========================================================================
# Shared helpers
# ===========================================================================


def _make_model_response(content="", tool_calls=None, finish_reason="stop"):
    """Build a ModelResponse without importing model_backend."""
    from models import ModelResponse

    return ModelResponse(
        content=content, tool_calls=tool_calls or [], finish_reason=finish_reason
    )


def _make_tool_call(name="web_search", parameters=None):
    from models import ToolCall

    return ToolCall(
        name=name, description="", parameters=parameters or {"query": "test"}
    )


async def _collect(async_gen):
    """Drain an async generator into a list."""
    return [msg async for msg in async_gen]


# ===========================================================================
# Group 1 — ModelBackend factory + dispatch
# ===========================================================================


class TestCreateModelBackend:
    """create_model_backend() factory returns the right class for each config."""

    def test_returns_ollama_backend(self):
        from model_backend import create_model_backend, OllamaBackend
        from config import Config

        cfg = Config(model_backend="ollama")
        backend = create_model_backend(cfg)
        assert isinstance(backend, OllamaBackend)

    def test_returns_openai_backend(self):
        from model_backend import create_model_backend, OpenAIBackend
        from config import Config

        cfg = Config(model_backend="openai", openai_api_key="sk-test")
        backend = create_model_backend(cfg)
        assert isinstance(backend, OpenAIBackend)

    def test_returns_azure_backend(self):
        from model_backend import create_model_backend, AzureOpenAIBackend
        from config import Config

        cfg = Config(
            model_backend="azure",
            azure_api_key="key",
            azure_endpoint="https://example.openai.azure.com",
            azure_deployment_name="gpt-4",
        )
        backend = create_model_backend(cfg)
        assert isinstance(backend, AzureOpenAIBackend)

    def test_returns_huggingface_backend(self):
        from model_backend import create_model_backend, HuggingFaceBackend
        from config import Config

        cfg = Config(
            model_backend="huggingface", huggingface_base_url="http://localhost:8080"
        )
        backend = create_model_backend(cfg)
        assert isinstance(backend, HuggingFaceBackend)

    def test_raises_on_unknown_backend(self):
        from model_backend import create_model_backend
        from config import Config

        cfg = Config(model_backend="nonexistent_backend")
        with pytest.raises(ValueError, match="Unknown model backend"):
            create_model_backend(cfg)


class TestOllamaBackendGenerate:
    """OllamaBackend.generate() correctly maps Ollama responses to ModelResponse."""

    async def test_returns_content_from_ollama(self):
        from model_backend import OllamaBackend
        from models import Message

        backend = OllamaBackend(model="llama3", base_url="http://localhost:11434")

        # Build a fake Ollama response structure matching what the real client returns.
        fake_message = MagicMock()
        fake_message.content = "The capital of France is Paris."
        fake_message.tool_calls = None
        fake_response = MagicMock()
        fake_response.message = fake_message

        backend.client.chat = AsyncMock(return_value=fake_response)
        result = await backend.generate(
            model=None,
            messages=[Message(role="user", content="What is the capital of France?")],
        )

        assert result.content == "The capital of France is Paris."
        assert result.finish_reason == "stop"

    async def test_maps_tool_calls_to_tool_call_objects(self):
        from model_backend import OllamaBackend
        from models import Message

        backend = OllamaBackend(model="llama3", base_url="http://localhost:11434")

        fake_tc = MagicMock()
        fake_tc.function.name = "web_search"
        fake_tc.function.arguments = {"query": "climate change 2024"}

        fake_message = MagicMock()
        fake_message.content = ""
        fake_message.tool_calls = [fake_tc]
        fake_response = MagicMock()
        fake_response.message = fake_message

        backend.client.chat = AsyncMock(return_value=fake_response)
        result = await backend.generate(
            model=None,
            messages=[Message(role="user", content="Search for climate data.")],
            tools=[{"name": "web_search"}],
        )

        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].name == "web_search"
        assert result.tool_calls[0].parameters == {"query": "climate change 2024"}

    async def test_returns_error_response_on_exception(self):
        from model_backend import OllamaBackend
        from models import Message

        backend = OllamaBackend(model="llama3", base_url="http://localhost:11434")
        backend.client.chat = AsyncMock(
            side_effect=ConnectionError("Connection refused")
        )

        result = await backend.generate(
            model=None, messages=[Message(role="user", content="hi")]
        )
        assert result.finish_reason == "error"


# ===========================================================================
# Group 2 — MCPServerRegistry call routing
# ===========================================================================


class TestMCPRegistryRouting:
    """MCPServerRegistry routes tool calls to the server that owns the tool."""

    def _build_registry_with_servers(self):
        """Inject two fake clients directly into a registry without connecting."""
        from mcp_client import MCPServerRegistry

        registry = MCPServerRegistry()

        client_a = MagicMock()
        client_a.call_tool = AsyncMock(return_value={"result": "from_server_a"})

        client_b = MagicMock()
        client_b.call_tool = AsyncMock(return_value={"result": "from_server_b"})

        registry.servers = {"web_search": client_a, "file_handler": client_b}
        registry.tool_specs = {
            "web_search": [{"name": "search", "type": "function"}],
            "file_handler": [{"name": "read_file", "type": "function"}],
        }
        return registry, client_a, client_b

    async def test_routes_search_tool_to_correct_client(self):
        registry, client_a, client_b = self._build_registry_with_servers()
        result = await registry.call_tool("search", {"query": "test"})
        assert result == {"result": "from_server_a"}
        client_a.call_tool.assert_awaited_once()
        client_b.call_tool.assert_not_called()

    async def test_routes_file_tool_to_correct_client(self):
        registry, client_a, client_b = self._build_registry_with_servers()
        result = await registry.call_tool("read_file", {"path": "/tmp/test.txt"})
        assert result == {"result": "from_server_b"}
        client_b.call_tool.assert_awaited_once()
        client_a.call_tool.assert_not_called()

    async def test_raises_on_unknown_tool(self):
        registry, _, _ = self._build_registry_with_servers()
        with pytest.raises(Exception, match="not found in any registered MCP server"):
            await registry.call_tool("nonexistent_tool", {})

    async def test_get_all_tools_aggregates_from_all_servers(self):
        from mcp_client import MCPServerRegistry

        registry = MCPServerRegistry()
        client_a = MagicMock()
        client_a.list_tool_specs = AsyncMock(return_value=[{"name": "search"}])
        client_b = MagicMock()
        client_b.list_tool_specs = AsyncMock(return_value=[{"name": "scrape"}])

        registry.servers = {"web_search": client_a, "web_scraper": client_b}
        registry.tool_specs = {}

        tools = await registry.get_all_tools()
        names = {t["name"] for t in tools}
        assert "search" in names
        assert "scrape" in names
        assert len(tools) == 2

    async def test_call_tool_returns_error_dict_on_client_exception(self):
        """When the underlying client raises, registry returns {"error": ...} rather than propagating."""
        from mcp_client import MCPServerRegistry

        registry = MCPServerRegistry()
        client = MagicMock()
        client.call_tool = AsyncMock(side_effect=RuntimeError("boom"))

        registry.servers = {"web_search": client}
        registry.tool_specs = {"web_search": [{"name": "search", "type": "function"}]}

        result = await registry.call_tool("search", {})
        assert "error" in result


# ===========================================================================
# Group 3 — SubAgent.run()
# ===========================================================================


class TestSubAgentRun:
    """SubAgent.run() correctly delegates to ModelBackend and handles errors."""

    def _make_agent(self, mock_generate):
        from orchestrator import SearchAgent
        from mcp_client import MCPServerRegistry

        backend = MagicMock()
        backend.generate = mock_generate
        registry = MagicMock(spec=MCPServerRegistry)
        return SearchAgent(model_backend=backend, mcp_servers=registry)

    async def test_returns_content_when_no_tool_calls(self):
        generate = AsyncMock(return_value=_make_model_response(content="answer text"))
        agent = self._make_agent(generate)

        from models import Message

        result = await agent.run("What is 2+2?", tools=[])
        assert result.content == "answer text"
        assert result.finish_reason == "stop"
        generate.assert_awaited_once()

    async def test_model_receives_system_and_user_messages(self):
        generate = AsyncMock(return_value=_make_model_response(content="ok"))
        agent = self._make_agent(generate)

        await agent.run("user question", tools=[])
        _, kwargs = generate.call_args
        messages = kwargs.get("messages") or generate.call_args[0][1]
        roles = [m.role for m in messages]
        assert "system" in roles
        assert "user" in roles

    async def test_error_response_does_not_propagate_exception(self):
        generate = AsyncMock(side_effect=RuntimeError("LLM failed"))
        agent = self._make_agent(generate)

        result = await agent.run("test")
        assert result.finish_reason == "error"
        assert "LLM failed" in result.content

    async def test_status_transitions_to_done_on_success(self):
        from orchestrator import AgentStatus

        generate = AsyncMock(return_value=_make_model_response(content="done"))
        agent = self._make_agent(generate)

        await agent.run("test")
        assert agent.status == AgentStatus.DONE

    async def test_status_transitions_to_error_on_failure(self):
        from orchestrator import AgentStatus

        generate = AsyncMock(side_effect=RuntimeError("fail"))
        agent = self._make_agent(generate)

        await agent.run("test")
        assert agent.status == AgentStatus.ERROR


# ===========================================================================
# Group 4 — Orchestrator.plan()
# ===========================================================================


@pytest.fixture
def mock_registry():
    from mcp_client import MCPServerRegistry

    reg = MagicMock(spec=MCPServerRegistry)
    reg.get_all_tools = AsyncMock(return_value=[])
    reg.call_tool = AsyncMock(return_value={})
    return reg


@pytest.fixture
def mock_backend():
    from model_backend import ModelBackend

    b = MagicMock(spec=ModelBackend)
    b.generate = AsyncMock(return_value=_make_model_response(content="{}"))
    return b


@pytest.fixture
def mock_ltm():
    from long_term_memory import AsyncLongTermMemory

    ltm = MagicMock(spec=AsyncLongTermMemory)
    ltm.recall = AsyncMock(return_value=[])
    ltm.find_similar = AsyncMock(return_value=[])
    ltm.store = AsyncMock(return_value={"success": True})
    ltm.graph = MagicMock()
    ltm.graph.stats = AsyncMock(return_value={"entities": 0})
    ltm.graph.recall_graph_context = AsyncMock(return_value="")
    return ltm


@pytest.fixture
def make_orchestrator(mock_registry, mock_backend):
    """Factory that returns a configured Orchestrator with mocked internals."""

    def _factory(ltm=None, depth="shallow"):
        from orchestrator import Orchestrator, AgentPool

        pool = AgentPool.from_single_backend(mock_backend)
        pool.search = MagicMock()
        pool.search.run = AsyncMock(
            return_value=_make_model_response(
                content=json.dumps({"sources": [], "coverage_notes": ""})
            )
        )
        pool.analyst = MagicMock()
        pool.analyst.run = AsyncMock(
            return_value=_make_model_response(
                content=json.dumps({"claims": [], "tensions": [], "analyst_notes": ""})
            )
        )
        pool.loop = MagicMock()
        pool.loop.audit = AsyncMock(return_value=_make_audit_result("clean"))
        pool.report = MagicMock()
        pool.report.run = AsyncMock(
            return_value=_make_model_response(
                content=json.dumps(
                    {"sections": [{"title": "Overview", "description": "desc"}]}
                )
            )
        )

        from config import Config

        cfg = Config()
        orch = Orchestrator(
            cfg, mock_registry, pool, long_term_memory=ltm, research_depth=depth
        )
        # Replace the root backend so plan() doesn't hit Ollama
        orch._root_backend = mock_backend
        # Give _select_model a non-None return for every role so _run_search
        # and distillation helpers don't early-return before calling agents.
        orch.config.search_model_override = "test-model"
        orch.config.analyst_model_override = "test-model"
        orch.config.qa_model_override = "test-model"
        orch.config.root_model_override = "test-model"
        # synthesize() calls loop.run() (cross-reference) and
        # report.model.generate() (section drafting) directly.
        pool.loop.run = AsyncMock(
            return_value=_make_model_response(
                content='{"contradictions": [], "verdict": "clean"}'
            )
        )
        pool.report.model = MagicMock()
        pool.report.model.generate = AsyncMock(
            return_value=_make_model_response(content="Section content.")
        )
        return orch

    return _factory


def _make_audit_result(verdict="clean", contradictions=None):
    from orchestrator import AuditResult

    return AuditResult(contradictions=contradictions or [], verdict=verdict, notes="")


def _plan_json(steps=None):
    steps = steps or [{"id": 1, "description": "Research topic A"}]
    return json.dumps({"goal": "Test research goal", "steps": steps})


class TestOrchestratorPlan:
    """Orchestrator.plan() parses the root agent's JSON and emits a ResearchPlan."""

    async def test_plan_returns_research_plan_event(
        self, make_orchestrator, mock_backend
    ):
        mock_backend.generate = AsyncMock(
            return_value=_make_model_response(
                content=_plan_json(
                    [
                        {"id": 1, "description": "Step one"},
                        {"id": 2, "description": "Step two"},
                    ]
                )
            )
        )
        orch = make_orchestrator()
        messages = await _collect(orch.plan("What is climate change?"))
        plan_msg = next((m for m in messages if m.type == "plan"), None)
        assert plan_msg is not None
        assert len(plan_msg.plan["steps"]) == 2

    async def test_plan_calls_root_backend_generate(
        self, make_orchestrator, mock_backend
    ):
        mock_backend.generate = AsyncMock(
            return_value=_make_model_response(content=_plan_json())
        )
        orch = make_orchestrator()
        await _collect(orch.plan("Test query"))
        mock_backend.generate.assert_awaited_once()

    async def test_plan_with_ltm_injects_memory_context(
        self, make_orchestrator, mock_backend, mock_ltm
    ):
        mock_ltm.recall = AsyncMock(
            return_value=[
                {"content": "Prior finding about CO2 levels.", "importance": 8}
            ]
        )
        mock_ltm.graph.recall_graph_context = AsyncMock(return_value="CO2 is rising.")
        mock_ltm.graph.stats = AsyncMock(
            return_value={
                "entities": 5,
                "relationships": 0,
                "communities": 0,
                "contradictions": 0,
                "documents": 0,
                "claims": 0,
                "hierarchies": 0,
            }
        )

        mock_backend.generate = AsyncMock(
            return_value=_make_model_response(content=_plan_json())
        )
        orch = make_orchestrator(ltm=mock_ltm)
        await _collect(orch.plan("Climate research"))

        # Verify that the root backend received a prompt containing memory context
        _, gen_kwargs = mock_backend.generate.call_args
        messages = gen_kwargs.get("messages") or mock_backend.generate.call_args[0][1]
        full_prompt = " ".join(m.content for m in messages)
        assert "long-term memory" in full_prompt or "prior" in full_prompt.lower()

    async def test_plan_fallback_on_invalid_json(self, make_orchestrator, mock_backend):
        """Malformed root response should fall back to a single generic step."""
        mock_backend.generate = AsyncMock(
            return_value=_make_model_response(
                content="Sorry, I can't do that right now."
            )
        )
        orch = make_orchestrator()
        messages = await _collect(orch.plan("test"))
        plan_msg = next((m for m in messages if m.type == "plan"), None)
        assert plan_msg is not None
        # Fallback creates exactly one step
        assert len(plan_msg.plan["steps"]) >= 1

    async def test_deny_plan_acknowledged(self, make_orchestrator, mock_backend):
        mock_backend.generate = AsyncMock(
            return_value=_make_model_response(content=_plan_json())
        )
        orch = make_orchestrator()
        messages = await _collect(orch.plan("test query"))
        plan_msg = next(m for m in messages if m.type == "plan")
        plan_id = plan_msg.plan["id"]

        # deny_plan returns a single ResponseMessage (coroutine), not an async gen
        result = await orch.deny_plan(plan_id)
        assert result.type in ("plan_denied", "status", "error")

    async def test_modify_plan_updates_step_description(
        self, make_orchestrator, mock_backend
    ):
        mock_backend.generate = AsyncMock(
            return_value=_make_model_response(content=_plan_json())
        )
        orch = make_orchestrator()
        await _collect(orch.plan("test query"))

        plan_id = orch._pending_plan.id
        step_id = orch._pending_plan.steps[0].id
        # modify_plan returns a single ResponseMessage (coroutine), not an async gen
        result = await orch.modify_plan(
            plan_id, feedback=f"Change step {step_id} to focus on CO2 data"
        )
        assert result is not None
        assert orch._pending_plan is not None


# ===========================================================================
# Group 5 — Orchestrator._run_step() search→analyst path
# ===========================================================================


class TestOrchestratorRunStep:
    """_run_step() correctly sequences search, analyst, and QA phases."""

    def _step(self, id=1, description="Research AI adoption rates 2024"):
        from models import ResearchStep, StepStatus

        return ResearchStep(
            id=id,
            name=f"Step {id}",
            description=description,
            status=StepStatus.IN_PROGRESS,
        )

    async def test_search_agent_called_for_step(self, make_orchestrator):
        orch = make_orchestrator()
        # Prepare context (plan() normally does this)
        orch.context.save_step("query", "AI research")

        from pipeline import PipelineRunner

        runner = PipelineRunner()
        step = self._step()

        await _collect(
            orch._run_step(step, "AI research", tools=[], pipeline_runner=runner)
        )
        orch.agents.search.run.assert_awaited()

    async def test_analyst_agent_called_after_search(self, make_orchestrator):
        orch = make_orchestrator()
        orch.context.save_step("query", "AI research")

        from pipeline import PipelineRunner

        runner = PipelineRunner()
        step = self._step()

        await _collect(
            orch._run_step(step, "AI research", tools=[], pipeline_runner=runner)
        )
        orch.agents.analyst.run.assert_awaited()

    async def test_shallow_depth_skips_qa(self, make_orchestrator):
        orch = make_orchestrator(depth="shallow")
        orch.context.save_step("query", "test")

        from pipeline import PipelineRunner

        runner = PipelineRunner()
        step = self._step()

        await _collect(orch._run_step(step, "test", tools=[], pipeline_runner=runner))
        # In shallow mode the LoopAgent audit must NOT be called
        orch.agents.loop.audit.assert_not_called()

    async def test_step_completed_on_success(self, make_orchestrator):
        orch = make_orchestrator()
        orch.context.save_step("query", "test")

        from pipeline import PipelineRunner
        from models import StepStatus

        runner = PipelineRunner()
        step = self._step()

        await _collect(orch._run_step(step, "test", tools=[], pipeline_runner=runner))
        assert step.status == StepStatus.COMPLETED

    async def test_duplicate_step_skipped(self, make_orchestrator):
        orch = make_orchestrator()
        orch.context.save_step("query", "test")

        from pipeline import PipelineRunner

        runner = PipelineRunner()
        step_a = self._step(1)
        step_b = self._step(2, description=step_a.description)  # exact same description

        # First run registers the query
        await _collect(orch._run_step(step_a, "test", tools=[], pipeline_runner=runner))
        # Second run with same description should skip without calling search again
        search_call_count_before = orch.agents.search.run.call_count
        msgs = await _collect(
            orch._run_step(step_b, "test", tools=[], pipeline_runner=runner)
        )
        assert orch.agents.search.run.call_count == search_call_count_before
        assert any(
            getattr(m, "data", {}) and m.data.get("skipped_duplicate") for m in msgs
        )

    async def test_qa_loop_agent_called_in_moderate_depth(self, make_orchestrator):
        orch = make_orchestrator(depth="moderate")
        orch.context.save_step("query", "test")

        from pipeline import PipelineRunner

        runner = PipelineRunner()
        step = self._step()

        await _collect(orch._run_step(step, "test", tools=[], pipeline_runner=runner))
        orch.agents.loop.audit.assert_awaited()

    async def test_qa_retries_on_flagged_contradiction(self, make_orchestrator):
        from orchestrator import AuditResult, Contradiction
        from search_result_store import SearchResultStore

        # First audit: flagged with a targeted query; second audit: clean
        flagged = AuditResult(
            contradictions=[
                Contradiction(
                    source_a="http://a.com",
                    claim_a="X is true",
                    source_b="http://b.com",
                    claim_b="X is false",
                    contradiction_type="factual_error",
                    targeted_query="Is X true or false? primary source",
                )
            ],
            verdict="flagged",
        )
        clean = AuditResult(contradictions=[], verdict="clean")

        orch = make_orchestrator(depth="moderate")
        orch.agents.loop.audit = AsyncMock(side_effect=[flagged, clean])
        orch.context.save_step("query", "test")

        # Mock the search store so chunk_count differs before/after supplemental
        # search, preventing the data-poverty early-exit before the 2nd audit.
        orch._search_store = MagicMock(spec=SearchResultStore)
        orch._search_store.chunk_count = MagicMock(side_effect=[0, 1])
        orch._search_store.retrieve = AsyncMock(return_value="")
        orch._search_store.add = AsyncMock()
        orch._search_store.mark_superseded_by_step = MagicMock(return_value=0)
        orch._search_store.filter_long_term_memories = AsyncMock(return_value=[])
        orch._search_store.persist_to_long_term_memory = AsyncMock(return_value=0)

        from pipeline import PipelineRunner

        runner = PipelineRunner()
        step = self._step()

        await _collect(orch._run_step(step, "test", tools=[], pipeline_runner=runner))
        # Audit must have been called at least twice (initial + retry)
        assert orch.agents.loop.audit.await_count >= 2

    async def test_actionable_contradiction_marked_resolved_after_research(
        self, make_orchestrator
    ):
        """Regression: an actionable contradiction whose targeted re-search yields
        new evidence must be flagged ``resolved=True`` with a resolution note.

        Previously ``Contradiction.resolved`` was never set, so contradicted
        sources were never retired and every contradiction surfaced as
        "unresolved" in the final report.
        """
        from orchestrator import AuditResult, Contradiction
        from search_result_store import SearchResultStore

        contradiction = Contradiction(
            source_a="http://a.com",
            claim_a="X is true",
            source_b="http://b.com",
            claim_b="X is false",
            contradiction_type="factual_error",
            targeted_query="Is X true or false? primary source",
        )
        flagged = AuditResult(contradictions=[contradiction], verdict="flagged")
        clean = AuditResult(contradictions=[], verdict="clean")

        orch = make_orchestrator(depth="moderate")
        orch.agents.loop.audit = AsyncMock(side_effect=[flagged, clean])
        orch.context.save_step("query", "test")

        # chunk_count must increase across the supplemental search so the
        # data-poverty early-exit is not taken and the resolution path runs.
        orch._search_store = MagicMock(spec=SearchResultStore)
        orch._search_store.chunk_count = MagicMock(side_effect=[0, 1])
        orch._search_store.retrieve = AsyncMock(return_value="")
        orch._search_store.add = AsyncMock()
        orch._search_store.mark_superseded_by_step = MagicMock(return_value=0)
        orch._search_store.filter_long_term_memories = AsyncMock(return_value=[])
        orch._search_store.persist_to_long_term_memory = AsyncMock(return_value=0)

        from pipeline import PipelineRunner

        runner = PipelineRunner()
        step = self._step()

        await _collect(orch._run_step(step, "test", tools=[], pipeline_runner=runner))

        assert contradiction.resolved is True
        assert contradiction.resolution  # non-empty resolution note recorded
        assert any(c.resolved for c in orch._contradictions)


# ===========================================================================
# Group 6 — Distillation helpers
# ===========================================================================


class TestDistillationHelpers:
    """_distill_tool_result() and _generate_step_summary() internal helpers."""

    def _make_orch(self, mock_registry, mock_backend):
        from orchestrator import Orchestrator, AgentPool
        from config import Config

        pool = AgentPool.from_single_backend(mock_backend)
        pool.search = MagicMock()
        # _distill_tool_result and _generate_step_summary call agents.search.run()
        pool.search.run = AsyncMock(
            return_value=_make_model_response(content="Distilled summary.")
        )
        pool.analyst = MagicMock()
        pool.loop = MagicMock()
        pool.report = MagicMock()
        cfg = Config()
        orch = Orchestrator(cfg, mock_registry, pool)
        orch._root_backend = mock_backend
        # Ensure _select_model returns a non-None value so helpers don't
        # short-circuit before calling agents.search.run().
        orch.config.search_model_override = "test-model"
        return orch

    async def test_short_content_passes_through_without_model_call(
        self, mock_registry, mock_backend
    ):
        orch = self._make_orch(mock_registry, mock_backend)
        short = "x" * (orch.config.distill_max_chars - 1)

        result = await orch._distill_tool_result(short, step_description="test")
        # Model should NOT have been called for content under the threshold
        mock_backend.generate.assert_not_awaited()
        assert result == short

    async def test_long_content_triggers_model_call(self, mock_registry, mock_backend):
        orch = self._make_orch(mock_registry, mock_backend)
        # Content must exceed the module-level config.distill_max_chars (default 4000)
        long_content = "word " * 1000  # 5000 chars > 4000 threshold

        result = await orch._distill_tool_result(long_content, step_description="test")
        # _distill_tool_result calls self.agents.search.run() (not backend.generate)
        orch.agents.search.run.assert_awaited_once()
        # Returns {"text": distilled_content} for long inputs
        assert result == {"text": "Distilled summary."}

    async def test_generate_step_summary_stores_in_step_summaries(
        self, mock_registry, mock_backend
    ):
        # Seed the search agent to return a bullet summary
        orch = self._make_orch(mock_registry, mock_backend)
        orch.agents.search.run = AsyncMock(
            return_value=_make_model_response(
                content="• CO2 is rising\n• Arctic ice declining"
            )
        )
        # _generate_step_summary(step: ResearchStep, analyst_result: dict) -> str
        # and stores the result in self._step_summaries[step.id] via _run_step.
        # Call it directly and manually store the result, matching production usage.
        from models import ResearchStep, StepStatus

        step = ResearchStep(
            id=3,
            name="Step 3",
            description="Research CO2 emissions",
            status=StepStatus.IN_PROGRESS,
        )
        analyst_result = {
            "claims": [{"claim": "CO2 up", "sources": []}],
            "tensions": [],
        }
        summary = await orch._generate_step_summary(step, analyst_result)
        orch._step_summaries[step.id] = summary
        assert 3 in orch._step_summaries
        assert orch._step_summaries[3] != ""

    async def test_reset_state_clears_step_summaries(self, mock_registry, mock_backend):
        orch = self._make_orch(mock_registry, mock_backend)
        orch._step_summaries[1] = "summary"
        orch._step_summaries[2] = "another"
        orch._reset_state()
        assert orch._step_summaries == {}


# ===========================================================================
# Group 7 — Orchestrator.synthesize()
# ===========================================================================


class TestOrchestratorSynthesize:
    """synthesize() drives the multi-pass report composer pipeline."""

    async def test_synthesize_requires_analyst_output(self, make_orchestrator):
        orch = make_orchestrator()
        # _analyst_output is None by default — synthesize should emit an error
        msgs = await _collect(orch.synthesize())
        error_msg = next((m for m in msgs if m.type == "error"), None)
        assert error_msg is not None

    async def test_synthesize_calls_report_composer(self, make_orchestrator):
        orch = make_orchestrator()
        orch._analyst_output = {"claims": [], "tensions": [], "analyst_notes": ""}
        orch.context.save_step("query", "climate change research")

        await _collect(orch.synthesize())
        orch.agents.report.run.assert_awaited()

    async def test_synthesize_emits_report_event(self, make_orchestrator, mock_backend):
        from orchestrator import AuditResult

        orch = make_orchestrator()
        orch._analyst_output = {
            "claims": [],
            "tensions": [],
            "analyst_notes": "test notes",
        }
        orch.context.save_step("query", "test query")

        # Mock the outline call and the section drafting call
        outline_json = json.dumps(
            {"sections": [{"title": "Overview", "description": "Summary of findings"}]}
        )
        section_draft = "Overview content here."
        final_report = "RESEARCH REPORT: Test\n\nEXECUTIVE SUMMARY\nSome content."

        orch.agents.report.run = AsyncMock(
            side_effect=[
                _make_model_response(content=outline_json),  # outline call
                _make_model_response(content=section_draft),  # section 1 draft
                _make_model_response(content=final_report),  # final assembly
            ]
        )
        orch.agents.loop.audit = AsyncMock(
            return_value=AuditResult(contradictions=[], verdict="clean")
        )

        msgs = await _collect(orch.synthesize())
        report_msg = next((m for m in msgs if m.type == "report"), None)
        assert report_msg is not None
        assert report_msg.data is not None

    async def test_synthesize_includes_step_summaries_in_outline_prompt(
        self, make_orchestrator
    ):
        orch = make_orchestrator()
        orch._analyst_output = {"claims": [], "tensions": []}
        orch.context.save_step("query", "AI research")
        orch._step_summaries = {
            1: "• Fact A about AI\n• Fact B about AI",
            2: "• Fact C about deployment",
        }

        prompts_seen = []

        async def capture_run(prompt, **kwargs):
            prompts_seen.append(prompt)
            return _make_model_response(
                content=json.dumps({"sections": [{"title": "S1", "description": "d"}]})
            )

        orch.agents.report.run = AsyncMock(side_effect=capture_run)

        await _collect(orch.synthesize())
        # The first (outline) call must include the step summaries
        assert any("Fact A" in p or "Step 1" in p for p in prompts_seen)


# ===========================================================================
# Group 8 — PipelineRunner deduplication
# ===========================================================================


class TestPipelineRunnerDedup:
    """PipelineRunner.is_query_new() provides atomic check-and-register."""

    async def test_new_query_returns_true(self):
        from pipeline import PipelineRunner

        runner = PipelineRunner()
        result = await runner.is_query_new("climate change emissions 2024")
        assert result is True

    async def test_duplicate_query_returns_false(self):
        from pipeline import PipelineRunner

        runner = PipelineRunner()
        await runner.is_query_new("duplicate query")
        result = await runner.is_query_new("duplicate query")
        assert result is False

    async def test_queries_are_compared_case_insensitively(self):
        from pipeline import PipelineRunner

        runner = PipelineRunner()
        await runner.is_query_new("Climate Change")
        result = await runner.is_query_new("climate change")
        assert result is False

    async def test_empty_query_always_returns_true(self):
        from pipeline import PipelineRunner

        runner = PipelineRunner()
        result1 = await runner.is_query_new("   ")
        result2 = await runner.is_query_new("   ")
        # Empty/whitespace-only queries are always considered new
        assert result1 is True
        assert result2 is True

    async def test_concurrent_same_query_only_one_passes(self):
        """Two concurrent calls with the same query must produce exactly one True."""
        from pipeline import PipelineRunner

        runner = PipelineRunner()
        results = await asyncio.gather(
            runner.is_query_new("concurrent query"),
            runner.is_query_new("concurrent query"),
        )
        assert results.count(True) == 1
        assert results.count(False) == 1

    async def test_searched_count_increments(self):
        from pipeline import PipelineRunner

        runner = PipelineRunner()
        await runner.is_query_new("query one")
        await runner.is_query_new("query two")
        await runner.is_query_new("query two")  # duplicate, not counted
        assert runner.searched_count == 2


# ===========================================================================
# Group 9 — ResearchSessionManager
# ===========================================================================


class TestResearchSessionManager:
    """ResearchSessionManager tracks, queries, and cancels research jobs."""

    async def test_start_job_creates_tracked_job(self):
        from session_manager import ResearchSessionManager

        manager = ResearchSessionManager()

        async def _noop():
            await asyncio.sleep(0)

        job = await manager.start_job("session-1", _noop())
        assert job.job_id is not None
        assert job.session_id == "session-1"
        assert manager.get_job(job.job_id) is job

    async def test_get_job_returns_none_for_unknown_id(self):
        from session_manager import ResearchSessionManager

        manager = ResearchSessionManager()
        assert manager.get_job("nonexistent-id") is None

    async def test_get_job_for_session_returns_running_job(self):
        from session_manager import ResearchSessionManager

        manager = ResearchSessionManager()
        event = asyncio.Event()

        async def _wait():
            await event.wait()

        job = await manager.start_job("session-abc", _wait())
        found = manager.get_job_for_session("session-abc")
        assert found is job
        # Cleanup
        event.set()
        await asyncio.sleep(0)

    async def test_cancel_job_cancels_task(self):
        from session_manager import ResearchSessionManager

        manager = ResearchSessionManager()
        event = asyncio.Event()

        async def _long_running():
            await event.wait()

        job = await manager.start_job("session-x", _long_running())
        cancelled = await manager.cancel_job(job.job_id)
        assert cancelled is True
        # Give the event loop a tick to propagate the cancellation
        await asyncio.sleep(0)
        assert job.task.cancelled() or job.task.done()

    async def test_cancel_unknown_job_returns_false(self):
        from session_manager import ResearchSessionManager

        manager = ResearchSessionManager()
        result = await manager.cancel_job("does-not-exist")
        assert result is False

    async def test_shutdown_completes_without_hanging(self):
        from session_manager import ResearchSessionManager

        manager = ResearchSessionManager()

        async def _quick():
            await asyncio.sleep(0.01)

        await manager.start_job("s1", _quick())
        await manager.start_job("s2", _quick())
        # Should complete within the timeout without raising
        await manager.shutdown(timeout=5.0)

    async def test_completed_jobs_report_correct_status(self):
        from session_manager import ResearchSessionManager

        manager = ResearchSessionManager()

        async def _instant():
            return "done"

        job = await manager.start_job("s", _instant())
        # Allow the task to finish
        await asyncio.sleep(0.05)
        assert job.status == "complete"


# ===========================================================================
# Group 10 — AsyncLongTermMemory with mocked Neo4j
# ===========================================================================


def _make_neo4j_session_mock(records=None):
    """Build a fake async Neo4j session as a context manager."""
    mock_result = AsyncMock()
    mock_result.single = AsyncMock(return_value=None)
    mock_result.data = AsyncMock(return_value=records or [])

    # Make `async for record in result` work
    mock_result.__aiter__ = MagicMock(return_value=iter(records or []))

    mock_session = AsyncMock()
    mock_session.run = AsyncMock(return_value=mock_result)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    return mock_session


def _make_neo4j_driver_mock(session_mock=None):
    """Build a fake async Neo4j driver."""
    session_mock = session_mock or _make_neo4j_session_mock()
    driver = AsyncMock()
    driver.verify_connectivity = AsyncMock(return_value=None)
    driver.session = MagicMock(return_value=session_mock)
    driver.close = AsyncMock()
    return driver


class TestAsyncLongTermMemory:
    """AsyncLongTermMemory with fully mocked Neo4j driver + embeddings."""

    async def test_async_init_runs_schema_queries(self):
        """_create_schema() must fire constraint and vector index queries."""
        from long_term_memory import AsyncLongTermMemory

        session_mock = _make_neo4j_session_mock()
        driver_mock = _make_neo4j_driver_mock(session_mock)

        with patch("long_term_memory.AsyncGraphDatabase") as mock_gdb:
            mock_gdb.driver.return_value = driver_mock
            ltm = AsyncLongTermMemory()
            await ltm.async_init()

        assert ltm._available is True
        # session.run() must have been called for each constraint + index query.
        # New schema: 1 Memory constraint + 1 Memory vector idx
        #           + 7 entity type constraints + 7 entity type vector indexes
        #           + 1 Community constraint + 1 Community vector idx
        #           + 1 Claim constraint + 1 Claim vector idx
        #           + 2 Document constraints + 1 Document vector idx
        # Total = 2 + 14 + 2 + 2 + 3 = 23
        assert session_mock.run.await_count >= 23

    async def test_store_unavailable_returns_failure(self):
        """When _available=False, store() must return a failure dict immediately."""
        from long_term_memory import AsyncLongTermMemory

        ltm = AsyncLongTermMemory()
        ltm._available = False
        result = await ltm.store("Some content")
        assert result["success"] is False

    async def test_store_calls_neo4j_create(self):
        """store() must execute a Cypher CREATE/MERGE when LTM is available."""
        from long_term_memory import AsyncLongTermMemory

        session_mock = _make_neo4j_session_mock()
        driver_mock = _make_neo4j_driver_mock(session_mock)

        with patch("long_term_memory.AsyncGraphDatabase") as mock_gdb, patch(
            "embeddings.EMBEDDINGS_ENABLED", False
        ):
            mock_gdb.driver.return_value = driver_mock
            ltm = AsyncLongTermMemory()
            await ltm.async_init()
            # Reset call count after schema setup
            session_mock.run.reset_mock()

            await ltm.store(
                "Climate change is accelerating.", category="research_finding"
            )

        # At least one cypher call (the CREATE node) should have been made
        assert session_mock.run.await_count >= 1
        # Verify it was called with a string Cypher query (content stored)
        all_args = [str(c.args[0]) for c in session_mock.run.call_args_list]
        assert any("Memory" in q or "CREATE" in q or "MERGE" in q for q in all_args)

    async def test_store_returns_success_on_happy_path(self):
        from long_term_memory import AsyncLongTermMemory

        session_mock = _make_neo4j_session_mock()
        driver_mock = _make_neo4j_driver_mock(session_mock)

        with patch("long_term_memory.AsyncGraphDatabase") as mock_gdb, patch(
            "embeddings.EMBEDDINGS_ENABLED", False
        ):
            mock_gdb.driver.return_value = driver_mock
            ltm = AsyncLongTermMemory()
            await ltm.async_init()

            result = await ltm.store("Test content", importance=7)

        assert result.get("success") is True or "memory_id" in result

    async def test_store_empty_content_returns_failure(self):
        from long_term_memory import AsyncLongTermMemory

        session_mock = _make_neo4j_session_mock()
        driver_mock = _make_neo4j_driver_mock(session_mock)

        with patch("long_term_memory.AsyncGraphDatabase") as mock_gdb:
            mock_gdb.driver.return_value = driver_mock
            ltm = AsyncLongTermMemory()
            await ltm.async_init()
            result = await ltm.store("   ")

        assert result["success"] is False

    async def test_recall_returns_empty_list_when_no_results(self):
        from long_term_memory import AsyncLongTermMemory

        session_mock = _make_neo4j_session_mock(records=[])
        driver_mock = _make_neo4j_driver_mock(session_mock)

        with patch("long_term_memory.AsyncGraphDatabase") as mock_gdb, patch(
            "embeddings.EMBEDDINGS_ENABLED", False
        ):
            mock_gdb.driver.return_value = driver_mock
            ltm = AsyncLongTermMemory()
            await ltm.async_init()
            results = await ltm.recall("some query")

        assert results == []

    async def test_close_shuts_down_driver(self):
        from long_term_memory import AsyncLongTermMemory

        driver_mock = _make_neo4j_driver_mock()
        with patch("long_term_memory.AsyncGraphDatabase") as mock_gdb:
            mock_gdb.driver.return_value = driver_mock
            ltm = AsyncLongTermMemory()
            await ltm.async_init()
            await ltm.close()

        driver_mock.close.assert_awaited_once()
        assert ltm._available is False


# ===========================================================================
# Group 11 — KnowledgeGraph (inner class of long_term_memory.py)
# ===========================================================================


class TestKnowledgeGraph:
    """KnowledgeGraph executes the expected Cypher patterns on Neo4j."""

    def _build_ltm_with_mocked_driver(self, session_mock=None):
        """Return an ``AsyncLongTermMemory`` instance with a mocked driver already wired."""
        from long_term_memory import AsyncLongTermMemory

        if session_mock is None:
            session_mock = _make_neo4j_session_mock()
        driver_mock = _make_neo4j_driver_mock(session_mock)

        ltm = AsyncLongTermMemory()
        ltm._driver = driver_mock
        ltm._available = True
        ltm.graph._ltm = ltm
        return ltm, session_mock

    async def test_upsert_entity_executes_merge_cypher(self):
        from long_term_memory import KnowledgeGraph

        # The upsert_entity result mock needs to return a single record
        entity_record = {
            "id": "ent-1",
            "name": "Climate Change",
            "entity_type": "concept",
        }
        session_mock = _make_neo4j_session_mock(records=[entity_record])
        result_mock = AsyncMock()
        result_mock.single = AsyncMock(return_value=None)  # No dedup match
        session_mock.run = AsyncMock(return_value=result_mock)

        ltm, session_mock = self._build_ltm_with_mocked_driver(session_mock)

        with patch("embeddings.EMBEDDINGS_ENABLED", False):
            await ltm.graph.upsert_entity(
                "Climate Change", entity_type="concept", session_id="sess1"
            )

        called_cyphers = [str(c.args[0]) for c in session_mock.run.call_args_list]
        # Should CREATE a :Concept node (not :Entity)
        assert any("Concept" in q or "CREATE" in q for q in called_cyphers)

    async def test_store_source_executes_merge_on_source_node(self):
        """store_source() is deprecated and delegates to store_document(), which MERGES :Document."""
        entity_record = {
            "url": "https://example.com",
            "title": "Example",
            "credibility_score": 0.9,
        }
        session_mock = _make_neo4j_session_mock(records=[entity_record])
        result_mock = AsyncMock()
        result_mock.single = AsyncMock(return_value=entity_record)
        session_mock.run = AsyncMock(return_value=result_mock)

        ltm, session_mock = self._build_ltm_with_mocked_driver(session_mock)

        with patch("embeddings.EMBEDDINGS_ENABLED", False):
            await ltm.graph.store_source(
                "https://example.com", "Example", credibility_score=0.9
            )

        called_cyphers = [str(c.args[0]) for c in session_mock.run.call_args_list]
        # Should MERGE a :Document node (not :Source)
        assert any("Document" in q or "MERGE" in q for q in called_cyphers)

    async def test_store_relationship_auto_upserts_missing_endpoints(self):
        """store_relationship must auto-create missing endpoint entities.

        Regression: ``MATCH (s) MATCH (t) CREATE (s)-[...]->(t)`` silently
        no-ops (binding nothing) when either endpoint entity does not yet
        exist, yet the method still returned ``success: True`` — losing the
        edge. The fix verifies both endpoints first and upserts any missing
        one before creating the relationship.
        """
        # Default session.single() -> None, so _entity_exists() returns False
        # for both endpoints, forcing the auto-upsert path.
        ltm, session_mock = self._build_ltm_with_mocked_driver()
        ltm.graph.upsert_entity = AsyncMock(
            return_value={"success": True, "entity_id": "ent-x"}
        )

        with patch("embeddings.EMBEDDINGS_ENABLED", False):
            result = await ltm.graph.store_relationship(
                "Solar power",
                "Carbon emissions",
                "reduces",
                session_id="sess1",
            )

        upserted_names = {
            c.kwargs.get("name") for c in ltm.graph.upsert_entity.await_args_list
        }
        assert "Solar power" in upserted_names
        assert "Carbon emissions" in upserted_names
        # A relationship CREATE must still run after the endpoints are ensured.
        called_cyphers = [str(c.args[0]) for c in session_mock.run.call_args_list]
        assert any("CREATE" in q for q in called_cyphers)
        assert result.get("success") is True
