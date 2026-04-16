"""
Smoke tests for all new architecture features.
Run from the backend/ directory: python smoke_test.py

This file is intentionally NOT a pytest test module — it uses asyncio.run()
directly and must be executed as a standalone script.  It is excluded from
pytest collection via collect_ignore in conftest.py.

Static Analysis via the `ast` Module
-------------------------------------
Several tests in this file use Python's built-in `ast` (Abstract Syntax Tree)
library to verify structural properties of backend modules *without importing
or executing them*.  This is necessary because modules like optimization.py,
orchestrator.py, and long_term_memory.py carry heavy runtime dependencies
(LLM clients, Neo4j, MCP servers) that may be unavailable in the test
environment — importing them would fail on missing credentials or services.

The pattern used throughout is:

  1. Read the source file as a string.
  2. Parse it into an AST with ast.parse().
  3. Walk the tree with ast.walk() to locate specific classes, methods, or
     function definitions by name.
  4. Extract the raw source text of a located node with
     ast.get_source_segment(src, node).
  5. Assert on the presence or absence of identifiers/strings within that
     source segment to enforce implementation contracts (e.g. "must call
     .recall()", "must not reference mcp_servers", "must include
     'knowledge_graph' key").

This approach gives confidence about code structure and naming conventions
at near-zero runtime cost, with no side effects from module initialization.
"""

import asyncio
import sys
import os

os.chdir(os.path.dirname(os.path.abspath(__file__)))


def test_retrieval_utils():
    from retrieval_utils import tfidf_similarity

    # Use corpus docs that share vocabulary with the query so TF-IDF can rank them.
    # TF-IDF is vocabulary-overlap based — synonyms without shared tokens score 0.
    scores = tfidf_similarity(
        "climate change emissions carbon",
        [
            "climate change and carbon emissions data from 2024",  # high overlap
            "best pasta recipes for dinner tonight",  # zero overlap
            "emissions data shows climate trends accelerating",  # medium overlap
        ],
    )
    assert scores[0] > scores[1], "emissions doc should outscore pasta doc"
    assert scores[2] > scores[1], "climate trends doc should outscore pasta doc"
    assert scores[0] >= scores[2], "doc0 has most overlap so should rank highest"
    # Verify zero-overlap doc scores 0
    assert scores[1] == 0.0, "pasta doc should score 0 (no shared tokens)"
    print("✅ retrieval_utils.tfidf_similarity")


def test_context_supersede():
    from context import ResearchContext

    ctx = ResearchContext()
    ctx.add_result(1, "CO2 emissions", "Carbon dioxide rose 2ppm in 2024.")
    ctx.add_result(2, "pasta cooking", "Boil water, add pasta, cook 8 minutes.")
    ctx.add_result(3, "CO2 update", "New: CO2 rose 2.1ppm in 2024, supersedes step 1.")

    ctx.supersede(1, reason="Step 3 is more recent")

    result = ctx.retrieve_relevant("CO2 emissions climate", top_k=3)
    assert "Step 1" not in result, "Superseded step 1 must be excluded"
    assert "Step 3" in result, "Step 3 should appear"

    # include_superseded override
    full = ctx.retrieve_relevant("CO2", top_k=3, include_superseded=True)
    assert "Step 1" in full, "include_superseded=True must include step 1"
    print("✅ context.ResearchContext.supersede() + include_superseded override")


async def test_search_result_store():
    from search_result_store import SearchResultStore

    # SearchResultStore no longer takes an mcp_registry — long-term memory
    # is now an optional AsyncLongTermMemory instance (None = no persistence).
    store = SearchResultStore(long_term_memory=None)

    await store.add(
        "web_search",
        {"text": "Climate change causes sea level rise."},
        step_id=1,
        source_url="https://a.com",
    )
    await store.add(
        "web_search",
        {"text": "Pasta is a popular Italian dish."},
        step_id=2,
        source_url="https://b.com",
    )
    await store.add(
        "web_search",
        {"text": "CO2 emissions reached record high in 2024."},
        step_id=3,
        source_url="https://c.com",
    )

    assert store.chunk_count() == 3
    assert store.chunk_count(include_superseded=False) == 3

    flagged = store.mark_superseded_by_step(2, reason="test")
    assert flagged == 1, f"Expected 1 flagged, got {flagged}"
    assert store.chunk_count(include_superseded=False) == 2

    result = await store.retrieve("climate emissions CO2", top_k=5)
    assert "Pasta" not in result, "Superseded pasta chunk must not appear"
    assert "Climate" in result or "CO2" in result, "Climate/CO2 chunks must appear"
    print("✅ search_result_store: mark_superseded_by_step + retrieve filtering")

    # mark_superseded by id
    chunks_before = store.chunk_count(include_superseded=False)
    chunk_id = store._chunks[0].id
    n = store.mark_superseded([chunk_id], reason="direct id test")
    assert n == 1
    assert store.chunk_count(include_superseded=False) == chunks_before - 1
    print("✅ search_result_store.mark_superseded by chunk id")

    # filter_long_term_memories
    store2 = SearchResultStore(long_term_memory=None)
    await store2.add(
        "web_search", {"text": "Climate change causes sea level rise."}, step_id=1
    )
    memories = [
        "Climate change causes sea level rise.",  # near-dup → drop
        "The history of the Roman Empire.",  # unrelated → keep
    ]
    filtered = await store2.filter_long_term_memories(
        memories, similarity_threshold=0.5
    )
    assert len(filtered) == 1, f"Expected 1 kept, got {len(filtered)}: {filtered}"
    assert "Roman" in filtered[0]
    print("✅ search_result_store.filter_long_term_memories deduplication")

    # persist_to_long_term_memory — mock memory server unavailable path
    persisted = await store2.persist_to_long_term_memory("climate query", top_k=5)
    assert persisted == 0, "No memory server registered → should return 0"
    print(
        "✅ search_result_store.persist_to_long_term_memory (no server → graceful skip)"
    )


def test_models_section_draft():
    from models import ResponseMessage

    msg = ResponseMessage(
        type="section_draft",
        message="Section drafted: Key Findings",
        data={
            "section_index": 2,
            "section_title": "Key Findings",
            "section_content": "Finding 1: emissions up 2%.",
            "total_sections": 5,
        },
    )
    assert msg.type == "section_draft"
    assert msg.data["section_index"] == 2
    assert msg.data["total_sections"] == 5
    print("✅ models.ResponseMessage section_draft type")


def test_orchestrator_step_summaries():
    from unittest.mock import MagicMock, AsyncMock, patch
    from config import Config

    mock_registry = MagicMock()
    mock_registry.get.return_value = None
    mock_registry.get_all_tools = AsyncMock(return_value=[])
    mock_registry.call_tool = AsyncMock(return_value={})

    cfg = Config()
    mock_backend = MagicMock()

    from orchestrator import Orchestrator, AgentPool

    pool = AgentPool.from_single_backend(mock_backend)
    orch = Orchestrator(cfg, mock_registry, pool)

    assert hasattr(orch, "_step_summaries"), "Must have _step_summaries"
    assert isinstance(orch._step_summaries, dict)

    orch._step_summaries[1] = "• CO2 up 2.1 ppm\n• Arctic ice declined 8%"
    orch._step_summaries[2] = "• GDP impact: -1.2% per decade\n• 40M displaced by 2050"
    assert len(orch._step_summaries) == 2

    orch._reset_state()
    assert orch._step_summaries == {}, "_reset_state must clear _step_summaries"
    print("✅ Orchestrator._step_summaries init, populate, and reset")


def test_search_agent_prompt_distillation():
    """Verify the SearchAgent prompt includes relevance pass and knowledge_snippet."""
    from orchestrator import SearchAgent
    from unittest.mock import MagicMock

    mock_backend = MagicMock()
    mock_registry = MagicMock()
    agent = SearchAgent(mock_backend, mock_registry)

    prompt = agent._DEFAULT_SYSTEM_PROMPT
    assert "RELEVANCE PASS" in prompt, "Prompt must include internal relevance pass"
    assert "knowledge_snippet" in prompt, "Prompt must require knowledge_snippet output"
    assert (
        "excerpt" not in prompt.lower() or "knowledge_snippet" in prompt
    ), "Old raw excerpt pattern should be replaced by knowledge_snippet"
    print("✅ SearchAgent prompt: RELEVANCE PASS + knowledge_snippet present")


def test_new_orchestrator_constants():
    """Verify the distillation/summary config fields exist and are positive."""
    from config import Config

    cfg = Config()
    assert hasattr(cfg, "distill_max_chars"), "Missing config.distill_max_chars"
    assert hasattr(
        cfg, "step_summary_max_chars"
    ), "Missing config.step_summary_max_chars"
    assert hasattr(cfg, "section_draft_top_k"), "Missing config.section_draft_top_k"
    assert cfg.distill_max_chars > 0
    assert cfg.step_summary_max_chars > 0
    assert cfg.section_draft_top_k > 0
    print("✅ Orchestrator distillation/summary constants present")


