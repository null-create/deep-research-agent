import os
import asyncio
import json
import traceback
from typing import Optional, Dict, List, Any
from contextlib import AsyncExitStack

import anyio
import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.types import (
    Tool,
    InitializeResult,
    CallToolResult,
    ListResourcesResult,
    ListToolsResult,
    ListPromptsResult,
    ListResourceTemplatesResult,
    ReadResourceResult,
)
from mcp.client.streamable_http import streamable_http_client
from mcp.client.stdio import stdio_client
from mcp.client.sse import sse_client

from config import Config
from models import Message
from observability import get_logger

logger = get_logger(__name__)


class MCPClient:
    def __init__(self):
        self.session: Optional[ClientSession] = None
        self.exit_stack = None
        self._session_context = None
        self._streams_context = None
        # Transport configuration — persisted so reconnect() can replay.
        self._transport: str = "streamable-http"
        self._url: Optional[str] = None
        self._headers: Optional[dict] = None
        self._command: Optional[str] = None  # stdio only
        self._args: List[str] = []  # stdio only
        self._env: Optional[dict] = None  # stdio only
        # Prevent concurrent reconnection attempts.
        self._reconnect_lock = asyncio.Lock()

    async def connect(
        self,
        url: Optional[str] = None,
        headers: Optional[dict] = None,
        use_oauth: bool = False,
        max_retries: int = 5,
        transport: str = "streamable-http",
        command: Optional[str] = None,
        args: Optional[List[str]] = None,
        env: Optional[dict] = None,
    ):
        """Connect to an MCP server.

        Supports three transports:
        - ``streamable-http`` (default): HTTP-based, requires *url*.
        - ``sse``: Server-Sent Events, requires *url*.
        - ``stdio``: subprocess-based, requires *command* (and optionally *args*, *env*).
        """
        self._transport = transport
        self._url = url
        self._headers = headers
        self._command = command
        self._args = args or []
        self._env = env

        last_error = None

        for attempt in range(max_retries):
            try:
                if attempt > 0:
                    wait_time = min(2**attempt, 30)  # Exponential backoff, max 30s
                    logger.info(
                        f"[MCPClient] Retry attempt {attempt + 1}/{max_retries} after {wait_time}s..."
                    )
                    await asyncio.sleep(wait_time)

                target = url or f"{command} {' '.join(self._args)}"
                logger.info(f"[MCPClient] Connecting via {transport}: {target}")

                async with AsyncExitStack() as exit_stack:
                    if transport == "stdio":
                        params = StdioServerParameters(
                            command=command,
                            args=self._args,
                            env=env,
                        )
                        self._streams_context = stdio_client(params)
                        transport_streams = await exit_stack.enter_async_context(
                            self._streams_context
                        )
                        read_stream, write_stream = transport_streams
                    elif transport == "sse":
                        self._streams_context = sse_client(url, headers=headers)
                        transport_streams = await exit_stack.enter_async_context(
                            self._streams_context
                        )
                        read_stream, write_stream = transport_streams
                    else:  # streamable-http
                        self._streams_context = streamable_http_client(
                            url,
                            http_client=httpx.AsyncClient(
                                headers=headers,
                                timeout=httpx.Timeout(
                                    connect=10.0, read=None, write=30.0, pool=10.0
                                ),
                                limits=httpx.Limits(
                                    max_keepalive_connections=5,
                                    keepalive_expiry=30,
                                ),
                            ),
                        )
                        transport_streams = await exit_stack.enter_async_context(
                            self._streams_context
                        )
                        read_stream, write_stream, _ = transport_streams
                        if isinstance(read_stream, Exception):
                            logger.error(
                                f"[MCPClient] Error creating read stream: {str(read_stream)}"
                            )
                            raise read_stream

                    self._session_context = ClientSession(read_stream, write_stream)

                    # pylint: disable=W0201
                    self.session = await exit_stack.enter_async_context(
                        self._session_context
                    )

                    try:
                        result: InitializeResult = await asyncio.wait_for(
                            self.session.initialize(), timeout=10.0
                        )
                        logger.info(
                            f"[MCPClient] ✓ Session initialized with {result.serverInfo.name} "
                            f"protocol version {result.protocolVersion}"
                        )
                    except asyncio.TimeoutError as e:
                        logger.error(
                            f"[MCPClient] ✗ Timeout during session initialization: {str(e)}"
                        )
                        raise TimeoutError("Session initialization timed out") from e

                    self.exit_stack = exit_stack.pop_all()
                    return  # Success!

            except (
                TimeoutError,
                ConnectionError,
                httpx.ConnectError,
                httpx.ReadTimeout,
            ) as e:
                last_error = e
                logger.warning(
                    f"[MCPClient] Connection attempt {attempt + 1}/{max_retries} failed: "
                    f"{type(e).__name__}: {str(e)}"
                )
                try:
                    await self.disconnect()
                except Exception:
                    pass
                if attempt < max_retries - 1:
                    continue
                # If this was the last attempt, fall through to raise the error

            except Exception as e:
                last_error = e
                logger.error(
                    "[MCPClient] ✗ Failed to connect: %s — %s", type(e).__name__, str(e)
                )
                logger.error("[MCPClient] Traceback: %s", traceback.format_exc())
                try:
                    await self.disconnect()
                except Exception:
                    pass
                # For non-retryable errors, raise immediately
                raise

        # If we exhausted all retries, raise the last error
        if last_error:
            logger.error(
                f"[MCPClient] ✗ Failed to connect after {max_retries} attempts"
            )
            raise last_error

    async def reconnect(self) -> None:
        """Re-establish the MCP session after a transport drop."""
        async with self._reconnect_lock:
            # Another coroutine may have already reconnected while we waited.
            if self.session is not None:
                return

            if self._transport == "stdio":
                if not self._command:
                    raise RuntimeError(
                        "[MCPClient] Cannot reconnect: no command stored."
                    )
                target = f"{self._command} {' '.join(self._args)}"
            else:
                if not self._url:
                    raise RuntimeError(
                        "[MCPClient] Cannot reconnect: no URL stored (connect() was never called)."
                    )
                target = self._url

            logger.warning(
                "[MCPClient] Session lost for %s — attempting reconnect…", target
            )

            # Discard the dead exit stack without raising (best-effort).
            old_stack = self.exit_stack
            self.exit_stack = None
            self.session = None
            if old_stack is not None:
                try:
                    # Run teardown in a fresh task so anyio cancel-scope
                    # constraints are satisfied (scopes must be exited from
                    # the same task that entered them).
                    cleanup = asyncio.get_running_loop().create_task(old_stack.aclose())
                    await asyncio.wait_for(cleanup, timeout=5.0)
                except Exception:
                    pass  # Best-effort; we're already reconnecting

            await self.connect(
                url=self._url,
                headers=self._headers,
                transport=self._transport,
                command=self._command,
                args=self._args,
                env=self._env,
                max_retries=3,
            )
            logger.info("[MCPClient] ✓ Reconnected to %s successfully.", target)

    # ------------------------------------------------------------------
    # Helpers to detect a dead / invalidated session
    # ------------------------------------------------------------------

    @staticmethod
    def _is_session_error(exc: Exception) -> bool:
        """Return True if *exc* looks like a dropped or invalidated session.

        Deliberately does NOT match all RuntimeErrors — shutdown-time errors
        like 'Event loop is closed' or 'no running event loop' are RuntimeErrors
        but should not trigger a reconnect attempt.
        """
        # anyio resource errors have empty str() so must be checked by type first.
        if isinstance(exc, (anyio.ClosedResourceError, anyio.BrokenResourceError)):
            return True
        msg = str(exc).lower()
        return (
            "eof" in msg
            or "closed" in msg
            or "broken pipe" in msg
            or "connection reset" in msg
            or "not connected" in msg
            or "send stream" in msg
        )

    async def list_tool_specs(self) -> list[dict[str, Any]]:
        if not self.session:
            raise RuntimeError("MCP client is not connected.")

        result: ListToolsResult = await self.session.list_tools()
        tools = result.tools
        tool_specs = []

        for tool in tools:
            name = tool.name
            description = tool.description if tool.description else ""
            input_schema = tool.inputSchema if tool.inputSchema else {}

            # TODO: handle outputSchema if needed
            output_schema = getattr(tool, "outputSchema", None)

            tool_specs.append(
                {"name": name, "description": description, "parameters": input_schema}
            )

        return tool_specs

    async def call_tool(
        self, function_name: str, function_args: dict
    ) -> Optional[dict]:
        """Call a tool on the MCP server, reconnecting once if the session has dropped."""
        logger.info("[MCPClient] Calling tool '%s'", function_name)

        for attempt in range(2):  # 0 = normal, 1 = after reconnect
            if not self.session:
                if attempt == 0:
                    await self.reconnect()
                else:
                    raise RuntimeError("MCP client is not connected.")

            try:
                result: CallToolResult = await asyncio.wait_for(
                    self.session.call_tool(function_name.lower(), function_args),
                    timeout=120.0,
                )

                if not result:
                    raise Exception("No result returned from MCP tool call.")

                result_dict = result.model_dump()
                result_content = result_dict.get("content", {})

                if result.isError:
                    raise Exception(result_content)
                return result_content

            except asyncio.CancelledError:
                raise  # Never swallow cancellation

            except Exception as exc:
                if attempt == 0 and self._is_session_error(exc):
                    logger.warning(
                        "[MCPClient] Tool call '%s' failed with session error (%s) — reconnecting…",
                        function_name,
                        repr(exc),
                    )
                    self.session = None  # Force reconnect on next iteration
                    await self.reconnect()
                    continue  # Retry the call with the new session
                raise  # Re-raise on second attempt or non-session errors

    async def list_resources(self, cursor: Optional[str] = None) -> Optional[dict]:
        if not self.session:
            raise RuntimeError("MCP client is not connected.")

        result: ListResourcesResult = await self.session.list_resources(cursor=cursor)
        if not result:
            raise Exception("No result returned from MCP list_resources call.")

        result_dict = result.model_dump()
        resources = result_dict.get("resources", [])

        return resources

    async def list_resource_templates(self) -> list[dict]:
        if not self.session:
            raise RuntimeError("MCP client is not connected.")
        try:
            result: ListResourceTemplatesResult = (
                await self.session.list_resource_templates()
            )
            return result.model_dump().get("resourceTemplates", [])
        except Exception:
            return []

    async def list_prompts(self) -> list[dict]:
        if not self.session:
            raise RuntimeError("MCP client is not connected.")
        try:
            result: ListPromptsResult = await self.session.list_prompts()
            return result.model_dump().get("prompts", [])
        except Exception:
            return []

    async def read_resource(self, uri: str) -> Optional[dict]:
        if not self.session:
            raise RuntimeError("MCP client is not connected.")

        result: ReadResourceResult = await self.session.read_resource(uri)

        if not result:
            raise Exception("No result returned from MCP read_resource call.")

        result_dict = result.model_dump()

        return result_dict

    async def disconnect(self):
        """Tear down the MCP session.

        The exit stack contains anyio cancel scopes that **must** be exited
        from the same OS task that entered them.  We achieve this by
        scheduling the ``aclose()`` as a brand-new asyncio Task, which anyio
        treats as a fresh task with no parent cancel scope, satisfying the
        constraint and eliminating the
        "Attempted to exit cancel scope in a different task" warnings.
        """
        stack = self.exit_stack
        self.exit_stack = None
        self.session = None

        if stack is not None:
            try:
                # Create a new task so anyio sees the cancel-scope cleanup
                # as originating from a fresh task context.
                loop = asyncio.get_running_loop()
                cleanup_task = loop.create_task(stack.aclose())
                # Shield from any outer CancelledError while we wait.
                await asyncio.shield(asyncio.wait_for(cleanup_task, timeout=10.0))
            except asyncio.TimeoutError:
                logger.warning("[MCPClient] Disconnect timed out; abandoning.")
            except asyncio.CancelledError:
                # The shield itself was cancelled (e.g. SIGKILL). Nothing
                # more we can do — the cleanup task continues on its own.
                pass
            except Exception as exc:
                # "Attempted to exit cancel scope in a different task" is a
                # known anyio/MCP SDK limitation with the streamable-http
                # transport and carries no data loss. Log at DEBUG only.
                msg = str(exc)
                if "cancel scope" in msg:
                    logger.debug(
                        "[MCPClient] Disconnect cancel-scope notice (harmless): %s", exc
                    )
                else:
                    logger.warning("[MCPClient] Error during disconnect: %s", exc)

    async def ping(self) -> bool:
        """Verify the server is reachable.

        For HTTP-based transports (streamable-http, sse), performs a lightweight
        GET to /health.  For stdio, returns True when the session is live.
        """
        if self._transport == "stdio":
            return self.session is not None
        if not self._url:
            return False
        health_url = self._url.rsplit("/mcp", 1)[0] + "/health"
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(health_url)
                return resp.is_success
        except Exception:
            return False

    async def __aenter__(self):
        if self.exit_stack:
            await self.exit_stack.__aenter__()
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        if self.exit_stack:
            await self.exit_stack.__aexit__(exc_type, exc_value, traceback)
        await self.disconnect()


