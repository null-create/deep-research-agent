import os
import json
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator

from art import tprint
from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from config import Config
from mcp_client import create_mcp_registry
from model_backend import create_model_backend
from optimization import SelfOptimizingAgent
from advanced_features import AdvancedResearchAgent
from long_term_memory import AsyncLongTermMemory
from orchestrator import Orchestrator, AgentPool
from models import ConfigUpdate, ResearchPlan, ResearchStep, ResponseMessage
from session_manager import ResearchSessionManager
from session_store import SessionStore, ResearchSession
from observability import setup_logging, get_logger, log_dump, record_event
from embeddings import warm_up as warm_up_embeddings

# ── Logging ───────────────────────────────────────────────────────────────────
# setup_logging() reads LOG_LEVEL / DEBUG_LOGGING from the environment,
# wires up the rich terminal formatter, the in-process buffer handler, and
# the rotating NDJSON file handler in one call.
setup_logging()
logger = get_logger(__name__)

# ── Config persistence ────────────────────────────────────────────────────────
_SETTINGS_FILE = Path(
    os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "configs",
        "model_settings.json",
    )
)


def _load_persisted_settings() -> dict:
    """Load overrides from the model_settings.json file if it exists."""
    if _SETTINGS_FILE.exists():
        try:
            return json.loads(_SETTINGS_FILE.read_text())
        except Exception as exc:
            logger.warning("Failed to load model_settings.json: %s", exc)
    return {}


def _persist_settings(cfg: "Config") -> None:
    """Persist the model-related config fields to model_settings.json."""
    _SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    fields = {
        "model_backend",
        "openai_api_key",
        "openai_base_url",
        "openai_heavy_model",
        "openai_light_model",
        "azure_api_key",
        "azure_endpoint",
        "azure_heavy_model",
        "azure_light_model",
        "aws_api_key",
        "aws_base_url",
        "aws_heavy_model",
        "aws_light_model",
        "gcp_api_key",
        "gcp_base_url",
        "gcp_heavy_model",
        "gcp_light_model",
        "ollama_base_url",
        "ollama_heavy_model",
        "ollama_light_model",
        "huggingface_api_key",
        "huggingface_base_url",
        "huggingface_model",
        "root_model_override",
        "search_model_override",
        "analyst_model_override",
        "qa_model_override",
        "root_temperature",
        "search_temperature",
        "analyst_temperature",
        "qa_temperature",
        "root_top_p",
        "search_top_p",
        "analyst_top_p",
        "qa_top_p",
        "root_max_tokens",
        "search_max_tokens",
        "analyst_max_tokens",
        "qa_max_tokens",
    }
    data = {k: v for k, v in cfg.model_dump().items() if k in fields and v is not None}
    tmp = _SETTINGS_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(_SETTINGS_FILE)


# ── Lifespan ──────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ───────────────────────────────────────────────────────────────
    logger.info("Starting Research Agent API...")
    app.state.config = Config()
    cfg = app.state.config

    # Overlay any settings previously saved via the UI.
    saved = _load_persisted_settings()
    if saved:
        for key, value in saved.items():
            if hasattr(cfg, key):
                setattr(cfg, key, value)
        logger.info(
            "Loaded %d persisted model settings from %s.", len(saved), _SETTINGS_FILE
        )

    # Initialize the model backend and MCP registry based on the current config.
    model_backend = create_model_backend(cfg)
    mcp_registry = await create_mcp_registry(cfg)
    # Stored so per-session orchestrators can share it.
    app.state.mcp_registry = mcp_registry
    # Initialize in-process long-term memory (Neo4j-backed, replaces the
    # standalone memory MCP server).
    long_term_memory = AsyncLongTermMemory(
        neo4j_uri=cfg.neo4j_uri,
        neo4j_user=cfg.neo4j_user,
        neo4j_password=cfg.neo4j_password,
        neo4j_database=cfg.neo4j_database,
        embedding_dimensions=cfg.neo4j_embedding_dimensions,
    )
    await long_term_memory.async_init()
    app.state.long_term_memory = long_term_memory
    logger.info("Long-term memory store initialized at %s.", cfg.neo4j_uri)
    if cfg.agent_mode == "self-optimization":
        app.state.agent = SelfOptimizingAgent(
            model_backend, mcp_registry, long_term_memory=long_term_memory
        )
        logger.info("Initialized Self-Optimizing Agent in self-optimization mode.")
    else:
        app.state.agent = AdvancedResearchAgent(model_backend, mcp_registry)
        logger.info("Agent is initialized and ready to accept requests.")

    # Always initialise a dedicated SelfOptimizingAgent so the self_optimize
    # WebSocket command is available regardless of the AGENT_MODE setting.
    app.state.self_optimizing_agent = SelfOptimizingAgent(
        model_backend, mcp_registry, long_term_memory=long_term_memory
    )
    logger.info("Self-optimizing agent initialized.")

    # Initialize agent pool and connect to MCP servers to populate tools for
    # sub-agents.
    # NOTE: each sub-agent could use a separate backend in the future if needed.
    # For now, we use the same one for simplicity.
    logger.info("Creating agent pool with single backend: %s", cfg.model_backend)
    app.state.agent_pool = AgentPool.from_single_backend(model_backend)
    await app.state.agent_pool.async_init(mcp_registry)

    # Session store — one entry per active research pipeline.
    app.state.session_store = SessionStore()
    logger.info("Session store initialized.")

    # Centralized task manager — owns all research background tasks.
    app.state.session_manager = ResearchSessionManager(
        max_concurrent_sessions=cfg.max_concurrent_sessions,
    )
    logger.info("Session manager initialized.")

    # Pre-warm the in-process sentence-transformer model so the first research
    # request does not stall waiting for model weights to load.
    await warm_up_embeddings()
    logger.info("Embedding model warm-up complete.")

    # Start background keepalive pings to all MCP servers.  Without this, Docker
    # network NAT entries for idle SSE streams expire after ~5 min and silently
    # drop the connection.  The keepalive runs every 4 min by default
    # (MCP_KEEPALIVE_INTERVAL env var).
    mcp_registry.start_keepalive()

    # Shutdown guard — checked by WebSocket handlers to reject new sessions
    # once the shutdown sequence has started.
    app.state.shutting_down = False

    # Cool banners :)
    tprint("Deep Research Agent", font="slant", chr_ignore=True)
    tprint("Version 1.0.0", font="slant", chr_ignore=True)

    try:
        yield
    except Exception as exc:
        logger.exception("Fatal error in lifespan: %s", exc)

    # ── Shutdown ──────────────────────────────────────────────────────────────
    finally:
        # Shield the entire shutdown sequence from the uvicorn lifespan
        # CancelledError.  Without this, any `await` inside the finally block
        # is immediately interrupted by the cancel scope that uvicorn fires
        # when the server receives SIGTERM/SIGINT, leaving MCP sessions
        # unterminated and producing spurious anyio cancel-scope errors.
        async def _shutdown() -> None:
            logger.info("Shutting down Research Agent API...")

            # Immediately signal that we are shutting down so the WebSocket
            # handler rejects any new sessions arriving during teardown.
            app.state.shutting_down = True

            session_store: SessionStore = app.state.session_store

            # ── Step 1: cancel all active research jobs via session manager ───────
            try:
                session_manager: ResearchSessionManager = app.state.session_manager
                await session_manager.shutdown()
            except Exception as exc:
                logger.warning("[shutdown] Error in session manager shutdown: %s", exc)

            # ── Step 2: remove all sessions from the store ────────────────────────
            try:
                for sid in list(session_store._sessions):
                    session_store.remove(sid)
            except Exception as exc:
                logger.warning("[shutdown] Error clearing session store: %s", exc)

            # ── Step 3: close the agent pool ─────────
            try:
                await app.state.agent_pool.close()
                logger.info("[shutdown] Agent pool closed.")
            except Exception as exc:
                logger.warning("[shutdown] Error closing agent pool: %s", exc)

            # ── Step 4: disconnect all MCP servers ───────────────────────────────
            try:
                await app.state.mcp_registry.close()
                logger.info("[shutdown] MCP registry closed.")
            except Exception as exc:
                logger.warning("[shutdown] Error closing MCP registry: %s", exc)

            # ── Step 5: write full-lifetime debug log dump ────────────────────────
            try:
                dump_path = log_dump(label="shutdown")
                logger.info("[shutdown] Debug log dump written: %s", dump_path)
            except Exception as exc:
                logger.warning("[shutdown] Could not write log dump: %s", exc)

            # ── Step 6: null out all shared state references ───────────────────────
            if app.state.long_term_memory is not None:
                await app.state.long_term_memory.close()
            app.state.agent = None
            app.state.self_optimizing_agent = None
            app.state.orchestrator = None
            app.state.agent_pool = None
            app.state.mcp_registry = None
            app.state.long_term_memory = None
            app.state.session_store = None
            app.state.session_manager = None
            logger.info("Shutdown complete!")

        try:
            await asyncio.shield(_shutdown())
        except asyncio.CancelledError:
            # The shield was itself cancelled (SIGKILL / force quit).
            # _shutdown() continues running detached; nothing more we can do.
            pass


# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Helpers ───────────────────────────────────────────────────────────────────
def make_serializable(obj: Any) -> list[Any] | dict[Any, Any] | Any:
    """
    Recursively convert any non-JSON-serializable research objects into plain
    dicts so the entire event can be passed to send_json() without risk of a
    TypeError.
    """
    if isinstance(obj, (ResearchStep, ResearchPlan)):
        return make_serializable(obj.to_dict())
    if isinstance(obj, dict):
        return {key: make_serializable(value) for key, value in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [make_serializable(item) for item in obj]
    return obj


# ── Dependencies ──────────────────────────────────────────────────────────────
def get_agent(
    request: Request,
) -> AdvancedResearchAgent | SelfOptimizingAgent:
    agent = request.app.state.agent
    if agent is None:
        raise HTTPException(status_code=503, detail="Agent not initialized")
    return agent


def _make_orchestrator(app_state, research_depth: str = "shallow") -> Orchestrator:
    """Create a fresh Orchestrator for a new research session.

    Each WebSocket session gets its own Orchestrator so concurrent connections
    do not share mutable plan/execution state.  The expensive shared resources
    (AgentPool, MCP registry, long-term memory store) are reused from app state.
    """
    return Orchestrator(
        config=app_state.config,
        mcp_registry=app_state.mcp_registry,
        agent_pool=app_state.agent_pool,
        long_term_memory=app_state.long_term_memory,
        research_depth=research_depth,
    )


async def _recover_session_from_checkpoint(
    app_state,
    session_manager: ResearchSessionManager,
    session_store: SessionStore,
    checkpoint_data: dict,
) -> ResearchSession:
    """Reconstruct an in-memory session from a disk checkpoint.

    If execute() completed (synthesis_checkpoint present) but no ``report``
    event was emitted, re-runs synthesize() in a background task so the client
    receives the report on reconnect.
    """
    session_id = checkpoint_data["session_id"]
    saved_state = checkpoint_data.get("state", "unknown")
    replay_log: list = checkpoint_data.get("replay_log", [])
    syn_checkpoint: dict | None = checkpoint_data.get("synthesis_checkpoint")

    # Check whether the report was already delivered in a previous run.
    report_delivered = any(evt.get("type") == "report" for evt in replay_log)

    orchestrator = _make_orchestrator(app_state, research_depth="deep")
    orchestrator.session_id = session_id

    session = session_store.create(orchestrator, session_id=session_id)
    session.replay_log = list(replay_log)
    session.state = saved_state

    needs_synthesis = (
        syn_checkpoint is not None
        and not report_delivered
        and saved_state not in ("error", "cancelled")
    )

    if not needs_synthesis:
        # Replay-only: mark complete so drain can exit after flushing.
        session.state = saved_state
        session.complete = True
        session.finish()
        logger.info(
            "[ws][%s] Recovered from disk (state=%s, events=%d) — replay only.",
            session_id,
            saved_state,
            len(session.replay_log),
        )
        return session

    # Re-run synthesis using the restored orchestrator state.
    orchestrator.restore_synthesis_state(syn_checkpoint)
    logger.info(
        "[ws][%s] Recovered from disk (state=%s, events=%d) — re-running synthesis.",
        session_id,
        saved_state,
        len(session.replay_log),
    )

    async def _rerun_synthesis(s: ResearchSession = session) -> None:
        try:
            async for event in s.orchestrator.synthesize():
                serialized = make_serializable(event.model_dump())
                s.emit(serialized)
            s.state = "complete"
        except asyncio.CancelledError:
            s.state = "cancelled"
            s.emit({"type": "error", "message": "Synthesis was cancelled."})
        except Exception as exc:
            logger.exception("[ws][%s] Synthesis recovery error: %s", s.session_id, exc)
            s.state = "error"
            s.emit({"type": "error", "message": str(exc)})
        finally:
            s.complete = True
            s.new_event.set()
            s.finish()
            try:
                session_store.persist(s)
            except Exception:
                pass

    job = await session_manager.start_job(
        session_id=session_id,
        coro=_rerun_synthesis(),
    )
    session.background_task = job.task
    return session


async def _drain_session_to_ws(
    session: ResearchSession,
    websocket: WebSocket,
    start_from: int = 0,
) -> None:
    """Stream ``session.replay_log[start_from:]`` to *websocket*.

    Blocks until the session is marked complete and all events have been sent.
    If the WebSocket closes the exception propagates to the caller (the
    background execution task is unaffected and keeps running).

    A 60-second timeout is applied to each ``new_event.wait()`` call so the
    loop can never hang forever if ``finish()`` fails to signal the event.

    Parameters
    ----------
    session:
        The ``ResearchSession`` whose events should be forwarded.
    websocket:
        The live WebSocket connection to write to.
    start_from:
        Index into ``session.replay_log`` to begin sending from.  Pass 0 to
        replay the full log (reconnect case); pass
        ``len(session.replay_log)`` to receive only new events (first
        connection after approve_plan).
    """
    cursor = start_from
    while True:
        # ── Drain all events that have accumulated since last iteration ───────
        while cursor < len(session.replay_log):
            await websocket.send_json(session.replay_log[cursor])
            cursor += 1

        # ── Exit if the session is already done ───────────────────────────────
        if session.complete or session.state == "cancelled":
            break

        # ── Clear the event flag BEFORE re-checking the log to close the   ───
        # ── lost-wakeup race: if the background task emits an event between ───
        # ── the drain-while above and this clear(), the re-drain below      ───
        # ── will catch it; if it emits after the clear() the wait() below   ───
        # ── will wake immediately.                                           ───
        session.new_event.clear()

        # Re-drain in case events arrived in the window between the
        # drain-while and the clear() above.
        while cursor < len(session.replay_log):
            await websocket.send_json(session.replay_log[cursor])
            cursor += 1

        if session.complete or session.state == "cancelled":
            break

        # Wait for the background task to signal a new event (or completion).
        # The timeout prevents an infinite hang if finish() never fires.
        try:
            await asyncio.wait_for(session.new_event.wait(), timeout=60.0)
        except asyncio.TimeoutError:
            logger.debug(
                "[drain][%s] Timed out waiting for new_event — re-checking session state.",
                session.session_id,
            )
            # Loop around: if the session is complete we'll exit above;
            # if not we'll wait again.
            continue
        except WebSocketDisconnect:
            logger.info(
                "[drain][%s] WebSocket disconnected — background task continues.",
                session.session_id,
            )
            break


def get_app_config(request: Request) -> Config:
    return request.app.state.config


# ── REST endpoints ─────────────────────────────────────────────────────────────
@app.get("/")
async def root():
    return {"name": "Research Agent API", "version": "1.0.0", "status": "running"}


@app.get("/health")
async def health_check(request: Request):
    return {
        "status": "healthy",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "agent_initialized": request.app.state.agent is not None,
    }


# ── Agent document endpoints ──────────────────────────────────────────────────
@app.get("/agent/research-methods")
async def get_research_methods():
    """Return the contents of the RESEARCH-METHODS.md instruction document."""
    methods_path = os.path.join(
        os.path.dirname(__file__), "instructions", "RESEARCH-METHODS.md"
    )
    try:
        with open(methods_path, "r", encoding="utf-8") as f:
            content = f.read()
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="RESEARCH-METHODS.md not found.")
    return {"content": content}


