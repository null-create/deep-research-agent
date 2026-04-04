"""session_store.py

Per-research-session state management for the WebSocket endpoint.

Each ``ResearchSession`` wraps an Orchestrator instance together with:

* A chronological ``replay_log`` containing every serialised event emitted
  during the session.  Reconnecting clients start replay from index 0, so
  they receive the full execution history regardless of when they were
  disconnected.
* An ``asyncio.Event`` (``new_event``) that is signalled after every
  ``emit()`` call, allowing drain coroutines to block efficiently without
  polling.
* A ``background_task`` handle that runs ``execute()`` + ``synthesize()``
  concurrently with (and independently of) the originating WebSocket
  connection.  The task keeps running after a disconnect so that a
  reconnecting client can pick up mid-execution results.

Typical lifecycle
-----------------
1. Client sends ``query``  →  ``SessionStore.create()`` returns a new session.
2. Plan events are emitted synchronously (fast LLM call) and added to
   ``replay_log`` via ``session.emit()``.
3. Client sends ``approve_plan``  →  a background ``asyncio.Task`` is created
   that runs ``orchestrator.execute()`` then ``orchestrator.synthesize()``,
   emitting every event to the session.
4. A drain coroutine (``drain_session_to_ws``) streams ``replay_log`` events
   to the WebSocket in order; it waits on ``new_event`` between batches.
5. If the WebSocket closes the drain coroutine exits, but the background task
   continues running.
6. Client reconnects, sends ``resume`` with the ``session_id``  →  the handler
   replays the full ``replay_log`` and re-attaches a drain coroutine for any
   remaining live events.
"""

import asyncio
import json
import logging
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Sessions idle for longer than this are eligible for eviction.
_SESSION_TTL_SECONDS: int = 3600  # 1 hour

# Directory for persisted session checkpoints.  Respects the LOG_DIR env var
# so Docker deployments can override the path via environment configuration.
_DEFAULT_SESSIONS_DIR: Path = (
    Path(os.getenv("LOG_DIR", str(Path(__file__).parent / "logs"))) / "sessions"
)


@dataclass
class ResearchSession:
    """All mutable state for a single research pipeline run.

    Attributes
    ----------
    session_id:
        Unique identifier returned to the client on session creation so it
        can be presented on reconnect to resume the same pipeline.
    orchestrator:
        Dedicated ``Orchestrator`` instance for this session.
        Typed ``Any`` to avoid a circular import with ``orchestrator.py``.
    replay_log:
        All serialized ``ResponseMessage`` dicts in chronological order.
        Append-only; never shrinks.  Reconnecting clients replay from index 0.
    background_task:
        ``asyncio.Task`` running the execution pipeline.  Assigned when the
        client approves the plan; ``None`` until then.  Survives WebSocket
        disconnects.
    state:
        Coarse lifecycle label:
        ``idle`` → ``planning`` → ``awaiting_approval`` → ``executing``
        → ``complete`` | ``error`` | ``cancelled``.
    complete:
        ``True`` once the background task finishes (success or error).
        Drain coroutines check this to know when to exit.
    new_event:
        ``asyncio.Event`` signalled after every ``emit()`` and ``finish()``
        call.  Drain coroutines ``await`` it instead of busy-looping.
    created_at, last_activity:
        Used for TTL-based eviction in ``SessionStore.prune_expired()``.
    """

    session_id: str
    orchestrator: Any  # Orchestrator — Any to avoid circular import
    replay_log: List[Dict[str, Any]] = field(default_factory=list)
    background_task: Optional[asyncio.Task] = None
    job_id: Optional[str] = None  # ResearchSessionManager job ID
    state: str = "idle"
    complete: bool = False
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_activity: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        # asyncio.Event must be created inside a running event loop.
        self.new_event: asyncio.Event = asyncio.Event()

    # ------------------------------------------------------------------
    # Event helpers
    # ------------------------------------------------------------------

    def emit(self, event: Dict[str, Any]) -> None:
        """Append *event* to the replay log and wake any waiting drainers."""
        self.replay_log.append(event)
        self.last_activity = datetime.now(timezone.utc)
        self.new_event.set()

    def finish(self) -> None:
        """Signal that no more events will be emitted (session complete)."""
        self.complete = True
        self.last_activity = datetime.now(timezone.utc)
        self.new_event.set()  # wake drain coroutines so they can exit cleanly


class SessionStore:
    """In-memory registry of active research sessions.

    All mutations are synchronous dict operations.  Individual sessions use
    ``asyncio.Event`` internally, so concurrent access within a single asyncio
    event loop (as guaranteed by FastAPI/uvicorn) is safe.
    """

    def __init__(self, sessions_dir: Optional[Path] = None) -> None:
        self._sessions: Dict[str, ResearchSession] = {}
        self._sessions_dir: Path = (
            Path(sessions_dir) if sessions_dir else _DEFAULT_SESSIONS_DIR
        )
        self._sessions_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def create(
        self,
        orchestrator: Any,
        session_id: Optional[str] = None,
    ) -> ResearchSession:
        """Create, register, and return a new ``ResearchSession``."""
        sid = session_id or str(uuid.uuid4())
        session = ResearchSession(session_id=sid, orchestrator=orchestrator)
        self._sessions[sid] = session
        logger.info("Session created: %s", sid)
        return session

    def get(self, session_id: str) -> Optional[ResearchSession]:
        """Return the session for *session_id*, or ``None`` if not found."""
        return self._sessions.get(session_id)

    def remove(self, session_id: str) -> None:
        """Remove the session and cancel its background task if still running."""
        session = self._sessions.pop(session_id, None)
        if session is None:
            return
        if session.background_task and not session.background_task.done():
            session.background_task.cancel()
        logger.info("Session removed: %s", session_id)

    # ------------------------------------------------------------------
    # Disk persistence
    # ------------------------------------------------------------------

    def _session_file(self, session_id: str) -> Path:
        return self._sessions_dir / f"{session_id}.json"

    def persist(
        self,
        session: ResearchSession,
        synthesis_checkpoint: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Persist session replay_log and state to disk.

        Writes atomically via a .tmp intermediate so a crash mid-write never
        produces a corrupted checkpoint file.
        """
        data: Dict[str, Any] = {
            "session_id": session.session_id,
            "state": session.state,
            "created_at": session.created_at.isoformat(),
            "replay_log": session.replay_log,
        }
        if synthesis_checkpoint is not None:
            data["synthesis_checkpoint"] = synthesis_checkpoint
        try:
            path = self._session_file(session.session_id)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data), encoding="utf-8")
            tmp.replace(path)  # atomic rename on the same filesystem
        except Exception as exc:
            logger.warning("Could not persist session %s: %s", session.session_id, exc)

    def load_checkpoint(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Load persisted session data from disk; returns None if absent or corrupted."""
        path = self._session_file(session_id)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Could not read checkpoint for %s: %s", session_id, exc)
            return None

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------

    def prune_expired(self) -> int:
        """Remove sessions idle for more than ``_SESSION_TTL_SECONDS``.

        Returns the number of sessions pruned.
        """
        now = datetime.now(timezone.utc)
        stale = [
            sid
            for sid, sess in self._sessions.items()
            if (now - sess.last_activity).total_seconds() > _SESSION_TTL_SECONDS
        ]
        for sid in stale:
            self.remove(sid)
        if stale:
            logger.info("Pruned %d expired session(s).", len(stale))
        return len(stale)