class MCPServerRegistry:
    """
    In-memory registry for all MCP servers configured to work with the Research Agent
    """

    # How often to send a keepalive ping to each server (seconds).
    # Default 60s — well below Docker Desktop's NAT idle-connection timeout
    # (~120-300s depending on platform).  240s was too close to the timeout:
    # SSE streams were dropped before the first ping landed.
    _KEEPALIVE_INTERVAL: float = float(os.getenv("MCP_KEEPALIVE_INTERVAL", "60"))

    # Path for persisting user-added (non-builtin) servers across restarts.
    _USER_SERVERS_FILE: str = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "configs",
        "user_mcp_servers.json",
    )

    def __init__(self):
        # Cache for MCP clients keyed by server name
        self.servers: Dict[str, MCPClient] = {}

        # List of all available tools across servers, used for agent tool selection
        self.tool_specs: Dict[str, list[dict[str, Any]]] = {}

        # Rich configuration metadata for every registered server (for API responses).
        # Shape: {name, transport, url?, command?, args?, env?, builtin, tools_count}
        self.server_configs: Dict[str, dict] = {}

        # Background keepalive task handle (started by start_keepalive())
        self._keepalive_task: Optional[asyncio.Task] = None

    async def register(
        self,
        server_name: str,
        url: Optional[str] = None,
        transport: str = "streamable-http",
        command: Optional[str] = None,
        args: Optional[List[str]] = None,
        env: Optional[dict] = None,
        headers: Optional[dict] = None,
        builtin: bool = False,
        max_retries: int = 10,
    ) -> bool:
        """Register an MCP server.  Returns True on success, False if connection failed."""
        mcp_client = MCPClient()
        try:
            await mcp_client.connect(
                url=url,
                transport=transport,
                command=command,
                args=args,
                env=env,
                headers=headers,
                max_retries=max_retries,
            )
        except Exception as e:
            logger.error(
                f"[MCPRegistry] Failed to connect to MCP server '{server_name}': {str(e)}"
            )
            logger.warning(
                f"[MCPRegistry] Continuing without '{server_name}' server. "
                f"Some functionality may be limited."
            )
            # Store config even when connection fails so the UI shows it as disconnected.
            self.server_configs[server_name] = self._make_config(
                server_name, transport, url, command, args, env, builtin, tools_count=0
            )
            return False

        tool_specs = await mcp_client.list_tool_specs()
        for tool in tool_specs:
            tool["type"] = "function"

        self.tool_specs[server_name] = tool_specs
        self.servers[server_name] = mcp_client
        self.server_configs[server_name] = self._make_config(
            server_name,
            transport,
            url,
            command,
            args,
            env,
            builtin,
            tools_count=len(tool_specs),
        )
        logger.info(
            f"[MCPRegistry] ✓ Registered MCP server '{server_name}' with {len(tool_specs)} tools"
        )
        return True

    async def unregister(self, server_name: str) -> None:
        """Disconnect and remove a registered server."""
        if server_name in self.servers:
            try:
                await self.servers[server_name].disconnect()
            except Exception as exc:
                logger.warning(
                    "[MCPRegistry] Error disconnecting '%s': %s", server_name, exc
                )
            del self.servers[server_name]
        self.tool_specs.pop(server_name, None)
        self.server_configs.pop(server_name, None)

    @staticmethod
    def _make_config(
        name: str,
        transport: str,
        url: Optional[str],
        command: Optional[str],
        args: Optional[List[str]],
        env: Optional[dict],
        builtin: bool,
        tools_count: int,
    ) -> dict:
        cfg: dict = {
            "name": name,
            "transport": transport,
            "builtin": builtin,
            "tools_count": tools_count,
        }
        if transport in ("streamable-http", "sse"):
            cfg["url"] = url or ""
        else:
            cfg["command"] = command or ""
            cfg["args"] = args or []
            if env:
                cfg["env"] = env
        return cfg

    def get_all_server_info(self) -> List[dict]:
        """Return a list of server info dicts suitable for the API response."""
        result = []
        for name, cfg in self.server_configs.items():
            info = dict(cfg)
            client = self.servers.get(name)
            if client is None:
                info["status"] = "disconnected"
            elif client.session is not None:
                info["status"] = "connected"
            else:
                info["status"] = "error"
            result.append(info)
        return result

    def _save_user_servers(self) -> None:
        """Persist non-builtin server configs to disk."""
        user_servers = [
            cfg for cfg in self.server_configs.values() if not cfg.get("builtin", False)
        ]
        try:
            os.makedirs(os.path.dirname(self._USER_SERVERS_FILE), exist_ok=True)
            with open(self._USER_SERVERS_FILE, "w") as f:
                json.dump(user_servers, f, indent=2)
        except Exception as exc:
            logger.warning("[MCPRegistry] Failed to persist user servers: %s", exc)

    def _load_user_servers_raw(self) -> List[dict]:
        """Load persisted user-added server configs from disk."""
        try:
            with open(self._USER_SERVERS_FILE) as f:
                return json.load(f)
        except FileNotFoundError:
            return []
        except Exception as exc:
            logger.warning("[MCPRegistry] Failed to load user servers file: %s", exc)
            return []

    def start_keepalive(self) -> None:
        """Launch a background task that pings every registered MCP server on a
        regular interval to prevent Docker/NAT idle-connection timeouts from
        silently dropping the long-lived SSE streams.

        Safe to call multiple times — duplicate calls are no-ops.
        """
        if self._keepalive_task and not self._keepalive_task.done():
            return
        self._keepalive_task = asyncio.get_running_loop().create_task(
            self._keepalive_loop(), name="mcp-keepalive"
        )
        logger.info(
            "[MCPRegistry] Keepalive task started (interval=%.0fs).",
            self._KEEPALIVE_INTERVAL,
        )

    def stop_keepalive(self) -> None:
        """Cancel the keepalive background task if it is running."""
        if self._keepalive_task and not self._keepalive_task.done():
            self._keepalive_task.cancel()
        self._keepalive_task = None

    async def _keepalive_loop(self) -> None:
        """Background loop: ping every registered server every _KEEPALIVE_INTERVAL seconds."""
        try:
            while True:
                await asyncio.sleep(self._KEEPALIVE_INTERVAL)
                for server_name, client in list(self.servers.items()):
                    alive = await client.ping()
                    if not alive:
                        logger.warning(
                            "[MCPRegistry] Keepalive ping to '%s' failed — reconnecting.",
                            server_name,
                        )
                        try:
                            await client.reconnect()
                        except Exception as exc:
                            logger.error(
                                "[MCPRegistry] Reconnect to '%s' failed: %s",
                                server_name,
                                exc,
                            )
                    else:
                        logger.debug(
                            "[MCPRegistry] Keepalive ping to '%s' OK.", server_name
                        )
        except asyncio.CancelledError:
            pass  # Normal shutdown

    def get(self, server_name: str) -> Optional[MCPClient]:
        """Get an MCP server client"""
        return self.servers.get(server_name)

    async def get_all_tools(self) -> List[Dict[str, Any]]:
        """Get all tools from all registered servers"""
        all_tools = []
        for server_name, client in self.servers.items():
            tool_specs = await client.list_tool_specs()
            for tool in tool_specs:
                tool["type"] = (
                    "function"  # injected for OpenAI function calling compatibility
                )
                all_tools.append(tool)
        return all_tools

    async def close(self) -> None:
        """Disconnect all registered MCP server clients."""
        self.stop_keepalive()
        for server_name, client in list(self.servers.items()):
            try:
                await client.disconnect()
            except asyncio.CancelledError:
                # Don't let an outer cancel scope abort the rest of the loop.
                logger.warning(
                    "[MCPRegistry] Disconnect of '%s' was cancelled; continuing.",
                    server_name,
                )
            except Exception as exc:
                logger.warning(
                    "[MCPRegistry] Error disconnecting '%s': %s", server_name, exc
                )
        self.servers.clear()
        self.tool_specs.clear()
        self.server_configs.clear()

    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Any:
        """Call a tool, automatically routing to the correct server"""
        for server_name, tools in self.tool_specs.items():
            if any(tool["name"] == tool_name for tool in tools):
                client = self.servers[server_name]
                try:
                    result = await client.call_tool(tool_name, arguments)
                    if not result:
                        raise Exception("No result returned from MCP tool call.")
                    logger.debug(
                        f"Called tool '{tool_name}' on server '{server_name}' with arguments {arguments}. Result: {json.dumps(result)}"
                    )
                    return result
                except Exception as e:
                    logger.error(
                        f"Error calling tool '{tool_name}' on server '{server_name}': {repr(e)}",
                        exc_info=True,
                    )
                    return {"error": repr(e)}

        raise Exception(f"Tool '{tool_name}' not found in any registered MCP server.")


async def create_mcp_registry(config: Config) -> MCPServerRegistry:
    """Create and configure the MCP server registry."""
    registry = MCPServerRegistry()

    # Register the three built-in streamable-http servers.
    builtin_servers = [
        ("web_search", config.search_server_url),
        ("fetch", config.fetch_server_url),
        ("file_handler", config.file_server_url),
    ]
    for server_name, server_url in builtin_servers:
        try:
            await registry.register(
                server_name,
                url=server_url,
                transport="streamable-http",
                builtin=True,
            )
        except Exception as e:
            logger.error(f"Error registering MCP server '{server_name}': {str(e)}")

    # Load and register user-added servers persisted from previous sessions.
    for cfg in registry._load_user_servers_raw():
        name = cfg.get("name")
        if not name or name in registry.server_configs:
            continue
        try:
            await registry.register(
                name,
                url=cfg.get("url"),
                transport=cfg.get("transport", "streamable-http"),
                command=cfg.get("command"),
                args=cfg.get("args"),
                env=cfg.get("env"),
                builtin=False,
                max_retries=3,
            )
        except Exception as e:
            logger.warning(f"Failed to reconnect user server '{name}': {e}")

    return registry