# ── Docs endpoints ────────────────────────────────────────────────────────────
# NOTE: /docs is reserved by FastAPI (Swagger UI). Route is /project-docs.
_DOCS_DIR = os.path.join(os.path.dirname(__file__), "docs")


@app.get("/project-docs")
async def list_docs():
    """Return a list of available documentation filenames from the docs/ directory."""
    try:
        filenames = sorted(f for f in os.listdir(_DOCS_DIR) if f.endswith(".md"))
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Docs directory not found.")
    return {"docs": filenames}


@app.get("/project-docs/{filename}")
async def get_doc(filename: str):
    """Return the content of a single documentation file by filename."""
    # Sanitize: only allow simple .md filenames, no path traversal
    safe_name = os.path.basename(filename)
    if not safe_name.endswith(".md") or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="Invalid filename.")
    doc_path = os.path.join(_DOCS_DIR, safe_name)
    try:
        with open(doc_path, "r", encoding="utf-8") as f:
            content = f.read()
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Doc '{safe_name}' not found.")
    return {"filename": safe_name, "content": content}


# ── MCP helper endpoints ───────────────────────────────────────────────────────
@app.get("/mcp/servers")
async def list_mcp_servers(request: Request):
    registry = request.app.state.mcp_registry
    return {"servers": registry.get_all_server_info()}


@app.post("/mcp/servers")
async def add_mcp_server(request: Request, body: dict):
    """Register a new user-defined MCP server (stdio, sse, or streamable-http)."""
    name = body.get("name", "").strip()
    transport = body.get("transport", "streamable-http")
    if not name:
        raise HTTPException(status_code=400, detail="Server name is required.")
    if transport not in ("stdio", "sse", "streamable-http"):
        raise HTTPException(
            status_code=400, detail=f"Unsupported transport '{transport}'."
        )

    registry = request.app.state.mcp_registry
    if name in registry.server_configs and registry.server_configs[name].get("builtin"):
        raise HTTPException(
            status_code=409,
            detail=f"'{name}' is a built-in server and cannot be replaced.",
        )

    # Unregister if already present (allow re-registration / URL update).
    if name in registry.server_configs:
        await registry.unregister(name)

    try:
        await registry.register(
            name,
            url=body.get("url"),
            transport=transport,
            command=body.get("command"),
            args=body.get("args"),
            env=body.get("env"),
            headers=body.get("headers"),
            builtin=False,
            max_retries=3,
        )
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to register server: {str(e)}"
        )

    registry._save_user_servers()
    return {"server": registry.server_configs.get(name, {})}


@app.delete("/mcp/servers/{name}")
async def delete_mcp_server(name: str, request: Request):
    """Remove a user-defined MCP server."""
    registry = request.app.state.mcp_registry
    cfg = registry.server_configs.get(name)
    if cfg is None:
        raise HTTPException(status_code=404, detail=f"Server '{name}' not found.")
    if cfg.get("builtin"):
        raise HTTPException(
            status_code=403, detail=f"Built-in server '{name}' cannot be deleted."
        )
    await registry.unregister(name)
    registry._save_user_servers()
    return {"status": "deleted", "name": name}