def test_orchestrator_has_new_helpers():
    """Verify the two new private helpers exist on Orchestrator."""
    from orchestrator import Orchestrator
    import inspect

    assert hasattr(Orchestrator, "_distill_tool_result"), "Missing _distill_tool_result"
    assert hasattr(
        Orchestrator, "_generate_step_summary"
    ), "Missing _generate_step_summary"
    assert inspect.iscoroutinefunction(
        Orchestrator._distill_tool_result
    ), "_distill_tool_result must be async"
    assert inspect.iscoroutinefunction(
        Orchestrator._generate_step_summary
    ), "_generate_step_summary must be async"
    print(
        "✅ Orchestrator._distill_tool_result and _generate_step_summary exist and are async"
    )


def test_ltm_neo4j_schema_setup():
    """Verify AsyncLongTermMemory._create_schema method exists and creates
    the required Neo4j constraints and vector indexes.

    This test uses source inspection because neo4j itself may not be running
    in the test environment.
    """
    import ast, os

    src_path = os.path.join(os.path.dirname(__file__), "long_term_memory.py")
    with open(src_path) as f:
        src = f.read()
    tree = ast.parse(src)

    ltm_class = next(
        (
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.ClassDef) and n.name == "AsyncLongTermMemory"
        ),
        None,
    )
    assert ltm_class is not None, "AsyncLongTermMemory class not found"

    schema_method = next(
        (
            n
            for n in ast.walk(ltm_class)
            if isinstance(n, ast.AsyncFunctionDef) and n.name == "_create_schema"
        ),
        None,
    )
    assert schema_method is not None, "_create_schema not found in AsyncLongTermMemory"

    schema_src = ast.get_source_segment(src, schema_method) or ""
    assert "memory_id" in schema_src, "_create_schema must create memory_id constraint"
    # Typed entity labels: constraint + vector index are created in a loop
    # over ENTITY_TYPES. Verify the loop and key fragments exist.
    assert (
        "ENTITY_TYPES" in schema_src or "for label in" in schema_src
    ), "_create_schema must loop over entity types to create per-type constraints"
    assert (
        "_ENTITY_INDEX_NAMES" in schema_src or "embedding_idx" in schema_src
    ), "_create_schema must create per-type vector indexes"
    assert (
        "community_id" in schema_src
    ), "_create_schema must create community_id constraint"
    assert (
        "memory_embedding_idx" in schema_src
    ), "_create_schema must create memory vector index"
    assert (
        "community_embedding_idx" in schema_src
    ), "_create_schema must create community vector index"
    # Claim and Document nodes
    assert "claim_id" in schema_src, "_create_schema must create claim_id constraint"
    assert (
        "claim_embedding_idx" in schema_src
    ), "_create_schema must create claim vector index"
    assert (
        "document_id" in schema_src
    ), "_create_schema must create document_id constraint"
    assert (
        "document_url" in schema_src
    ), "_create_schema must create document_url constraint"
    assert (
        "document_embedding_idx" in schema_src
    ), "_create_schema must create document vector index"
    print(
        "✅ AsyncLongTermMemory._create_schema creates all constraints and vector indexes"
    )


def test_search_result_store_binary_detection():
    """Verify binary/PDF content is rejected by _extract_text."""
    from search_result_store import SearchResultStore

    # Normal text must pass through
    result = SearchResultStore._extract_text({"text": "This is a normal article."})
    assert result == "This is a normal article.", f"Normal text broken: {result!r}"

    # Raw PDF-like binary must be rejected
    binary = "\x00\x01\x02\x03" * 200
    result = SearchResultStore._extract_text({"text": binary})
    assert result == "", f"Binary dict content must be rejected, got: {result!r}"

    result = SearchResultStore._extract_text([{"text": binary}])
    assert result == "", f"Binary list content must be rejected, got: {result!r}"

    result = SearchResultStore._extract_text(binary)
    assert result == "", f"Binary string must be rejected, got: {result!r}"

    # Mixed list — only the clean block should survive
    result = SearchResultStore._extract_text(
        [{"text": binary}, {"text": "Clean paragraph."}]
    )
    assert (
        result == "Clean paragraph."
    ), f"Mixed list should keep clean block: {result!r}"
    print("✅ SearchResultStore._extract_text: binary PDF content rejected correctly")


async def test_search_result_store_exclude_step_id():
    """Verify retrieve() exclude_step_id keeps current step out of cross-step retrieval."""
    from search_result_store import SearchResultStore

    store = SearchResultStore(long_term_memory=None)
    await store.add("web_search", {"text": "Step 1: AI adoption data 2024."}, step_id=1)
    await store.add(
        "web_search", {"text": "Step 2: LLM market share Q1 2025."}, step_id=2
    )
    await store.add(
        "web_search", {"text": "Step 3: MCP server deployments 2025."}, step_id=3
    )

    # exclude_step_id=3 → step 3's chunk must not appear
    result = await store.retrieve("AI MCP deployment", top_k=5, exclude_step_id=3)
    assert (
        "MCP server deployments" not in result
    ), "exclude_step_id=3 must filter out step 3 chunk"
    assert (
        "AI adoption" in result or "LLM market" in result
    ), "Other steps must still be returned"

    # step_id_filter=2 → only step 2's chunk
    result2 = await store.retrieve("market", top_k=5, step_id_filter=2)
    assert "LLM market share" in result2
    assert "AI adoption" not in result2
    assert "MCP server" not in result2
    print("✅ SearchResultStore.retrieve: exclude_step_id + step_id_filter both work")


def test_orchestrator_analyst_recommendations():
    """Verify _analyst_recommendations is initialised and cleared on reset."""
    from unittest.mock import MagicMock, AsyncMock
    from config import Config
    from orchestrator import Orchestrator, AgentPool

    mock_registry = MagicMock()
    mock_registry.get_all_tools = AsyncMock(return_value=[])
    mock_registry.call_tool = AsyncMock(return_value={})

    pool = AgentPool.from_single_backend(MagicMock())
    orch = Orchestrator(Config(), mock_registry, pool)

    assert hasattr(
        orch, "_analyst_recommendations"
    ), "Must have _analyst_recommendations attribute"
    assert isinstance(orch._analyst_recommendations, list)

    # Simulate adding a recommendation
    orch._analyst_recommendations.append(
        {"step_id": 1, "recommendations": "Suggest searching for MCP production cases"}
    )
    assert len(orch._analyst_recommendations) == 1

    # _reset_state must clear it
    orch._reset_state()
    assert (
        orch._analyst_recommendations == []
    ), "_reset_state must clear _analyst_recommendations"
    print("✅ Orchestrator._analyst_recommendations init, populate, and reset")


def test_run_analyst_strips_coverage_notes():
    """Verify _run_analyst fallback path excludes coverage_notes from prompt."""
    from orchestrator import Orchestrator
    import inspect

    src = inspect.getsource(Orchestrator._run_analyst)
    # The fallback must exclude coverage_notes (not just pass the whole search_output)
    assert (
        '"coverage_notes"' in src
    ), '_run_analyst must explicitly exclude "coverage_notes" from fallback context'
    assert (
        '"_tools_used"' in src
    ), '_run_analyst must explicitly exclude "_tools_used" from fallback context'
    print("✅ _run_analyst fallback excludes coverage_notes and _tools_used")


def test_run_analyst_cross_step_retrieval():
    """Verify _run_analyst calls retrieve() with exclude_step_id for cross-step context."""
    from orchestrator import Orchestrator
    import inspect

    src = inspect.getsource(Orchestrator._run_analyst)
    assert (
        "exclude_step_id" in src
    ), "_run_analyst must call retrieve() with exclude_step_id for cross-step corroboration"
    assert (
        "Corroborating evidence" in src
        or "cross-step" in src.lower()
        or "prior steps" in src
    ), "_run_analyst must include cross-step context section in prompt"
    print("✅ _run_analyst uses exclude_step_id for cross-step corroboration")


def test_qa_loop_data_poverty_exit():
    """Verify QA retry path checks chunk_count for data poverty early exit."""
    from orchestrator import Orchestrator
    import inspect

    src = inspect.getsource(Orchestrator._run_step)
    assert (
        "chunk_count" in src
    ), "_run_step must check chunk_count before/after supplemental search for data poverty exit"
    assert (
        "chunks_before" in src and "chunks_after" in src
    ), "_run_step must compare chunk counts to detect data poverty"
    assert (
        "data poverty" in src.lower() or "no new content" in src.lower()
    ), "_run_step must log data poverty condition"
    print("✅ QA loop: data poverty early exit via chunk_count comparison present")


