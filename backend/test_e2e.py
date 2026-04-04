"""Quick end-to-end test for the orchestrator pipeline (no real MCP required).

Will run through the entire plan->execute->synthesize flow using a mocked MCP registry and a real model backend.
The goal is to catch any major issues in the pipeline without needing to set up actual MCP servers or tools.
"""

import asyncio
import sys
import time

sys.path.insert(0, ".")

from unittest.mock import AsyncMock, MagicMock

from config import Config
from model_backend import create_model_backend
from orchestrator import Orchestrator, AgentPool


# Convert seconds to hh:mm:ss format
def format_time(seconds: float) -> str:
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


async def test_pipeline():
    """
    Test the full orchestrator pipeline with a mocked MCP registry and real model backend.

    This will run through planning, execution, and synthesis phases, printing out events as they occur.
    Any exceptions will be caught and printed, along with a traceback, to help identify issues in the pipeline.

    It can also run for a very long time! This should be used as a quick smoke test to catch major issues,
    not as a comprehensive test suite.
    """
    cfg = Config()
    backend = create_model_backend(cfg)

    # Mock MCP registry — no real MCP servers needed
    mock_registry = MagicMock()
    mock_registry.get_all_tools = AsyncMock(return_value=[])
    mock_registry.call_tool = AsyncMock(return_value={"error": "no tool"})
    mock_registry.get = MagicMock(return_value=None)
    mock_registry.call_tool_on_server = AsyncMock(return_value={"result": ""})

    pool = AgentPool.from_single_backend(backend)
    await pool.async_init(mock_registry)

    orch = Orchestrator(config=cfg, mcp_registry=mock_registry, agent_pool=pool)

    print("=== PLANNING ===")
    try:
        async for event in orch.plan("What are the benefits of regular exercise?"):
            print(f"  [{event.type}] {event.message[:100]}")
    except Exception as e:
        print(f"  PLANNING FAILED: {e}")
        import traceback

        traceback.print_exc()
        return

    print("\n=== EXECUTING ===")
    try:
        async for event in orch.execute():
            print(f"  [{event.type}] {event.message[:100]}")
    except Exception as e:
        print(f"  EXECUTION FAILED: {e}")
        import traceback

        traceback.print_exc()
        return

    print("\n=== SYNTHESIZING ===")
    try:
        async for event in orch.synthesize():
            t = event.type
            msg = event.message[:80] if event.message else ""
            data_keys = list(event.data.keys()) if event.data else []
            print(f"  [{t}] {msg} data={data_keys}")
    except Exception as e:
        print(f"  SYNTHESIS FAILED: {e}")
        import traceback

        traceback.print_exc()
        return

    print("\nDONE")


if __name__ == "__main__":
    start = time.time()

    asyncio.run(test_pipeline())

    end = time.time()
    print(f"\nTotal execution time: {format_time(end - start)}")