@app.get("/mcp/servers/{name}")
async def get_mcp_server_detail(name: str, request: Request):
    """Return config + live capability listings for a single registered server."""
    registry = request.app.state.mcp_registry
    cfg = registry.server_configs.get(name)
    if cfg is None:
        raise HTTPException(status_code=404, detail=f"Server '{name}' not found.")

    client = registry.servers.get(name)
    if client is None or client.session is None:
        # Server is registered but not connected — return config only.
        return {
            "server": {**cfg, "status": "disconnected"},
            "tools": [],
            "resources": [],
            "resource_templates": [],
            "prompts": [],
        }

    tools, resources, resource_templates, prompts = await asyncio.gather(
        client.list_tool_specs(),
        client.list_resources(),
        client.list_resource_templates(),
        client.list_prompts(),
        return_exceptions=True,
    )

    def _safe(result: Any, default: list) -> list:
        return result if isinstance(result, list) else default

    return {
        "server": {**cfg, "status": "connected"},
        "tools": _safe(tools, []),
        "resources": _safe(resources, []),
        "resource_templates": _safe(resource_templates, []),
        "prompts": _safe(prompts, []),
    }


@app.get("/mcp/tools")
async def list_tools(
    agent: AdvancedResearchAgent | SelfOptimizingAgent = Depends(get_agent),
):
    tools = await agent.mcp_servers.get_all_tools()
    return {"tools": tools}


# ── Config endpoints ───────────────────────────────────────────────────────────
@app.get("/config")
async def get_config(cfg: Config = Depends(get_app_config)):
    """Return the current agent configuration."""
    return {"config": cfg.model_dump()}


@app.post("/config")
async def update_config(request: Request, config_update: ConfigUpdate):
    """
    Update model configuration, persist to disk, and reinitialize the
    model backend + agent pool so changes take effect immediately for new
    research sessions (in-flight sessions are not affected).
    """
    try:
        cfg: Config = request.app.state.config

        # ── Apply backend switch ───────────────────────────────────────────
        if config_update.model_backend is not None:
            cfg.model_backend = config_update.model_backend

        backend = cfg.model_backend.lower()

        # ── Apply API credentials ─────────────────────────────────────────
        if config_update.api_key is not None:
            if backend == "openai":
                cfg.openai_api_key = config_update.api_key
            elif backend == "azure":
                cfg.azure_api_key = config_update.api_key
            elif backend == "aws":
                cfg.aws_api_key = config_update.api_key
            elif backend == "gcp":
                cfg.gcp_api_key = config_update.api_key
            elif backend == "huggingface":
                cfg.huggingface_api_key = config_update.api_key
            elif backend == "anthropic":
                cfg.anthropic_api_key = config_update.api_key

        if config_update.api_base_url is not None:
            if backend == "openai":
                cfg.openai_base_url = config_update.api_base_url
            elif backend == "azure":
                cfg.azure_endpoint = config_update.api_base_url
            elif backend == "aws":
                cfg.aws_base_url = config_update.api_base_url
            elif backend == "gcp":
                cfg.gcp_base_url = config_update.api_base_url
            elif backend == "ollama":
                cfg.ollama_base_url = config_update.api_base_url
            elif backend == "huggingface":
                cfg.huggingface_base_url = config_update.api_base_url
            elif backend == "anthropic":
                cfg.anthropic_base_url = config_update.api_base_url or None
            elif backend == "bedrock":
                cfg.aws_region = (
                    config_update.api_base_url
                )  # UI sends region in this field

        # ── Apply heavy/light model names ─────────────────────────────────
        if config_update.heavy_model is not None:
            if backend == "openai":
                cfg.openai_heavy_model = config_update.heavy_model
            elif backend == "azure":
                cfg.azure_heavy_model = config_update.heavy_model
            elif backend == "aws":
                cfg.aws_heavy_model = config_update.heavy_model
            elif backend == "gcp":
                cfg.gcp_heavy_model = config_update.heavy_model
            elif backend == "ollama":
                cfg.ollama_heavy_model = config_update.heavy_model
            elif backend == "anthropic":
                cfg.anthropic_heavy_model = config_update.heavy_model
            elif backend == "bedrock":
                cfg.aws_heavy_model = (
                    config_update.heavy_model
                )  # bedrock shares aws_* model fields

        if config_update.light_model is not None:
            if backend == "openai":
                cfg.openai_light_model = config_update.light_model
            elif backend == "azure":
                cfg.azure_light_model = config_update.light_model
            elif backend == "aws":
                cfg.aws_light_model = config_update.light_model
            elif backend == "gcp":
                cfg.gcp_light_model = config_update.light_model
            elif backend == "ollama":
                cfg.ollama_light_model = config_update.light_model
            elif backend == "anthropic":
                cfg.anthropic_light_model = config_update.light_model
            elif backend == "bedrock":
                cfg.aws_light_model = (
                    config_update.light_model
                )  # bedrock shares aws_* model fields

        # ── Apply per-agent model overrides ───────────────────────────────
        for attr in (
            "root_model_override",
            "search_model_override",
            "analyst_model_override",
            "qa_model_override",
        ):
            val = getattr(config_update, attr)
            if val is not None:
                setattr(cfg, attr, val or None)  # empty string → clear override

        # ── Apply per-agent sampling params ───────────────────────────────
        for attr in (
            "root_temperature",
            "search_temperature",
            "analyst_temperature",
            "qa_temperature",
            "root_top_p",
            "search_top_p",
            "analyst_top_p",
            "qa_top_p",
            "root_max_tokens",
            "search_max_tokens",
            "analyst_max_tokens",
            "qa_max_tokens",
        ):
            val = getattr(config_update, attr)
            if val is not None:
                setattr(cfg, attr, val)

        # Legacy single model field
        if config_update.model is not None:
            if backend == "openai":
                cfg.openai_model = config_update.model
            elif backend == "azure":
                pass  # azure uses deployment_name, not a free-form model
            elif backend == "aws":
                cfg.aws_model = config_update.model
            elif backend == "gcp":
                cfg.gcp_model = config_update.model
            elif backend == "ollama":
                cfg.ollama_model = config_update.model
            elif backend == "huggingface":
                cfg.huggingface_model = config_update.model
            elif backend == "anthropic":
                cfg.anthropic_model = config_update.model
            elif backend == "bedrock":
                cfg.aws_model = config_update.model  # bedrock shares aws_* model fields

        # ── Persist to disk ───────────────────────────────────────────────
        _persist_settings(cfg)

        # ── Reinitialize model backend + agent pool ───────────────────────
        # This makes the new settings take effect for all new research sessions
        # without requiring a process restart.
        new_backend = create_model_backend(cfg)
        new_agent_pool = AgentPool.from_single_backend(new_backend)
        await new_agent_pool.async_init(request.app.state.mcp_registry)
        request.app.state.model_backend = new_backend
        request.app.state.agent_pool = new_agent_pool
        # Also update the /chat AdvancedResearchAgent
        request.app.state.agent = AdvancedResearchAgent(
            new_backend, request.app.state.mcp_registry
        )
        request.app.state.config = cfg

        logger.info(
            "Model config updated — backend=%s, settings persisted to %s",
            backend,
            _SETTINGS_FILE,
        )
        return {"status": "success", "config": cfg.model_dump()}

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error updating config: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ── Graph inspection endpoints ────────────────────────────────────────────────