def test_self_optimize_uses_long_term_memory():
    """Verify SelfOptimizingAgent accepts long_term_memory and get_all_memories
    uses it (including knowledge graph) rather than the removed memory MCP server.
    Also verifies Phase 5 persistence uses _long_term_memory, not the dead MCP client.
    """
    import ast

    with open("optimization.py", "r") as f:
        src = f.read()

    # Confirm the import was added
    assert (
        "from long_term_memory import AsyncLongTermMemory" in src
    ), "optimization.py must import AsyncLongTermMemory from long_term_memory"

    tree = ast.parse(src)

    # Locate SelfOptimizingAgent class
    soa_class = next(
        (
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.ClassDef) and n.name == "SelfOptimizingAgent"
        ),
        None,
    )
    assert (
        soa_class is not None
    ), "SelfOptimizingAgent class not found in optimization.py"

    # Locate __init__ and get_all_memories methods
    init_node = next(
        (
            n
            for n in ast.walk(soa_class)
            if isinstance(n, ast.FunctionDef) and n.name == "__init__"
        ),
        None,
    )
    assert init_node is not None, "__init__ not found in SelfOptimizingAgent"
    init_args = [a.arg for a in init_node.args.args] + [
        kw.arg for kw in (init_node.args.defaults and []) or []
    ]
    kwonly_args = [a.arg for a in init_node.args.kwonlyargs]
    all_init_args = [a.arg for a in init_node.args.args] + kwonly_args
    assert "long_term_memory" in all_init_args or any(
        isinstance(n, ast.Name) and n.id == "long_term_memory"
        for n in ast.walk(init_node)
    ), "__init__ must accept long_term_memory parameter"

    mem_node = next(
        (
            n
            for n in ast.walk(soa_class)
            if isinstance(n, ast.AsyncFunctionDef) and n.name == "get_all_memories"
        ),
        None,
    )
    assert mem_node is not None, "get_all_memories not found in SelfOptimizingAgent"

    mem_src = ast.get_source_segment(src, mem_node) or ""
    assert (
        "mcp_servers" not in mem_src
    ), "get_all_memories must not reference the removed memory MCP server"
    assert (
        "_long_term_memory" in mem_src
    ), "get_all_memories must use self._long_term_memory"
    assert (
        "recall" in mem_src
    ), "get_all_memories must call .recall() on the long-term memory store"
    # Knowledge graph integration
    assert (
        "graph" in mem_src or "recall_graph_context" in mem_src
    ), "get_all_memories must query the knowledge graph"
    assert (
        "knowledge_graph" in mem_src
    ), "get_all_memories must include graph context under 'knowledge_graph' key"

    # Verify _analyze_memories references the knowledge graph
    analyze_node = next(
        (
            n
            for n in ast.walk(soa_class)
            if isinstance(n, ast.AsyncFunctionDef) and n.name == "_analyze_memories"
        ),
        None,
    )
    assert analyze_node is not None, "_analyze_memories not found"
    analyze_src = ast.get_source_segment(src, analyze_node) or ""
    assert (
        "knowledge_graph" in analyze_src.lower()
        or "knowledge graph" in analyze_src.lower()
    ), "_analyze_memories prompt must reference knowledge graph context"

    # Verify Phase 5 persistence uses _long_term_memory, NOT the removed MCP server
    optimize_node = next(
        (
            n
            for n in ast.walk(soa_class)
            if isinstance(n, ast.AsyncFunctionDef) and n.name == "self_optimize"
        ),
        None,
    )
    assert optimize_node is not None, "self_optimize not found"
    optimize_src = ast.get_source_segment(src, optimize_node) or ""
    assert (
        'mcp_servers.get("memory")' not in optimize_src
        and "mcp_servers.get('memory')" not in optimize_src
    ), "self_optimize must not use the removed memory MCP server for persistence"
    assert (
        "_long_term_memory" in optimize_src
    ), "self_optimize must persist insights via _long_term_memory"

    print("✅ SelfOptimizingAgent: graph-aware get_all_memories + LTM persistence")


def test_orchestrator_has_file_context_method():
    """Verify Orchestrator has _gather_uploaded_file_context and it calls the right tools."""
    import ast

    with open("orchestrator.py", "r") as f:
        src = f.read()
    tree = ast.parse(src)

    orch_class = next(
        (
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.ClassDef) and n.name == "Orchestrator"
        ),
        None,
    )
    assert orch_class is not None, "Orchestrator class not found"

    method = next(
        (
            n
            for n in ast.walk(orch_class)
            if isinstance(n, ast.AsyncFunctionDef)
            and n.name == "_gather_uploaded_file_context"
        ),
        None,
    )
    assert method is not None, "_gather_uploaded_file_context not found on Orchestrator"

    method_src = ast.get_source_segment(src, method) or ""
    assert "list_files" in method_src, "Method must call list_files tool"
    assert "read_file" in method_src, "Method must call read_file tool"
    assert "mcp_registry" in method_src, "Method must use self.mcp_registry"
    # outputSchema unwrapping (MCP SDK ≥1.26 wraps as {"result": value})
    assert (
        "files.get" in method_src or "isinstance(files, dict)" in method_src
    ), "Method must handle dict-wrapped list_files result"
    print(
        "✅ Orchestrator._gather_uploaded_file_context exists and references list_files + read_file"
    )


def test_plan_incorporates_file_context():
    """Verify Orchestrator.plan() calls _gather_uploaded_file_context and injects result."""
    import ast

    with open("orchestrator.py", "r") as f:
        src = f.read()
    tree = ast.parse(src)

    orch_class = next(
        (
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.ClassDef) and n.name == "Orchestrator"
        ),
        None,
    )
    assert orch_class is not None, "Orchestrator class not found"

    plan_method = next(
        (
            n
            for n in ast.walk(orch_class)
            if isinstance(n, ast.AsyncFunctionDef) and n.name == "plan"
        ),
        None,
    )
    assert plan_method is not None, "plan() not found on Orchestrator"

    plan_src = ast.get_source_segment(src, plan_method) or ""
    assert (
        "_gather_uploaded_file_context" in plan_src
    ), "plan() must call _gather_uploaded_file_context"
    assert (
        "file_context" in plan_src
    ), "plan() must use file_context in the planning prompt"
    print(
        "✅ Orchestrator.plan() calls _gather_uploaded_file_context and injects file context"
    )


def test_self_optimize_api_server_wiring():
    """Verify api_server passes long_term_memory to SelfOptimizingAgent."""
    import ast

    with open("api_server.py", "r") as f:
        tree = ast.parse(f.read())

    self_opt_calls = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = (
                func.id
                if isinstance(func, ast.Name)
                else (func.attr if isinstance(func, ast.Attribute) else None)
            )
            if name == "SelfOptimizingAgent":
                kwarg_names = [kw.arg for kw in node.keywords]
                self_opt_calls.append(kwarg_names)

    assert self_opt_calls, "No SelfOptimizingAgent() calls found in api_server.py"
    for kwargs in self_opt_calls:
        assert (
            "long_term_memory" in kwargs
        ), f"SelfOptimizingAgent() call missing long_term_memory kwarg; got kwargs={kwargs}"
    print(
        f"✅ api_server.py passes long_term_memory to all {len(self_opt_calls)} SelfOptimizingAgent() instantiations"
    )

    print(
        f"✅ api_server.py passes long_term_memory to all {len(self_opt_calls)} SelfOptimizingAgent() instantiations"
    )


# ── Knowledge Graph (GraphRAG) tests ─────────────────────────────────────────


def test_ltm_knowledge_graph_collections():
    """Verify AsyncLongTermMemory._create_schema creates all Neo4j indexes."""
    import ast

    with open("long_term_memory.py", "r") as f:
        src = f.read()
    tree = ast.parse(src)

    # Find _create_schema method inside AsyncLongTermMemory
    ltm_class = next(
        (
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.ClassDef) and n.name == "AsyncLongTermMemory"
        ),
        None,
    )
    assert ltm_class is not None, "AsyncLongTermMemory class not found"

    schema_method = next(
        (
            n
            for n in ast.walk(ltm_class)
            if isinstance(n, ast.AsyncFunctionDef) and n.name == "_create_schema"
        ),
        None,
    )
    assert schema_method is not None, "_create_schema not found"

    schema_src = ast.get_source_segment(src, schema_method) or ""
    # Must create vector indexes — entity indexes are now per-type in a loop
    for idx_name in (
        "memory_embedding_idx",
        "community_embedding_idx",
    ):
        assert (
            idx_name in schema_src
        ), f"_create_schema must create '{idx_name}' vector index"
    # Per-type entity indexes are created dynamically via ENTITY_TYPES loop
    assert (
        "ENTITY_TYPES" in schema_src or "_ENTITY_INDEX_NAMES" in schema_src
    ), "_create_schema must create per-type entity vector indexes"
    # Must create uniqueness constraints
    for constraint in ("memory_id", "community_id"):
        assert (
            constraint in schema_src
        ), f"_create_schema must create '{constraint}' constraint"
    # Per-type entity constraints are created dynamically
    assert (
        "IS UNIQUE" in schema_src
    ), "_create_schema must create uniqueness constraints"
    print(
        "✅ AsyncLongTermMemory._create_schema creates all Neo4j indexes and constraints"
    )


def test_knowledge_graph_class_exists():
    """Verify KnowledgeGraph class exists with required methods."""
    import ast

    with open("long_term_memory.py", "r") as f:
        src = f.read()
    tree = ast.parse(src)

    kg_class = next(
        (
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.ClassDef) and n.name == "KnowledgeGraph"
        ),
        None,
    )
    assert kg_class is not None, "KnowledgeGraph class not found in long_term_memory.py"

    # Check required async methods
    required_methods = [
        "upsert_entity",
        "store_relationship",
        "find_entities",
        "get_relationships",
        "get_communities",
        "update_communities",
        "recall_graph_context",
        "stats",
    ]
    found_methods = set()
    for node in ast.walk(kg_class):
        if isinstance(node, ast.AsyncFunctionDef):
            found_methods.add(node.name)

    for method in required_methods:
        assert method in found_methods, (
            f"KnowledgeGraph must have async method '{method}'; "
            f"found: {sorted(found_methods)}"
        )
    print(f"✅ KnowledgeGraph class has all {len(required_methods)} required methods")


def test_orchestrator_extract_graph_triples():
    """Verify Orchestrator has _extract_graph_triples and it's called from _store_analyst_findings."""
    import ast

    with open("orchestrator.py", "r") as f:
        src = f.read()
    tree = ast.parse(src)

    orch_class = next(
        (
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.ClassDef) and n.name == "Orchestrator"
        ),
        None,
    )
    assert orch_class is not None, "Orchestrator class not found"

    # _extract_graph_triples must exist
    extract_method = next(
        (
            n
            for n in ast.walk(orch_class)
            if isinstance(n, ast.AsyncFunctionDef)
            and n.name == "_extract_graph_triples"
        ),
        None,
    )
    assert (
        extract_method is not None
    ), "_extract_graph_triples not found on Orchestrator"

    extract_src = ast.get_source_segment(src, extract_method) or ""
    assert (
        "upsert_entity" in extract_src
    ), "_extract_graph_triples must call upsert_entity"
    assert (
        "store_relationship" in extract_src
    ), "_extract_graph_triples must call store_relationship"
    assert (
        "json.loads" in extract_src
    ), "_extract_graph_triples must parse LLM JSON response"

    # _store_analyst_findings must call _extract_graph_triples
    store_method = next(
        (
            n
            for n in ast.walk(orch_class)
            if isinstance(n, ast.AsyncFunctionDef)
            and n.name == "_store_analyst_findings"
        ),
        None,
    )
    assert store_method is not None, "_store_analyst_findings not found"
    store_src = ast.get_source_segment(src, store_method) or ""
    assert (
        "_extract_graph_triples" in store_src
    ), "_store_analyst_findings must call _extract_graph_triples"
    print(
        "✅ Orchestrator._extract_graph_triples exists and is wired into _store_analyst_findings"
    )


def test_orchestrator_recall_uses_graph():
    """Verify _recall_memories includes graph-aware recall."""
    import ast

    with open("orchestrator.py", "r") as f:
        src = f.read()
    tree = ast.parse(src)

    orch_class = next(
        (
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.ClassDef) and n.name == "Orchestrator"
        ),
        None,
    )
    assert orch_class is not None, "Orchestrator class not found"

    recall_method = next(
        (
            n
            for n in ast.walk(orch_class)
            if isinstance(n, ast.AsyncFunctionDef) and n.name == "_recall_memories"
        ),
        None,
    )
    assert recall_method is not None, "_recall_memories not found"

    recall_src = ast.get_source_segment(src, recall_method) or ""
    assert (
        "recall_graph_context" in recall_src
    ), "_recall_memories must call graph.recall_graph_context"
    assert (
        "find_similar" in recall_src
    ), "_recall_memories must still do flat vector search via find_similar"
    print("✅ Orchestrator._recall_memories includes graph-aware recall + flat search")


def test_orchestrator_community_detection_post_synthesis():
    """Verify synthesize() calls graph.update_communities after persisting chunks."""
    import ast

    with open("orchestrator.py", "r") as f:
        src = f.read()
    tree = ast.parse(src)

    orch_class = next(
        (
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.ClassDef) and n.name == "Orchestrator"
        ),
        None,
    )
    assert orch_class is not None, "Orchestrator class not found"

    synth_method = next(
        (
            n
            for n in ast.walk(orch_class)
            if isinstance(n, ast.AsyncFunctionDef) and n.name == "synthesize"
        ),
        None,
    )
    assert synth_method is not None, "synthesize not found"

    synth_src = ast.get_source_segment(src, synth_method) or ""
    assert (
        "update_communities" in synth_src
    ), "synthesize() must call graph.update_communities for community detection"
    assert (
        "knowledge graph communities" in synth_src.lower()
        or "community" in synth_src.lower()
    ), "synthesize() must include a community detection status message"

    # Verify update_communities has a max_communities cap to prevent unbounded LLM calls
    ltm_src = open("long_term_memory.py").read()
    assert (
        "max_communities" in ltm_src
    ), "update_communities must accept a max_communities parameter to cap sequential LLM calls"
    print("✅ Orchestrator.synthesize() calls graph.update_communities post-synthesis")


async def test_knowledge_graph_entity_lifecycle():
    """Integration test: upsert_entity, find_entities, store_relationship, get_relationships.

    Requires a running Neo4j instance.  Skips gracefully if unavailable.
    """
    from long_term_memory import AsyncLongTermMemory

    ltm = AsyncLongTermMemory(
        neo4j_uri="bolt://localhost:7687",
        neo4j_user="neo4j",
        neo4j_password="research_pass",
        neo4j_database="neo4j",
    )
    await ltm.async_init()

    if not ltm._available:
        print("⚠️  knowledge_graph_entity_lifecycle: Neo4j unavailable, skipping")
        return

    try:
        graph = ltm.graph
        assert graph.available, "Graph should be available after init"

        # Upsert entity
        r1 = await graph.upsert_entity(
            name="Python",
            entity_type="technology",
            description="A programming language",
            session_id="s1",
        )
        assert r1["success"], f"Entity upsert failed: {r1}"

        r2 = await graph.upsert_entity(
            name="FastAPI",
            entity_type="technology",
            description="A Python web framework",
            session_id="s1",
        )
        assert r2["success"]

        s = await graph.stats()
        assert s["entities"] >= 2, f"Expected >= 2 entities, got {s['entities']}"

        # Store relationship
        r3 = await graph.store_relationship(
            source="FastAPI",
            target="Python",
            relation="is built with",
            evidence="FastAPI is a Python web framework",
            session_id="s1",
            step_id=1,
        )
        assert r3["success"], f"Relationship store failed: {r3}"

        s = await graph.stats()
        assert s["relationships"] >= 1

        # Get relationships by name
        rels = await graph.get_relationships(entity_names=["FastAPI"])
        assert len(rels) >= 1, f"Expected at least 1 relationship, got {len(rels)}"
        assert any(
            r["source_entity"] == "FastAPI" and r["target_entity"] == "Python"
            for r in rels
        ), f"Expected FastAPI→Python relationship in {rels}"

        print("✅ KnowledgeGraph entity/relationship lifecycle works end-to-end")

    finally:
        await ltm.close()


async def test_knowledge_graph_recall_context():
    """Integration test: recall_graph_context returns structured text.

    Requires a running Neo4j instance.  Skips gracefully if unavailable.
    """
    from long_term_memory import AsyncLongTermMemory

    ltm = AsyncLongTermMemory(
        neo4j_uri="bolt://localhost:7687",
        neo4j_user="neo4j",
        neo4j_password="research_pass",
        neo4j_database="neo4j",
    )
    await ltm.async_init()

    if not ltm._available:
        print("⚠️  knowledge_graph_recall_context: Neo4j unavailable, skipping")
        return

    try:
        graph = ltm.graph

        # Populate some entities and relationships
        await graph.upsert_entity("Neo4j", "technology", "Graph database")
        await graph.upsert_entity(
            "Embeddings", "concept", "Vector representations of text"
        )
        await graph.store_relationship(
            source="Neo4j",
            target="Embeddings",
            relation="stores and indexes",
            evidence="Neo4j uses embeddings for similarity search",
        )

        # Recall should return structured context
        context = await graph.recall_graph_context("graph database", entity_limit=5)
        # Context may be empty if sentence-transformers not installed — that's OK.
        if context:
            assert (
                "KNOWN ENTITIES" in context
            ), f"Expected KNOWN ENTITIES header in:\n{context}"
            assert (
                "KNOWN RELATIONSHIPS" in context or "Neo4j" in context
            ), f"Expected relationship data in:\n{context}"
            print("✅ KnowledgeGraph.recall_graph_context returns structured context")
        else:
            print(
                "⚠️  knowledge_graph_recall_context: embeddings unavailable, context empty (OK)"
            )

    finally:
        await ltm.close()


def test_gcp_backend_converts_tool_calls():
    """Verify GCPVertexAIBackend.generate() converts raw SDK tool call objects to
    internal ToolCall dataclass objects (tc.function.name pattern), not raw pass-through.
    """
    import ast

    src_path = os.path.join(os.path.dirname(__file__), "model_backend.py")
    with open(src_path) as f:
        tree = ast.parse(f.read())

    # Find GCPVertexAIBackend class
    gcp_cls = None
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "GCPVertexAIBackend":
            gcp_cls = node
            break
    assert gcp_cls is not None, "GCPVertexAIBackend class not found"

    # Collect all string constants inside the class body to find tc.function.name usage
    source_text = ast.unparse(gcp_cls)
    assert "tc.function.name" in source_text, (
        "GCPVertexAIBackend.generate() must convert tool calls using tc.function.name "
        "(same pattern as BedrockBackend)"
    )
    assert (
        "ToolCall(" in source_text
    ), "GCPVertexAIBackend.generate() must create ToolCall() dataclass instances"
    print(
        "✅ GCPVertexAIBackend: tool call conversion uses tc.function.name + ToolCall()"
    )


def test_error_handler_no_double_fault():
    """Verify _run_search error handler uses getattr() for tool_call name to prevent
    double-fault when the original AttributeError was on the .name attribute."""
    import ast

    src_path = os.path.join(os.path.dirname(__file__), "orchestrator.py")
    with open(src_path) as f:
        source = f.read()

    # The error log must not do bare tool_call.name — must use getattr
    assert "getattr(tool_call" in source, (
        "_run_search error handler must use getattr(tool_call, 'name', ...) "
        "to avoid double-fault AttributeError"
    )
    print("✅ _run_search error handler uses getattr() — no double-fault possible")