def _get_ltm(request: Request):
    """Return the live long-term memory instance or raise 503."""
    ltm = getattr(request.app.state, "long_term_memory", None)
    if ltm is None or not ltm.graph.available:
        raise HTTPException(status_code=503, detail="Knowledge graph not available")
    return ltm


@app.get("/graph/stats")
async def graph_stats(request: Request):
    """Return entity/relationship/community/contradiction counts."""
    ltm = _get_ltm(request)
    return {"stats": await ltm.graph.stats()}


@app.get("/graph/entities")
async def graph_entities(
    request: Request,
    query: str = "",
    limit: int = 10,
    include_hierarchy: bool = False,
    node_type: str = "",
):
    """Semantic entity search.  Returns up to *limit* entities closest to *query*.

    Optionally filter by *node_type* (person, organization, technology, concept,
    event, location, metric).  Comma-separated values accepted.
    """
    if limit < 1 or limit > 100:
        raise HTTPException(status_code=422, detail="limit must be between 1 and 100")
    ltm = _get_ltm(request)
    node_types = (
        [t.strip() for t in node_type.split(",") if t.strip()] if node_type else None
    )
    if query:
        entities = await ltm.graph.find_entities(
            query,
            limit=limit,
            include_hierarchy=include_hierarchy,
            node_types=node_types,
        )
    else:
        entities = await ltm.graph.recent_entities(limit=limit)
    return {"entities": entities}


@app.get("/graph/relationships")
async def graph_relationships(
    request: Request,
    entity: str = "",
    max_hops: int = 2,
    min_confidence: float = 0.0,
):
    """Return relationships reachable from *entity* within *max_hops* hops."""
    if max_hops < 1 or max_hops > 4:
        raise HTTPException(status_code=422, detail="max_hops must be between 1 and 4")
    ltm = _get_ltm(request)
    relationships = await ltm.graph.get_relationships(
        entity_names=[entity] if entity else None, max_hops=max_hops
    )
    if min_confidence > 0.0:
        relationships = [
            r for r in relationships if r.get("confidence", 1.0) >= min_confidence
        ]
    return {"relationships": relationships}


@app.get("/graph/communities")
async def graph_communities(request: Request):
    """List all community summaries."""
    ltm = _get_ltm(request)
    communities = await ltm.graph.get_communities()
    return {"communities": communities}


@app.get("/graph/contradictions")
async def graph_contradictions(
    request: Request,
    entity: str = "",
    limit: int = 20,
):
    """Return contradiction edges, optionally filtered to a named entity."""
    if limit < 1 or limit > 100:
        raise HTTPException(status_code=422, detail="limit must be 1-100")
    ltm = _get_ltm(request)
    contradictions = await ltm.graph.find_contradictions(
        entity_names=[entity] if entity else None, limit=limit
    )
    return {"contradictions": contradictions}


@app.get("/graph/provenance")
async def graph_provenance(
    request: Request,
    entity: str = "",
):
    """Return source provenance chain for the named entity (or all entities)."""
    ltm = _get_ltm(request)
    provenance = await ltm.graph.get_provenance(
        entity_names=[entity] if entity else None
    )
    return {"provenance": provenance}


@app.get("/graph/paths")
async def graph_paths(
    request: Request,
    source: str,
    target: str,
    max_depth: int = 4,
):
    """Find shortest paths between two named entities."""
    if not source or not target:
        raise HTTPException(
            status_code=422, detail="Both 'source' and 'target' are required"
        )
    ltm = _get_ltm(request)
    paths = await ltm.graph.find_paths(source, target, max_depth=max_depth)
    return {"paths": paths}


@app.get("/graph/session/{session_id}")
async def graph_session_diff(request: Request, session_id: str):
    """Return entities and relationships created during a specific research session."""
    ltm = _get_ltm(request)
    diff = await ltm.graph.session_diff(session_id)
    return {"session_id": session_id, **diff}


@app.post("/graph/prune")
async def graph_prune(
    request: Request,
    min_confidence: float = 0.1,
    max_age_days: int = 180,
    dry_run: bool = True,
):
    """
    Prune stale graph elements.

    By default runs in dry-run mode and returns counts of what *would* be
    deleted.  Set ``dry_run=false`` to execute the deletes.

    Query parameters
    ----------------
    min_confidence : float, default 0.1
        Remove RELATES_TO edges whose confidence has fallen below this value.
    max_age_days : int, default 180
        Remove RELATES_TO edges not confirmed in more than this many days.
    dry_run : bool, default true
        When true, only count — do not delete.
    """
    ltm = _get_ltm(request)
    result = await ltm.graph.prune(
        min_confidence=min_confidence,
        max_age_days=max_age_days,
        dry_run=dry_run,
    )
    return {
        "dry_run": dry_run,
        "pruned": result,
        "message": (
            f"Would delete: {result['relationships']} relationships, "
            f"{result['entities']} orphaned entities, "
            f"{result['contradictions']} dangling contradictions, "
            f"{result['claims']} orphaned claims."
            if dry_run
            else f"Deleted: {result['relationships']} relationships, "
            f"{result['entities']} orphaned entities, "
            f"{result['contradictions']} dangling contradictions, "
            f"{result['claims']} orphaned claims."
        ),
    }


@app.get("/graph/claims")
async def graph_claims(
    request: Request,
    query: str = "",
    entity: str = "",
    status: str = "",
    limit: int = 20,
):
    """Search claims by semantic query, entity name, or status."""
    if limit < 1 or limit > 100:
        raise HTTPException(status_code=422, detail="limit must be 1-100")
    ltm = _get_ltm(request)
    claims = await ltm.graph.find_claims(
        query=query,
        limit=limit,
        entity_name=entity or None,
        status=status or None,
    )
    return {"claims": claims}