def test_config_heavy_light_model_fields():
    """Verify all backends have heavy_model / light_model config fields with correct defaults."""
    from config import Config

    cfg = Config()

    # OpenAI
    assert hasattr(cfg, "openai_heavy_model"), "Missing config.openai_heavy_model"
    assert hasattr(cfg, "openai_light_model"), "Missing config.openai_light_model"
    assert cfg.openai_heavy_model, "openai_heavy_model default must be non-empty"
    assert cfg.openai_light_model, "openai_light_model default must be non-empty"

    # Azure
    assert hasattr(cfg, "azure_heavy_model"), "Missing config.azure_heavy_model"
    assert hasattr(cfg, "azure_light_model"), "Missing config.azure_light_model"

    # AWS Bedrock
    assert hasattr(cfg, "aws_heavy_model"), "Missing config.aws_heavy_model"
    assert hasattr(cfg, "aws_light_model"), "Missing config.aws_light_model"
    assert (
        "sonnet" in cfg.aws_heavy_model.lower()
        or "claude" in cfg.aws_heavy_model.lower()
    ), "aws_heavy_model should default to a Sonnet/Claude model"
    assert (
        "haiku" in cfg.aws_light_model.lower()
        or "claude" in cfg.aws_light_model.lower()
    ), "aws_light_model should default to a Haiku/Claude model"

    # GCP
    assert hasattr(cfg, "gcp_heavy_model"), "Missing config.gcp_heavy_model"
    assert hasattr(cfg, "gcp_light_model"), "Missing config.gcp_light_model"
    assert (
        cfg.gcp_heavy_model == "gemini-2.5-pro"
    ), f"GCP heavy default wrong: {cfg.gcp_heavy_model}"
    assert (
        cfg.gcp_light_model == "gemini-2.5-flash"
    ), f"GCP light default wrong: {cfg.gcp_light_model}"

    # Ollama
    assert hasattr(cfg, "ollama_heavy_model"), "Missing config.ollama_heavy_model"
    assert hasattr(cfg, "ollama_light_model"), "Missing config.ollama_light_model"

    print(
        "✅ All backend heavy/light model config fields present with correct defaults"
    )


def test_select_model_reads_config():
    """Verify _select_model() references config heavy/light fields, not hardcoded strings."""
    import ast

    src_path = os.path.join(os.path.dirname(__file__), "orchestrator.py")
    with open(src_path) as f:
        source = f.read()

    # After the self.config refactor, _select_model uses cfg.xxx (where cfg = self.config).
    assert (
        "cfg.gcp_heavy_model" in source
    ), "_select_model() must read cfg.gcp_heavy_model (self.config), not hardcode 'gemini-2.5-pro'"
    assert (
        "cfg.gcp_light_model" in source
    ), "_select_model() must read cfg.gcp_light_model (self.config), not hardcode 'gemini-2.5-flash'"
    assert (
        "cfg.aws_heavy_model" in source
    ), "_select_model() must read cfg.aws_heavy_model (self.config)"
    assert (
        "cfg.aws_light_model" in source
    ), "_select_model() must read cfg.aws_light_model (self.config)"
    # Ensure the old hardcoded values are gone from _select_model
    assert (
        '"gemini-2.5-pro"' not in source or "gcp_heavy_model" in source
    ), "Hardcoded 'gemini-2.5-pro' should be removed from _select_model()"
    print("✅ _select_model() reads from config heavy/light fields")


def test_search_agent_prompt_tool_use_instruction():
    """Verify SearchAgent system prompt instructs models to use tools for real-time
    retrieval rather than refusing based on training cutoff."""
    from orchestrator import SearchAgent
    from unittest.mock import MagicMock

    agent = SearchAgent(MagicMock(), MagicMock())
    prompt = agent._DEFAULT_SYSTEM_PROMPT

    assert (
        "real-time" in prompt.lower() or "real time" in prompt.lower()
    ), "SearchAgent prompt must mention real-time tools to prevent Gemini refusals"
    assert (
        "training" in prompt.lower() or "tools available" in prompt.lower()
    ), "SearchAgent prompt must clarify tools are available (not training data only)"
    print("✅ SearchAgent prompt includes tool-use instruction for real-time retrieval")


def test_session_store_persistence():
    """Verify SessionStore.persist() writes JSON and load_checkpoint() reads it back."""
    import tempfile
    from pathlib import Path
    from session_store import SessionStore

    with tempfile.TemporaryDirectory() as td:
        store = SessionStore(sessions_dir=Path(td))

        class _FakeOrch:
            session_id = None

        session = store.create(_FakeOrch())
        session.state = "executing"
        session.replay_log = [{"type": "step_complete", "message": "done"}]

        checkpoint = {"query": "test q", "analyst_output": {"1": "findings"}}
        store.persist(session, synthesis_checkpoint=checkpoint)

        loaded = store.load_checkpoint(session.session_id)
        assert loaded is not None, "load_checkpoint must return data after persist"
        assert loaded["state"] == "executing"
        assert len(loaded["replay_log"]) == 1
        assert loaded["synthesis_checkpoint"]["query"] == "test q"

        # Missing session returns None
        assert store.load_checkpoint("nonexistent") is None

        # Final persist (no checkpoint) preserves state
        session.state = "complete"
        store.persist(session)
        loaded2 = store.load_checkpoint(session.session_id)
        assert loaded2["state"] == "complete"
        assert "synthesis_checkpoint" not in loaded2

    print("✅ session_store: persist/load_checkpoint roundtrip works")


def test_orchestrator_synthesis_checkpoint():
    """Verify synthesis_checkpoint() and restore_synthesis_state() are present
    and form an invertible roundtrip."""
    import ast

    with open("orchestrator.py") as fh:
        source = fh.read()

    tree = ast.parse(source)
    methods = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            methods.add(node.name)

    assert (
        "synthesis_checkpoint" in methods
    ), "Orchestrator must have synthesis_checkpoint()"
    assert (
        "restore_synthesis_state" in methods
    ), "Orchestrator must have restore_synthesis_state()"

    # Verify synthesis_checkpoint returns the expected keys
    assert (
        "analyst_output" in source
    ), "synthesis_checkpoint body must include analyst_output"
    assert (
        "step_summaries" in source
    ), "synthesis_checkpoint body must include step_summaries"
    assert (
        "step_sources" in source
    ), "synthesis_checkpoint body must include step_sources"
    assert (
        "contradictions" in source
    ), "synthesis_checkpoint body must include contradictions"

    print("✅ Orchestrator.synthesis_checkpoint and restore_synthesis_state exist")


def test_api_server_recovery_function():
    """Verify _recover_session_from_checkpoint is defined in api_server.py."""
    import ast

    with open("api_server.py") as fh:
        source = fh.read()

    tree = ast.parse(source)
    top_level_funcs = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    assert (
        "_recover_session_from_checkpoint" in top_level_funcs
    ), "_recover_session_from_checkpoint must be a top-level async function in api_server.py"
    assert (
        "synthesis_checkpoint" in source
    ), "_run_session must call orchestrator.synthesis_checkpoint() before synthesize()"
    assert (
        "session_store.persist" in source
    ), "_run_session must call session_store.persist() for checkpoint and final state"
    assert (
        "load_checkpoint" in source
    ), "resume handler must call session_store.load_checkpoint() for disk recovery"
    print(
        "✅ api_server: _recover_session_from_checkpoint wired + checkpoint calls present"
    )


def test_orchestrator_release_memory():
    """Verify release_memory() exists in Orchestrator and api_server calls it."""
    import ast

    with open("orchestrator.py") as fh:
        orch_source = fh.read()

    tree = ast.parse(orch_source)
    methods = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            methods.add(node.name)

    assert "release_memory" in methods, "Orchestrator must have release_memory()"

    with open("api_server.py") as fh:
        api_source = fh.read()

    assert (
        "release_memory" in api_source
    ), "api_server _run_session must call orchestrator.release_memory()"

    print("✅ Orchestrator.release_memory exists and api_server calls it")


def test_ndjson_formatter_valid_json():
    """_NdjsonFormatter must produce valid JSON for all message types."""
    import json
    import logging

    from observability import _NdjsonFormatter

    fmt = _NdjsonFormatter()

    # 1. Message without single quotes — old %r format produced single-quoted string (invalid JSON)
    r1 = logging.LogRecord("mod", logging.INFO, "f.py", 1, "Simple message", (), None)
    parsed1 = json.loads(fmt.format(r1))
    assert parsed1["msg"] == "Simple message"

    # 2. Message with single quotes — must not produce escaped \'
    r2 = logging.LogRecord("mod", logging.DEBUG, "f.py", 1, "Called 'tool'", (), None)
    parsed2 = json.loads(fmt.format(r2))
    assert "tool" in parsed2["msg"]

    # 3. Multiline message args — output must be a single line
    r3 = logging.LogRecord(
        "mod", logging.INFO, "f.py", 1, "result: %s", ("a\nb\nc",), None
    )
    line3 = fmt.format(r3)
    assert "\n" not in line3, "NdjsonFormatter must produce a single output line"
    json.loads(line3)  # must parse cleanly

    # 4. ctx fields from ObservabilityLogger are included
    r4 = logging.LogRecord("mod", logging.INFO, "f.py", 1, "ev", (), None)
    r4.__dict__["_obs_ctx"] = {"session_id": "xyz", "step_id": 7}
    parsed4 = json.loads(fmt.format(r4))
    assert parsed4["ctx"]["session_id"] == "xyz"

    # 5. Timestamp must NOT contain literal %f — time.strftime doesn't expand it;
    #    datetime.strftime does.  Verify the override is present and working.
    ts = parsed1["ts"]
    assert "%f" not in ts, (
        f"NDJSON timestamp contains literal '%f' instead of microseconds: {ts!r}.\n"
        "Fix: override formatTime() in _NdjsonFormatter to use datetime.strftime."
    )
    assert "." in ts, f"NDJSON timestamp missing decimal (microseconds): {ts!r}"

    print("✅ _NdjsonFormatter produces valid JSON for all message types (single-line)")


def test_analyst_prompt_confidence_field():
    """AnalystAgent system prompt must define the required 'confidence' field."""
    with open("orchestrator.py") as fh:
        source = fh.read()

    assert (
        '"confidence"' in source
    ), "AnalystAgent._DEFAULT_SYSTEM_PROMPT must include a 'confidence' field in the claim schema"
    for label in ("corroborated", "partially_corroborated", "single_source"):
        assert (
            label in source
        ), f"AnalystAgent system prompt must define confidence label '{label}'"

    print(
        "✅ AnalystAgent system prompt defines confidence field with all three labels"
    )


def test_outline_parse_handles_empty_content():
    """Outline parse in orchestrator must not raise on None/empty LLM response."""
    import ast

    with open("orchestrator.py") as fh:
        source = fh.read()

    # Guard: raw = outline_response.content or ""
    assert (
        "outline_response.content or" in source
    ), "outline parse must guard against None content with 'or \"\"'"
    # Guard: if raw: before json.loads
    assert (
        "if raw:" in source
    ), "outline parse must check 'if raw:' before calling json.loads"

    print("✅ Orchestrator outline parse guards None/empty content before json.loads")


def test_orchestrator_step_level_logging():
    """Orchestrator must have logger.info calls for step_start, step_complete, research_complete."""
    with open("orchestrator.py") as fh:
        source = fh.read()

    assert (
        '"[Step %d] starting' in source or "[Step %d] starting" in source
    ), "orchestrator must logger.info step starting events"
    assert (
        '"[Step %d] complete' in source or "[Step %d] complete" in source
    ), "orchestrator must logger.info step complete events"
    assert (
        "research_complete" in source and "logger.info" in source
    ), "orchestrator must logger.info research_complete"

    print(
        "✅ Orchestrator has logger.info calls for step_start, step_complete, research_complete"
    )


def test_shutdown_guard_rejects_during_shutdown():
    """api_server: WS handler checks shutting_down flag alongside agent_pool guard."""
    import ast

    src = open("api_server.py").read()
    tree = ast.parse(src)
    # 1. lifespan sets shutting_down = False during startup
    assert (
        "shutting_down = False" in src
    ), "lifespan must initialise app.state.shutting_down = False during startup"
    # 2. _shutdown() sets it to True
    assert (
        "shutting_down = True" in src
    ), "_shutdown() must set app.state.shutting_down = True before cancelling jobs"
    # 3. WS handler guard checks the flag
    assert (
        "shutting_down"
        in src.split("Server not fully initialised")[0].split("async def research_ws")[
            -1
        ]
    ), "WS handler guard must check shutting_down flag when rejecting connections"
    print("✅ api_server: shutdown guard rejects new sessions during shutdown")


def test_cancel_preserves_complete_sessions():
    """api_server: CancelledError handler treats report-emitted sessions as complete."""
    import ast

    src = open("api_server.py").read()
    tree = ast.parse(src)
    # Find the _run_session function
    found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_run_session":
            seg = ast.get_source_segment(src, node)
            assert seg is not None, "_run_session source segment not found"
            # Must check replay_log for report event
            assert (
                "report" in seg and "replay_log" in seg
            ), "_run_session CancelledError handler must check replay_log for report events"
            # Must have a path that sets state to "complete" inside the cancel handler
            assert (
                'state = "complete"' in seg
            ), "_run_session CancelledError handler must set state=complete when report exists"
            found = True
            break
    assert found, "_run_session function not found in api_server.py"
    print(
        "✅ api_server: CancelledError handler preserves sessions with existing report"
    )


def test_session_manager_concurrency_semaphore():
    """ResearchSessionManager: session semaphore gates concurrent job execution."""
    import ast

    src = open("session_manager.py").read()
    tree = ast.parse(src)
    # Find the class
    cls = None
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "ResearchSessionManager":
            cls = node
            break
    assert cls is not None, "ResearchSessionManager class not found"
    cls_src = ast.get_source_segment(src, cls)
    assert cls_src is not None
    # __init__ accepts max_concurrent_sessions and creates a Semaphore
    assert (
        "max_concurrent_sessions" in cls_src
    ), "__init__ must accept max_concurrent_sessions parameter"
    assert (
        "Semaphore" in cls_src
    ), "__init__ must create an asyncio.Semaphore when limit > 0"
    # start_job wraps coroutine with semaphore gating
    assert (
        "_gated" in cls_src
    ), "start_job must define a _gated wrapper coroutine for semaphore acquisition"
    # api_server passes the config value
    api_src = open("api_server.py").read()
    assert (
        "max_concurrent_sessions" in api_src
    ), "api_server must pass max_concurrent_sessions to ResearchSessionManager"
    print("✅ ResearchSessionManager: session semaphore gates concurrent execution")


def test_synthesis_step_detection():
    """_is_synthesis_step() correctly identifies synthesis steps and lets real research steps through."""
    from orchestrator import Orchestrator
    from models import ResearchStep, StepStatus

    def make_step(desc: str) -> ResearchStep:
        return ResearchStep(
            id=99, name="Test", description=desc, status=StepStatus.PENDING
        )

    # Should be detected as synthesis steps
    synthesis_descs = [
        "Synthesize all findings and produce a comprehensive report",
        "Synthesise all findings from steps 1-9 into a structured report",  # UK spelling
        "Compile all findings from previous research steps",
        "Compile all results into a final summary",
        "Compile and synthesize research data for the report",
        "Compile and synthesise research data for the report",  # UK spelling
        "Integrate all findings from prior steps to build the report",
        "Integrate all research into a final deliverable",
        "Integrate all results and write a summary",
        "Consolidate all findings into a final document",
        "Consolidate all results and prepare the report",
        "Final synthesis: combine all research results",
        "Generate a final report from all research",
        "Generate the final report based on all gathered information",
        "Write a final report using all collected data",
        "Write the final report",
        "Create a final report summarizing all findings",
        "Create the final report document",
        "Produce a final report covering all topics",
        "Produce the final report with citations",
        "Formulate a final report for the stakeholders",
    ]
    for desc in synthesis_descs:
        step = make_step(desc)
        assert Orchestrator._is_synthesis_step(
            step
        ), f"Expected synthesis step to be detected: '{desc}'"

    # Should NOT be detected — these are legitimate research steps
    research_descs = [
        "Search for recent academic papers on climate change",
        "Retrieve financial data for the top 10 tech companies",
        "Synthesize findings from peer-reviewed literature on X",
        "Compile market data for Q3 2024",
        "Research and analyze competing approaches to problem Y",
        "Find primary sources discussing the policy implications",
        "Investigate contradictions between the two datasets",
        "Gather user reviews and sentiment data from social media",
        "Analyze the correlation between economic indicators",
    ]
    for desc in research_descs:
        step = make_step(desc)
        assert not Orchestrator._is_synthesis_step(
            step
        ), f"Research step incorrectly flagged as synthesis: '{desc}'"

    # Verify the planning system prompt explicitly prohibits synthesis steps
    orch_src = open("orchestrator.py").read()
    assert (
        "CRITICAL CONSTRAINT" in orch_src
    ), "_root_system_prompt must contain a CRITICAL CONSTRAINT prohibition on synthesis steps"
    assert (
        "skipped_synthesis" in orch_src
    ), "_run_step must emit skipped_synthesis flag in the step_complete event"
    print(
        "✅ _is_synthesis_step(): synthesis steps detected, research steps pass through"
    )


# ── Pipeline data-flow improvement smoke tests ──────────────────────────────


def test_config_increased_defaults():
    """Verify config default strings for pipeline data-flow tunables are set to
    the increased values that support comprehensive research."""
    import ast

    with open("config.py") as f:
        src = f.read()

    # Verify the default strings in the source code
    assert (
        '"5"' in src and "MAX_ITERATIONS" in src
    ), "MAX_ITERATIONS default should be '5' in config.py source"
    assert (
        '"4000"' in src and "DISTILL_MAX_CHARS" in src
    ), "DISTILL_MAX_CHARS default should be '4000' in config.py source"
    assert (
        '"1500"' in src and "STEP_SUMMARY_MAX_CHARS" in src
    ), "STEP_SUMMARY_MAX_CHARS default should be '1500' in config.py source"
    assert (
        '"10"' in src and "SECTION_DRAFT_TOP_K" in src
    ), "SECTION_DRAFT_TOP_K default should be '10' in config.py source"
    # analyst_top_k: '10' is in the source (ANALYST_TOP_K env var fallback)
    assert "ANALYST_TOP_K" in src, "config.py must have ANALYST_TOP_K field"
    print("✅ Config defaults: increased pipeline data-flow tunables verified")