@app.patch("/graph/claims/{claim_id}")
async def graph_update_claim(
    request: Request,
    claim_id: str,
    status: str = "",
):
    """Update a claim's status (supported, disputed, unverified, retracted)."""
    if not status:
        raise HTTPException(status_code=422, detail="'status' query parameter required")
    ltm = _get_ltm(request)
    result = await ltm.graph.update_claim_status(claim_id, status)
    if not result.get("success"):
        raise HTTPException(
            status_code=404, detail=result.get("message", "Claim not found")
        )
    return {"success": True, "claim_id": claim_id, "status": status}


@app.get("/graph/documents")
async def graph_documents(
    request: Request,
    query: str = "",
    doc_type: str = "",
    min_credibility: float = 0.0,
    limit: int = 20,
):
    """Search documents by semantic query, optionally filtered by type or credibility."""
    if limit < 1 or limit > 100:
        raise HTTPException(status_code=422, detail="limit must be 1-100")
    ltm = _get_ltm(request)
    documents = await ltm.graph.find_documents(
        query=query,
        limit=limit,
        doc_type=doc_type or None,
        min_credibility=min_credibility if min_credibility > 0.0 else None,
    )
    return {"documents": documents}


# ── Chat endpoint ──────────────────────────────────────────────────────────────
@app.post("/chat")
async def chat_endpoint(
    message: dict[str, str],
    agent: AdvancedResearchAgent | SelfOptimizingAgent = Depends(get_agent),
):
    """
    Streaming chat endpoint for testing the agent's response generation without
    going through the full research flow.

    Expects a JSON body of the form::

        {"role": "user", "content": "What are the latest advancements in renewable energy?"}

    Streams the agent's response back as plain-text chunks.
    """

    async def stream_response() -> AsyncIterator[str]:
        try:
            user_message = message.get("content", "").strip()
            if not user_message:
                yield "Error: 'content' field is required in the request body."
                return
            async for text_chunk in agent.chat(user_message):
                yield text_chunk
        except Exception as e:
            logger.exception("Chat endpoint error: %s", e)
            yield f"Error: {str(e)}"

    return StreamingResponse(stream_response(), media_type="text/plain")