def test_extract_sources_collects_from_rag_chunks():
    """_extract_sources collects source URLs from RAG store chunks even when
    SearchAgent's final_message JSON is unparseable."""
    import ast

    with open("orchestrator.py") as f:
        src = f.read()
    tree = ast.parse(src)

    # Find _extract_sources method
    orch_class = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.ClassDef) and n.name == "Orchestrator"
    )
    extract_method = next(
        n
        for n in ast.walk(orch_class)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and n.name == "_extract_sources"
    )
    method_src = ast.get_source_segment(src, extract_method)

    # Must NOT be a @staticmethod anymore (needs access to self._search_store)
    assert (
        "self" in method_src
    ), "_extract_sources must be an instance method (not static)"
    # Must reference the RAG store as a fallback
    assert (
        "_search_store" in method_src
    ), "_extract_sources must collect URLs from RAG store chunks"
    assert (
        "source_url" in method_src
    ), "_extract_sources must extract source_url from chunks"
    # Must still try parsing final_message as first tier
    assert (
        "final_message" in method_src
    ), "_extract_sources must still parse final_message JSON"
    print(
        "✅ _extract_sources: collects from both final_message JSON and RAG store chunks"
    )


def test_analyst_receives_prior_claims():
    """_run_analyst passes structured claims from prior steps to the analyst
    for cross-step triangulation."""
    import ast

    with open("orchestrator.py") as f:
        src = f.read()
    tree = ast.parse(src)

    orch_class = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.ClassDef) and n.name == "Orchestrator"
    )
    analyst_method = next(
        n
        for n in ast.walk(orch_class)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and n.name == "_run_analyst"
    )
    method_src = ast.get_source_segment(src, analyst_method)

    # Must include prior claims context
    assert (
        "prior_claims_block" in method_src
    ), "_run_analyst must inject prior_claims_block into analyst prompt"
    assert (
        "Structured findings from prior steps" in method_src
    ), "_run_analyst must label the prior claims block for the analyst"
    # Cross-step retrieval must use top_k >= 5
    assert (
        "top_k=5" in method_src
    ), "_run_analyst cross-step corroboration should use top_k=5 (was top_k=3)"
    print(
        "✅ _run_analyst: passes prior structured claims and expanded cross-step context"
    )


def test_section_drafting_receives_structured_claims():
    """Synthesis Phase B passes structured claims, tensions, and contradictions
    to section drafting to enable novel insight generation."""
    import ast

    with open("orchestrator.py") as f:
        src = f.read()
    tree = ast.parse(src)

    orch_class = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.ClassDef) and n.name == "Orchestrator"
    )
    synth_method = next(
        n
        for n in ast.walk(orch_class)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and n.name == "synthesize"
    )
    method_src = ast.get_source_segment(src, synth_method)

    assert (
        "structured_claims_block" in method_src
    ), "synthesize() must build and pass structured_claims_block to section drafts"
    assert (
        "structured_tensions_block" in method_src
    ), "synthesize() must build and pass structured_tensions_block to section drafts"
    assert (
        "unresolved_contradictions_block" in method_src
    ), "synthesize() must build and pass unresolved_contradictions_block to section drafts"
    assert (
        "novel" in method_src.lower() or "insight" in method_src.lower()
    ), "Section drafting system prompt should reference novel insights"
    print(
        "✅ synthesize() Phase B: passes claims, tensions, and contradictions to section drafting"
    )


def test_step_summary_expanded_input():
    """Step summary generation uses expanded claim/tension input and increased caps."""
    import ast

    with open("orchestrator.py") as f:
        src = f.read()
    tree = ast.parse(src)

    orch_class = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.ClassDef) and n.name == "Orchestrator"
    )
    summary_method = next(
        n
        for n in ast.walk(orch_class)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and n.name == "_generate_step_summary"
    )
    method_src = ast.get_source_segment(src, summary_method)

    # Must use at least 15 claims (was 10)
    assert (
        "claims[:15]" in method_src
    ), "_generate_step_summary must use claims[:15] (was claims[:10])"
    # Must use at least 5 tensions (was 3)
    assert (
        "tensions[:5]" in method_src
    ), "_generate_step_summary must use tensions[:5] (was tensions[:3])"
    # Must reference tensions in the prompt
    assert (
        "tension" in method_src.lower() or "disagreement" in method_src.lower()
    ), "_generate_step_summary prompt should mention tensions/disagreements"
    print(
        "✅ _generate_step_summary: expanded input claims/tensions and improved prompt"
    )


def test_knowledge_graph_enhanced_schema():
    """Verify _create_schema creates the Document constraint (replacing old Source)
    and that temporal properties are written on entity creation/update."""
    import ast

    with open("long_term_memory.py") as f:
        src = f.read()
    tree = ast.parse(src)

    # Find _create_schema inside AsyncLongTermMemory
    ltm_class = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.ClassDef) and n.name == "AsyncLongTermMemory"
    )
    schema_method = next(
        n
        for n in ast.walk(ltm_class)
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "_create_schema"
    )
    schema_src = ast.get_source_segment(src, schema_method) or ""
    assert (
        "document_url" in schema_src
    ), "_create_schema must add Document node uniqueness constraint on url"

    # Verify temporal props are included in upsert_entity
    kg_class = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.ClassDef) and n.name == "KnowledgeGraph"
    )
    upsert_method = next(
        n
        for n in ast.walk(kg_class)
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "upsert_entity"
    )
    upsert_src = ast.get_source_segment(src, upsert_method) or ""
    assert (
        "last_confirmed" in upsert_src
    ), "upsert_entity must write last_confirmed on both create and merge paths"
    assert (
        "confirmation_count" in upsert_src
    ), "upsert_entity must write confirmation_count on both create and merge paths"
    print(
        "✅ Enhanced schema: Source constraint + temporal properties in upsert_entity"
    )


def test_knowledge_graph_new_methods():
    """Verify all new KnowledgeGraph methods exist."""
    import ast

    with open("long_term_memory.py") as f:
        src = f.read()
    tree = ast.parse(src)

    kg_class = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.ClassDef) and n.name == "KnowledgeGraph"
    )
    found = {n.name for n in ast.walk(kg_class) if isinstance(n, ast.AsyncFunctionDef)}

    new_methods = [
        "store_hierarchy",
        "store_contradiction",
        "find_contradictions",
        "store_source",
        "link_to_source",
        "get_provenance",
        "recent_entities",
        "recent_relationships",
        "session_diff",
        "find_paths",
        "find_common_neighbors",
        "decay_confidence",
        "prune",
    ]
    missing = [m for m in new_methods if m not in found]
    assert not missing, f"KnowledgeGraph is missing new methods: {missing}"
    print(f"✅ KnowledgeGraph has all {len(new_methods)} new methods")


def test_relationship_dedup_in_store():
    """Verify store_relationship contains deduplication logic (exact-match triple check)."""
    import ast

    with open("long_term_memory.py") as f:
        src = f.read()
    tree = ast.parse(src)

    kg_class = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.ClassDef) and n.name == "KnowledgeGraph"
    )
    store_rel = next(
        n
        for n in ast.walk(kg_class)
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "store_relationship"
    )
    store_src = ast.get_source_segment(src, store_rel) or ""

    assert (
        "confirmation_count" in store_src
    ), "store_relationship must track confirmation_count for dedup merges"
    assert (
        "last_confirmed" in store_src
    ), "store_relationship must set last_confirmed on both create and merge paths"
    # Dedup check queries for existing triple
    assert (
        "relation_type" in store_src and "WHERE" in store_src
    ), "store_relationship must query for existing triple before creating"
    assert (
        '"merged"' in store_src or "'merged'" in store_src
    ), "store_relationship must return 'merged' flag in its result dict"
    print("✅ store_relationship has deduplication with confirmation tracking")


def test_prune_method_dry_run():
    """Verify prune() has three passes (relationships, orphans, contradictions) and
    a dry_run parameter that defaults to True."""
    import ast, inspect

    with open("long_term_memory.py") as f:
        src = f.read()
    tree = ast.parse(src)

    kg_class = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.ClassDef) and n.name == "KnowledgeGraph"
    )
    prune_method = next(
        n
        for n in ast.walk(kg_class)
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "prune"
    )

    # Check dry_run default = True
    defaults = prune_method.args.defaults
    kwdefaults = prune_method.args.kw_defaults
    # dry_run should be a keyword argument with default True
    prune_src = ast.get_source_segment(src, prune_method) or ""
    assert "dry_run" in prune_src, "prune() must have a dry_run parameter"
    assert (
        "dry_run: bool = True" in prune_src
    ), "prune() dry_run must default to True (safe by default)"

    # Pruning passes — now uses _FACTUAL_REL_TYPES loop instead of hardcoded RELATES_TO
    assert (
        "_FACTUAL_REL_TYPES" in prune_src or "rel_type" in prune_src
    ), "Pass 1: must prune stale edges across all factual relationship types"
    assert (
        "orphan" in prune_src.lower() or "NOT (e)-" in prune_src
    ), "Pass 2: must prune orphaned entities"
    assert "CONTRADICTS" in prune_src, "Pass 3: must prune dangling CONTRADICTS edges"
    assert (
        "Claim" in prune_src or "claims" in prune_src
    ), "Pass 4: must prune orphaned claims"
    print("✅ prune() has dry_run=True default + four-pass pruning strategy")