# ── WebSocket research endpoint (Orchestrator) ─────────────────────────────────
@app.websocket("/ws/research")
async def research_websocket(websocket: WebSocket):
    """
    WebSocket endpoint powered by the multi-agent ``Orchestrator``.

    Each new ``query`` message creates a fresh, isolated ``ResearchSession``
    with its own ``Orchestrator`` instance.  The ``session_id`` is returned to
    the client immediately so it can persist it for reconnection even if plan
    generation is slow.

    If the connection drops during execution the background task keeps running.
    A reconnecting client sends ``resume`` with the original ``session_id`` to
    receive the full replay log followed by any live events still being produced.

    Inbound message types
    ---------------------
    query        – start a new research session; triggers plan generation.
    approve_plan – approve the generated plan and begin execution + synthesis
                   as a persistent background task.
    modify_plan  – request changes to the current plan (requires planId +
                   feedback fields).
    deny_plan    – reject the plan entirely (requires planId).
    resume       – reconnect to an existing session (requires session_id);
                   replays the full event log then streams live events.

    Outbound message types
    ----------------------
    session_created   – sent immediately after query; carries session_id.
    status            – progress / informational update.
    plan              – research plan ready for approval (plan field populated).
    step_start        – a sub-agent step has begun       (data.step populated).
    step_complete     – a sub-agent step finished        (data.step populated).
    research_complete – all steps executed; synthesis starting.
    report            – final Markdown document          (data.document populated).
    plan_denied       – acknowledgement of denial.
    session_resumed   – sent after a successful resume; carries session_id.
    error             – recoverable or unrecoverable error.

    Additional outbound fields
    --------------------------
    data.contradictions – list of Contradiction objects flagged during a step.
    data.unresolved     – contradictions still open after QA retries.
    """
    app_state = websocket.app.state
    session_store: SessionStore = app_state.session_store

    if not app_state.agent_pool or getattr(app_state, "shutting_down", False):
        await websocket.accept()
        await websocket.send_json(
            {"type": "error", "message": "Server not fully initialised."}
        )
        await websocket.close()
        return

    await websocket.accept()
    logger.info("[ws] WebSocket client connected.")

    # The session currently owned by this connection (may change on resume).
    current_session: ResearchSession | None = None
    # Drain task for the current session (runs concurrently with receive loop).
    drain_task: asyncio.Task | None = None

    async def _cancel_drain() -> None:
        """Cancel and await the active drain task, if any."""
        nonlocal drain_task
        if drain_task and not drain_task.done():
            drain_task.cancel()
            try:
                await drain_task
            except (asyncio.CancelledError, Exception):
                pass
        drain_task = None

    try:
        while True:
            # ── Concurrently wait for either a new WS message or drain finish ─
            recv_task = asyncio.create_task(websocket.receive_json(), name="ws-recv")

            # If a drain is active, race it against the next incoming message.
            if drain_task and not drain_task.done():
                done, _ = await asyncio.wait(
                    {recv_task, drain_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if drain_task in done and recv_task not in done:
                    # Drain finished (session complete or WS error from drain).
                    # Propagate any exception from the drain task.
                    exc = drain_task.exception() if not drain_task.cancelled() else None
                    drain_task = None
                    if exc:
                        raise exc
                    # Drain ended cleanly — wait for the next message normally.
                    raw = await recv_task
                else:
                    # Incoming message arrived (drain may still be running).
                    if recv_task in done:
                        raw = recv_task.result()
                    else:
                        recv_task.cancel()
                        try:
                            await recv_task
                        except (asyncio.CancelledError, Exception):
                            pass
                        continue
            else:
                # No active drain — block on the next message directly.
                try:
                    raw = await recv_task
                except Exception:
                    recv_task.cancel()
                    raise

            message_type = raw.get("type")
            logger.info("[ws] Received message type: %s", message_type)

            # ── query → create session + generate plan ────────────────────────
            if message_type == "query":
                # Cancel any existing drain before starting a new session.
                await _cancel_drain()

                query = raw.get("content", "").strip()
                if not query:
                    await websocket.send_json(
                        {"type": "error", "message": "Query content is required."}
                    )
                    continue

                # Prune stale sessions opportunistically on each new query.
                session_store.prune_expired()
                research_depth = raw.get("research_depth", "shallow")
                if research_depth not in ("shallow", "moderate", "deep"):
                    research_depth = "shallow"
                orchestrator = _make_orchestrator(
                    app_state, research_depth=research_depth
                )
                session = session_store.create(orchestrator)
                orchestrator.session_id = session.session_id
                current_session = session
                session.state = "planning"

                record_event(
                    "session_created",
                    session_id=session.session_id,
                    query=query,
                )

                # Inform the client of its session_id *before* streaming events
                # so it can persist it for reconnection even if the plan is slow.
                await websocket.send_json(
                    {"type": "session_created", "session_id": session.session_id}
                )
                logger.info(
                    "[ws] Session %s — generating plan for: %s",
                    session.session_id,
                    query,
                )

                async for event in orchestrator.plan(query):
                    serialized = make_serializable(event.model_dump())
                    session.emit(serialized)
                    await websocket.send_json(serialized)

                session.state = "awaiting_approval"

            # ── resume → replay log + drain live events ───────────────────────
            elif message_type == "resume":
                await _cancel_drain()

                session_id = raw.get("session_id", "").strip()
                if not session_id:
                    await websocket.send_json(
                        {
                            "type": "error",
                            "message": "session_id is required to resume.",
                        }
                    )
                    continue

                session = session_store.get(session_id)
                if session is None:
                    # Attempt to recover from a disk checkpoint before giving up.
                    checkpoint_data = session_store.load_checkpoint(session_id)
                    if checkpoint_data is None:
                        await websocket.send_json(
                            {
                                "type": "error",
                                "message": (
                                    f"Session '{session_id}' not found or has expired. "
                                    "Please start a new research query."
                                ),
                            }
                        )
                        continue
                    logger.info(
                        "[ws] Recovering session %s from disk checkpoint (state=%s).",
                        session_id,
                        checkpoint_data.get("state"),
                    )
                    session = await _recover_session_from_checkpoint(
                        app_state,
                        app_state.session_manager,
                        session_store,
                        checkpoint_data,
                    )

                current_session = session
                logger.info(
                    "[ws] Client resuming session %s (state=%s, events=%d, complete=%s)",
                    session_id,
                    session.state,
                    len(session.replay_log),
                    session.complete,
                )

                # Acknowledge the resume before sending the replay so the
                # frontend can update its UI (e.g. show a "reconnected" banner).
                await websocket.send_json(
                    {
                        "type": "session_resumed",
                        "session_id": session_id,
                        "state": session.state,
                        "complete": session.complete,
                        "event_count": len(session.replay_log),
                    }
                )

                # Replay the full event history then drain any live events — run
                # as a concurrent task so the receive loop stays responsive.
                drain_task = asyncio.create_task(
                    _drain_session_to_ws(session, websocket, start_from=0),
                    name=f"drain-{session_id}",
                )

            # ── approve_plan → background execute + synthesize ────────────────
            elif message_type == "approve_plan":
                plan_id = raw.get("planId")
                if not plan_id:
                    await websocket.send_json(
                        {"type": "error", "message": "planId is required."}
                    )
                    continue

                if current_session is None:
                    await websocket.send_json(
                        make_serializable(
                            ResponseMessage(
                                type="error",
                                message="No active session. Please submit a query first.",
                            ).model_dump()
                        )
                    )
                    continue

                session = current_session

                # ── Guard: reject duplicate approvals ─────────────────────────
                if session.state != "awaiting_approval":
                    await websocket.send_json(
                        {
                            "type": "error",
                            "message": (
                                f"Session is in state '{session.state}' and cannot be approved. "
                                "Only sessions in 'awaiting_approval' state can be approved."
                            ),
                        }
                    )
                    continue

                existing_task: asyncio.Task | None = getattr(
                    session, "background_task", None
                )
                if existing_task is not None and not existing_task.done():
                    await websocket.send_json(
                        {
                            "type": "error",
                            "message": "Plan execution is already in progress for this session.",
                        }
                    )
                    continue

                orchestrator = session.orchestrator

                if orchestrator._pending_plan is None:
                    await websocket.send_json(
                        make_serializable(
                            ResponseMessage(
                                type="error",
                                message="No pending plan to approve. Please submit a query first.",
                            ).model_dump()
                        )
                    )
                    continue

                session.state = "executing"

                record_event(
                    "plan_approved",
                    session_id=session.session_id,
                    plan_id=plan_id,
                )

                # Capture cursor *before* the background task starts so the
                # drain below only forwards events produced after approval.
                drain_cursor = len(session.replay_log)

                # ── Background task ───────────────────────────────────────────
                # Snapshot `session` via the default-argument trick to avoid
                # any late-binding closure issues.
                async def _run_session(s: ResearchSession = session) -> None:
                    _synthesis_checkpoint: dict | None = None
                    try:
                        async for event in s.orchestrator.execute():
                            serialized = make_serializable(event.model_dump())
                            logger.debug(
                                "[ws][%s] execution event: %s",
                                s.session_id,
                                serialized.get("type", "unknown"),
                            )
                            s.emit(serialized)

                        # Persist synthesis state immediately after execute()
                        # completes so the report can be recovered if the server
                        # restarts during the subsequent synthesis phase.
                        try:
                            _synthesis_checkpoint = (
                                s.orchestrator.synthesis_checkpoint()
                            )
                            session_store.persist(
                                s, synthesis_checkpoint=_synthesis_checkpoint
                            )
                            logger.info(
                                "[ws][%s] Synthesis checkpoint persisted.",
                                s.session_id,
                            )
                        except Exception as _cp_exc:
                            logger.warning(
                                "[ws][%s] Could not write synthesis checkpoint: %s",
                                s.session_id,
                                _cp_exc,
                            )

                        async for event in s.orchestrator.synthesize():
                            serialized = make_serializable(event.model_dump())
                            logger.debug(
                                "[ws][%s] synthesis event: %s",
                                s.session_id,
                                json.dumps(serialized, indent=2),
                            )
                            s.emit(serialized)

                        s.state = "complete"

                    except asyncio.CancelledError:
                        logger.info("[ws][%s] Background task cancelled.", s.session_id)
                        # Cancel any live sub-tasks tracked by the orchestrator.
                        for t in s.orchestrator._active_tasks:
                            if not t.done():
                                t.cancel()
                        if s.orchestrator._active_tasks:
                            await asyncio.gather(
                                *s.orchestrator._active_tasks, return_exceptions=True
                            )
                        s.orchestrator._active_tasks = []

                        # If synthesis already emitted a report event, the
                        # session is effectively complete — the cancel arrived
                        # during post-report cleanup (status events, etc.).
                        # Treat it as a successful completion so the benchmark
                        # client receives the report, not a spurious error.
                        report_emitted = any(
                            e.get("type") == "report" for e in s.replay_log
                        )
                        if report_emitted:
                            logger.info(
                                "[ws][%s] Report already emitted — treating cancel as complete.",
                                s.session_id,
                            )
                            s.state = "complete"
                        else:
                            s.state = "cancelled"
                            record_event(
                                "session_cancelled",
                                session_id=s.session_id,
                            )
                            s.emit(
                                {
                                    "type": "error",
                                    "message": "Research session was cancelled.",
                                }
                            )

                    except Exception as exc:
                        logger.exception(
                            "[ws][%s] Background task error: %s", s.session_id, exc
                        )
                        s.state = "error"
                        record_event(
                            "session_error",
                            session_id=s.session_id,
                            error=str(exc),
                        )
                        s.emit({"type": "error", "message": str(exc)})

                    finally:
                        # Explicitly mark the session complete and wake the drain
                        # loop *before* calling finish() so _drain_session_to_ws
                        # can always exit cleanly regardless of what finish() does.
                        s.complete = True
                        s.new_event.set()
                        s.finish()
                        record_event(
                            "session_finished",
                            session_id=s.session_id,
                            final_state=s.state,
                            total_events=len(s.replay_log),
                        )
                        # Persist final state (includes report event if synthesis
                        # succeeded, or error state if it failed).  This ensures
                        # a reconnecting client can always replay the full log.
                        try:
                            session_store.persist(
                                s, synthesis_checkpoint=_synthesis_checkpoint
                            )
                        except Exception as _fp_exc:
                            logger.warning(
                                "[ws][%s] Could not persist final session state: %s",
                                s.session_id,
                                _fp_exc,
                            )
                        # Write a per-session debug dump so every completed
                        # research run has a self-contained JSON artifact.
                        try:
                            dump_path = log_dump(session_id=s.session_id)
                            logger.info(
                                "[ws][%s] Per-session log dump written: %s",
                                s.session_id,
                                dump_path,
                            )
                        except Exception as dump_exc:
                            logger.warning(
                                "[ws][%s] Could not write per-session log dump: %s",
                                s.session_id,
                                dump_exc,
                            )
                        # Release heavy Orchestrator buffers so completed
                        # sessions do not pin hundreds of MBs of claims,
                        # RAG chunks, and intermediate findings in memory
                        # while sitting idle in the SessionStore until
                        # TTL eviction.
                        try:
                            s.orchestrator.release_memory()
                        except Exception:
                            pass

                # Register the task with the session manager for coordinated
                # shutdown so it will never become an orphan.
                session_manager: ResearchSessionManager = app_state.session_manager
                job = await session_manager.start_job(
                    session_id=session.session_id,
                    coro=_run_session(),
                )
                session.background_task = job.task

                # Drain execution + synthesis events to this WebSocket connection
                # as a concurrent task so the receive loop stays responsive.
                # If the socket disconnects, _cancel_drain() in the
                # except-block below handles cleanup; the background task
                # continues unaffected.
                drain_task = asyncio.create_task(
                    _drain_session_to_ws(session, websocket, start_from=drain_cursor),
                    name=f"drain-{session.session_id}",
                )

            # ── modify_plan ───────────────────────────────────────────────────
            elif message_type == "modify_plan":
                plan_id = raw.get("planId")
                if not plan_id:
                    await websocket.send_json(
                        make_serializable(
                            ResponseMessage(
                                type="error",
                                message="planId is required to modify the plan.",
                            ).model_dump()
                        )
                    )
                    continue

                feedback = raw.get("feedback", "").strip()
                if not feedback:
                    await websocket.send_json(
                        make_serializable(
                            ResponseMessage(
                                type="error",
                                message="feedback is required to modify the plan.",
                            ).model_dump()
                        )
                    )
                    continue

                if current_session is None:
                    await websocket.send_json(
                        {"type": "error", "message": "No active session."}
                    )
                    continue

                orchestrator = current_session.orchestrator
                try:
                    updated = await orchestrator.modify_plan(
                        plan_id=plan_id, feedback=feedback
                    )
                    serialized = make_serializable(updated.model_dump())
                    current_session.emit(serialized)
                    await websocket.send_json(serialized)
                    logger.info(
                        "[ws] Modified plan sent for session %s",
                        current_session.session_id,
                    )
                except Exception as exc:
                    logger.exception("[ws] Error modifying plan: %s", exc)
                    await websocket.send_json({"type": "error", "message": str(exc)})
                continue

            # ── deny_plan ─────────────────────────────────────────────────────
            elif message_type == "deny_plan":
                plan_id = raw.get("planId")
                if current_session is None:
                    await websocket.send_json(
                        {"type": "error", "message": "No active session."}
                    )
                    continue

                orchestrator = current_session.orchestrator
                try:
                    response = await orchestrator.deny_plan(plan_id)
                    serialized = make_serializable(response.model_dump())
                    current_session.emit(serialized)
                    await websocket.send_json(serialized)
                except Exception as exc:
                    logger.warning("[ws] deny_plan error: %s", exc)

                logger.info(
                    "[ws] Plan denied for session %s.", current_session.session_id
                )
                current_session.finish()
                continue

            # ── self_optimize → run five-phase optimisation workflow ──────────
            elif message_type == "self_optimize":
                opt_agent: SelfOptimizingAgent = app_state.self_optimizing_agent
                await websocket.send_json(
                    {
                        "type": "optimize_started",
                        "message": "Self-optimization workflow starting…",
                    }
                )
                try:
                    async for event in opt_agent.self_optimize():
                        serialized = make_serializable(event.model_dump())
                        await websocket.send_json(serialized)
                except Exception as exc:
                    logger.exception("[ws] self_optimize error: %s", exc)
                    await websocket.send_json(
                        {"type": "error", "message": f"Self-optimization failed: {exc}"}
                    )
                continue

            # ── unknown message type ──────────────────────────────────────────
            else:
                logger.warning("[ws] Unknown message type: '%s'", message_type)
                await websocket.send_json(
                    {
                        "type": "error",
                        "message": (
                            f"Unknown message type: '{message_type}'\n"
                            f"Message: {json.dumps(raw, indent=2)}"
                        ),
                    }
                )

    except WebSocketDisconnect:
        logger.info(
            "[ws] WebSocket disconnected (session=%s). "
            "Background task continues if execution is in progress.",
            current_session.session_id if current_session else "none",
        )
        await _cancel_drain()

    except asyncio.CancelledError:
        logger.info(
            "[ws] WebSocket handler cancelled (session=%s).",
            current_session.session_id if current_session else "none",
        )
        await _cancel_drain()
        raise

    except Exception as exc:
        logger.exception("[ws] Unhandled WebSocket error: %s", exc)
        await _cancel_drain()
        try:
            await websocket.send_json({"type": "error", "message": str(exc)})
        except Exception:
            pass