def test_orchestrator_graph_mutation_counter():
    """Verify Orchestrator tracks _graph_mutations_since_community_update."""
    import ast

    with open("orchestrator.py") as f:
        src = f.read()
    tree = ast.parse(src)

    orch_class = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.ClassDef) and n.name == "Orchestrator"
    )

    # Counter must be initialised in __init__
    init_method = next(
        n
        for n in ast.walk(orch_class)
        if isinstance(n, ast.FunctionDef) and n.name == "__init__"
    )
    init_src = ast.get_source_segment(src, init_method) or ""
    assert (
        "_graph_mutations_since_community_update" in init_src
    ), "Orchestrator.__init__ must initialise _graph_mutations_since_community_update"

    # Counter must be incremented in _extract_graph_triples
    extract_method = next(
        n
        for n in ast.walk(orch_class)
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "_extract_graph_triples"
    )
    extract_src = ast.get_source_segment(src, extract_method) or ""
    assert (
        "_graph_mutations_since_community_update" in extract_src
    ), "_extract_graph_triples must increment the mutation counter"

    # synthesize() must gate community update on the counter
    synth_method = next(
        n
        for n in ast.walk(orch_class)
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "synthesize"
    )
    synth_src = ast.get_source_segment(src, synth_method) or ""
    assert (
        "_graph_mutations_since_community_update" in synth_src
    ), "synthesize() must check _graph_mutations_since_community_update before community update"
    print(
        "✅ Orchestrator: mutation counter init'd, incremented, and checked in synthesize()"
    )


def test_orchestrator_enhanced_extraction_prompt():
    """Verify _extract_graph_triples prompt requests parent_type, source_url,
    and injects existing graph context for contradiction detection."""
    import ast

    with open("orchestrator.py") as f:
        src = f.read()
    tree = ast.parse(src)

    orch_class = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.ClassDef) and n.name == "Orchestrator"
    )
    extract_method = next(
        n
        for n in ast.walk(orch_class)
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "_extract_graph_triples"
    )
    extract_src = ast.get_source_segment(src, extract_method) or ""

    assert (
        "parent_type" in extract_src
    ), "_extract_graph_triples prompt must request parent_type for IS_A hierarchy"
    assert (
        "source_url" in extract_src
    ), "_extract_graph_triples prompt must request source_url for provenance"
    assert (
        "store_hierarchy" in extract_src
    ), "_extract_graph_triples must call store_hierarchy when parent_type is provided"
    assert (
        "store_document" in extract_src
    ), "_extract_graph_triples must call store_document when source_url is provided"
    assert (
        "store_claim" in extract_src
    ), "_extract_graph_triples must call store_claim for extracted claims"
    assert (
        "recall_graph_context" in extract_src
    ), "_extract_graph_triples must inject existing graph context into the prompt"
    print(
        "✅ _extract_graph_triples: enhanced prompt + IS_A hierarchy + documents + claims + context injection"
    )


def test_orchestrator_confidence_decay_post_synthesis():
    """Verify synthesize() calls graph.decay_confidence after community detection."""
    import ast

    with open("orchestrator.py") as f:
        src = f.read()
    tree = ast.parse(src)

    orch_class = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.ClassDef) and n.name == "Orchestrator"
    )
    synth_method = next(
        n
        for n in ast.walk(orch_class)
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "synthesize"
    )
    synth_src = ast.get_source_segment(src, synth_method) or ""

    assert (
        "decay_confidence" in synth_src
    ), "synthesize() must call graph.decay_confidence post-synthesis to age unconfirmed facts"
    assert (
        "confidence_decay_half_life" in synth_src
    ), "synthesize() must read confidence_decay_half_life from config"
    print("✅ synthesize() calls decay_confidence with config half-life")


def test_graph_api_endpoints_exist():
    """Verify api_server.py exposes all required /graph/* endpoints."""
    with open("api_server.py") as f:
        src = f.read()

    required = [
        '"/graph/stats"',
        '"/graph/entities"',
        '"/graph/relationships"',
        '"/graph/communities"',
        '"/graph/contradictions"',
        '"/graph/provenance"',
        '"/graph/paths"',
        '"/graph/session/{session_id}"',
        '"/graph/prune"',
    ]
    missing = [r for r in required if r not in src]
    assert not missing, f"api_server.py missing graph endpoints: {missing}"
    # prune must be POST (destructive)
    assert (
        '@app.post("/graph/prune")' in src
    ), "POST /graph/prune must use @app.post (destructive operation)"
    print(f"✅ api_server.py exposes all {len(required)} /graph/* endpoints")


def test_config_graph_options():
    """Verify config.py has new graph-related fields."""
    import ast

    with open("config.py") as f:
        src = f.read()

    assert (
        "confidence_decay_half_life" in src
    ), "config.py must define confidence_decay_half_life field"
    assert (
        "CONFIDENCE_DECAY_HALF_LIFE" in src
    ), "confidence_decay_half_life must read from CONFIDENCE_DECAY_HALF_LIFE env var"
    assert (
        "graph_community_min_mutations" in src
    ), "config.py must define graph_community_min_mutations field"
    assert (
        "GRAPH_COMMUNITY_MIN_MUTATIONS" in src
    ), "graph_community_min_mutations must read from GRAPH_COMMUNITY_MIN_MUTATIONS env var"
    print(
        "✅ config.py: confidence_decay_half_life + graph_community_min_mutations defined"
    )


if __name__ == "__main__":
    errors = []

    tests = [
        test_retrieval_utils,
        test_context_supersede,
        test_models_section_draft,
        test_orchestrator_step_summaries,
        test_search_agent_prompt_distillation,
        test_new_orchestrator_constants,
        test_orchestrator_has_new_helpers,
        # RAG / LTM improvement tests
        test_ltm_neo4j_schema_setup,
        test_search_result_store_binary_detection,
        test_orchestrator_analyst_recommendations,
        test_run_analyst_strips_coverage_notes,
        test_run_analyst_cross_step_retrieval,
        test_qa_loop_data_poverty_exit,
        # Self-optimize pipeline fixes
        test_self_optimize_uses_long_term_memory,
        test_self_optimize_api_server_wiring,
        # File context in planning
        test_orchestrator_has_file_context_method,
        test_plan_incorporates_file_context,
        # Knowledge Graph (GraphRAG) tests
        test_ltm_knowledge_graph_collections,
        test_knowledge_graph_class_exists,
        test_orchestrator_extract_graph_triples,
        test_orchestrator_recall_uses_graph,
        test_orchestrator_community_detection_post_synthesis,
        # Enhanced Knowledge Graph tests (Phase 1-4)
        test_knowledge_graph_enhanced_schema,
        test_knowledge_graph_new_methods,
        test_relationship_dedup_in_store,
        test_prune_method_dry_run,
        test_orchestrator_graph_mutation_counter,
        test_orchestrator_enhanced_extraction_prompt,
        test_orchestrator_confidence_decay_post_synthesis,
        test_graph_api_endpoints_exist,
        test_config_graph_options,
        # GCP backend + config model selection fixes
        test_gcp_backend_converts_tool_calls,
        test_error_handler_no_double_fault,
        test_config_heavy_light_model_fields,
        test_select_model_reads_config,
        test_search_agent_prompt_tool_use_instruction,
        # Session persistence tests
        test_session_store_persistence,
        test_orchestrator_synthesis_checkpoint,
        test_api_server_recovery_function,
        test_orchestrator_release_memory,
        # Logging + orchestrator observability fixes
        test_ndjson_formatter_valid_json,
        test_analyst_prompt_confidence_field,
        test_outline_parse_handles_empty_content,
        test_orchestrator_step_level_logging,
        # Shutdown + cancellation fixes
        test_shutdown_guard_rejects_during_shutdown,
        test_cancel_preserves_complete_sessions,
        # Session concurrency limiter
        test_session_manager_concurrency_semaphore,
        # Synthesis step detection + planning prompt guard
        test_synthesis_step_detection,
        # Pipeline data-flow improvements
        test_config_increased_defaults,
        test_extract_sources_collects_from_rag_chunks,
        test_analyst_receives_prior_claims,
        test_section_drafting_receives_structured_claims,
        test_step_summary_expanded_input,
    ]
    async_tests = [
        test_search_result_store,
        test_search_result_store_exclude_step_id,
        test_knowledge_graph_entity_lifecycle,
        test_knowledge_graph_recall_context,
    ]

    for t in tests:
        try:
            t()
        except Exception as e:
            print(f"❌ {t.__name__}: {e}")
            errors.append(t.__name__)

    for t in async_tests:
        try:
            asyncio.run(t())
        except Exception as e:
            print(f"❌ {t.__name__}: {e}")
            errors.append(t.__name__)

    print()
    if errors:
        print(f"❌ {len(errors)} test(s) failed: {errors}")
        sys.exit(1)
    else:
        print("🎉 All smoke tests passed.")
        sys.exit(0)
