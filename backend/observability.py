"""
observability.py

Lightweight logging wrapper for the Research Assistant backend.

Wraps Python's standard ``logging`` module with:
  - Structured kwargs on every log call (attached as JSON context)
  - Colour-coded terminal output that adapts to INFO vs DEBUG level
  - ``log_dump()`` to write a compact JSON summary + optional full records

Usage
-----
    from observability import get_logger, setup_logging, log_dump

    setup_logging()                         # call once at startup
    log = get_logger(__name__)

    log.info("plan_generated", session_id="abc", num_steps=5)
    log.debug("tool_call", tool="web_search", query="climate change")
    log.error("llm_failure", error=str(exc))

    log_dump(session_id="abc")              # compact JSON artifact

Environment variables
---------------------
LOG_LEVEL     Integer or name (e.g. ``20`` or ``INFO``).  Default: ``INFO``.
              Set to ``10`` / ``DEBUG`` for verbose output and auto-dumps.
LOG_DIR       Directory for dump files.  Default: ``./logs``.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
import re
import sys
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Deque, Dict, List, Optional

__all__ = ["setup_logging", "get_logger", "log_dump", "record_event"]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_LOG_DIR = os.path.join(Path(__file__).parent, "logs")
_SESSIONS_SUBDIR = "sessions"
_ISO_FMT = "%Y-%m-%dT%H:%M:%S.%fZ"
_BUFFER_MAX = 10_000  # ring-buffer cap — keeps memory bounded

# Set to True by setup_logging() when the resolved level is DEBUG.
# Both the NDJSON file handler and log_dump() are gated on this flag.
_debug_mode: bool = False

# ---------------------------------------------------------------------------
# ANSI helpers
# ---------------------------------------------------------------------------

_RESET = "\033[0m"
_BOLD = "\033[1m"
_DIM = "\033[2m"
_LEVEL_COLORS = {
    logging.DEBUG: "\033[36m",  # cyan
    logging.INFO: "\033[32m",  # green
    logging.WARNING: "\033[33m",  # yellow
    logging.ERROR: "\033[31m",  # red
    logging.CRITICAL: "\033[35m",  # magenta
}


def _color(text: str, code: str) -> str:
    return f"{code}{text}{_RESET}" if sys.stdout.isatty() else text


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------


class _CompactFormatter(logging.Formatter):
    """Single-line INFO-level formatter."""

    def format(self, record: logging.LogRecord) -> str:
        colour = _LEVEL_COLORS.get(record.levelno, "")
        lvl = _color(f"{record.levelname:<8}", colour + _BOLD)
        name = _color(f"{record.name:<30}", _DIM)
        ts = self.formatTime(record, "%H:%M:%S")
        return f"{ts}  {lvl}  {name}  {record.getMessage()}"


class _VerboseFormatter(logging.Formatter):
    """Multi-line DEBUG formatter — also prints structured kwargs."""

    def format(self, record: logging.LogRecord) -> str:
        colour = _LEVEL_COLORS.get(record.levelno, "")
        lvl = _color(f"[{record.levelname}]", colour + _BOLD)
        name = _color(record.name, _DIM)
        ts = self.formatTime(record, _ISO_FMT)
        base = f"{ts}  {lvl}  [{name}]  {record.filename}:{record.lineno}\n  {record.getMessage()}"
        ctx: Dict[str, Any] = getattr(record, "_obs_ctx", {})
        if ctx:
            base += "\n  " + _color(json.dumps(ctx, default=str, indent=4), _DIM)
        if record.exc_info:
            base += "\n" + self.formatException(record.exc_info)
        return base


# ---------------------------------------------------------------------------
# In-process ring buffer (feeds log_dump)
# ---------------------------------------------------------------------------


class _Buffer:
    def __init__(self) -> None:
        self._buf: Deque[Dict[str, Any]] = deque(maxlen=_BUFFER_MAX)
        self._lock = Lock()

    def append(self, entry: Dict[str, Any]) -> None:
        with self._lock:
            self._buf.append(entry)

    def all(self) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._buf)

    def for_session(self, sid: str) -> List[Dict[str, Any]]:
        with self._lock:
            return [r for r in self._buf if r.get("session_id") == sid]

    def __len__(self) -> int:
        with self._lock:
            return len(self._buf)


_buffer = _Buffer()

# ---------------------------------------------------------------------------
# Buffer handler — appends every record to _buffer
# ---------------------------------------------------------------------------


class _BufferHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            ctx = getattr(record, "_obs_ctx", {})
            entry: Dict[str, Any] = {
                "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).strftime(
                    _ISO_FMT
                ),
                "level": logging.getLevelName(record.levelno),
                "logger": _ANSI_RE.sub("", record.name),
                "module": record.module,
                "lineno": record.lineno,
                "message": record.getMessage(),
                **ctx,
            }
            if record.exc_info:
                entry["exception"] = logging.Formatter().formatException(
                    record.exc_info
                )
            _buffer.append(entry)
        except Exception:
            self.handleError(record)


# ---------------------------------------------------------------------------
# ObservabilityLogger — thin wrapper that adds structured kwargs
# ---------------------------------------------------------------------------


class ObservabilityLogger:
    """Drop-in logger wrapper.  Pass any extra kwargs for structured output."""

    def __init__(self, logger: logging.Logger) -> None:
        self._logger = logger

    def _emit(
        self, level: int, msg: str, *args: Any, exc_info: bool = False, **kw: Any
    ) -> None:
        self._logger.log(
            level, msg, *args, exc_info=exc_info, extra={"_obs_ctx": kw}, stacklevel=3
        )

    def debug(self, msg: str, *a: Any, **kw: Any) -> None:
        self._emit(logging.DEBUG, msg, *a, **kw)

    def info(self, msg: str, *a: Any, **kw: Any) -> None:
        self._emit(logging.INFO, msg, *a, **kw)

    def warning(self, msg: str, *a: Any, **kw: Any) -> None:
        self._emit(logging.WARNING, msg, *a, **kw)

    def error(self, msg: str, *a: Any, **kw: Any) -> None:
        self._emit(logging.ERROR, msg, *a, **kw)

    def critical(self, msg: str, *a: Any, **kw: Any) -> None:
        self._emit(logging.CRITICAL, msg, *a, **kw)

    def exception(self, msg: str, *a: Any, **kw: Any) -> None:
        self._emit(logging.ERROR, msg, *a, exc_info=True, **kw)

    @property
    def name(self) -> str:
        return self._logger.name

    def isEnabledFor(self, level: int) -> bool:
        return self._logger.isEnabledFor(level)

    def setLevel(self, level: int) -> None:
        self._logger.setLevel(level)


def get_logger(name: str) -> ObservabilityLogger:
    """Return an ObservabilityLogger for *name*.  Use like ``logging.getLogger``."""
    return ObservabilityLogger(logging.getLogger(name))


# ---------------------------------------------------------------------------
# record_event() — inject a structured event directly into the buffer
# ---------------------------------------------------------------------------


def record_event(
    event_type: str, session_id: Optional[str] = None, **fields: Any
) -> None:
    """Write a structured event to the buffer without going through logging."""
    entry: Dict[str, Any] = {
        "ts": datetime.now(tz=timezone.utc).strftime(_ISO_FMT),
        "level": "EVENT",
        "logger": "observability.event",
        "message": event_type,
    }
    if session_id:
        entry["session_id"] = session_id
    entry.update(fields)
    _buffer.append(entry)


# ---------------------------------------------------------------------------
# log_dump() — compact JSON artifact
# ---------------------------------------------------------------------------


def log_dump(
    session_id: Optional[str] = None,
    log_dir: Optional[str | Path] = None,
    *,
    label: Optional[str] = None,
    include_records: bool = False,
) -> Optional[Path]:
    """Write a JSON summary of buffered log records to disk.

    Only runs when the global logging mode is DEBUG (i.e. ``setup_logging()``
    was called with ``level <= logging.DEBUG``).  Returns ``None`` silently
    when not in debug mode.

    By default, writes only aggregated counts plus errors/warnings — keeping
    files small.  Pass ``include_records=True`` to embed the full record list.

    Parameters
    ----------
    session_id:      Filter to a single session.  ``None`` dumps everything.
    log_dir:         Output directory.  Defaults to LOG_DIR env / ``./logs``.
    label:           Optional tag appended to the filename.
    include_records: Embed full record list in the JSON.  Default ``False``.
    """
    if not _debug_mode:
        logging.getLogger(__name__).debug(
            "log_dump skipped — logging level is not DEBUG"
        )
        return None

    base_dir = (
        Path(log_dir) if log_dir else Path(os.getenv("LOG_DIR", str(_DEFAULT_LOG_DIR)))
    )
    out_dir = (base_dir / _SESSIONS_SUBDIR) if session_id else base_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    records = _buffer.for_session(session_id) if session_id else _buffer.all()

    by_level: Dict[str, int] = {}
    by_logger: Dict[str, int] = {}
    errors: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []

    analyst_steps: List[Dict[str, Any]] = []

    for r in records:
        lvl = r.get("level", "UNKNOWN")
        by_level[lvl] = by_level.get(lvl, 0) + 1
        lg = r.get("logger", "unknown")
        by_logger[lg] = by_logger.get(lg, 0) + 1
        if lvl in ("ERROR", "CRITICAL"):
            errors.append(r)
        elif lvl == "WARNING":
            warnings.append(r)
        elif lvl == "EVENT" and r.get("message") == "analyst_step_complete":
            analyst_steps.append(r)

    # Sort analyst steps by step_id for deterministic ordering in the dump.
    analyst_steps.sort(key=lambda r: r.get("step_id", 0))

    now = datetime.now(tz=timezone.utc)
    doc: Dict[str, Any] = {
        "dump_id": str(uuid.uuid4()),
        "created_at": now.strftime(_ISO_FMT),
        "session_id": session_id,
        "label": label,
        "record_count": len(records),
        "summary": {
            "by_level": dict(sorted(by_level.items())),
            "by_logger": dict(
                sorted(by_logger.items(), key=lambda kv: kv[1], reverse=True)
            ),
            "error_count": len(errors),
            "warning_count": len(warnings),
            "errors": errors,
            "warnings": warnings,
            "analyst_steps": analyst_steps,
        },
    }
    if include_records:
        doc["records"] = records

    slug = label or (f"session_{session_id[:8]}" if session_id else "all")
    path = out_dir / f"debug_{now.strftime('%Y-%m-%dT%H-%M-%S')}_{slug}.json"

    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2, default=str)

    logging.getLogger(__name__).info("log_dump → %s  (%d records)", path, len(records))
    return path.resolve()


# ---------------------------------------------------------------------------
# NDJSON file formatter — proper JSON serialization with json.dumps()
# ---------------------------------------------------------------------------


class _NdjsonFormatter(logging.Formatter):
    """Serialize each log record as a single valid JSON line.

    Replaces the old ``%(message)r`` format-string approach, which produced
    Python repr output (single-quoted strings) that was not valid JSON and
    caused ~72% of lines in the NDJSON file to fail ``json.loads()``.
    """

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        # logging.Formatter.formatTime() delegates to time.strftime(), which does
        # NOT support %f (microseconds) — that directive is datetime-only.
        # Override here so every NDJSON timestamp contains real sub-second
        # precision instead of the literal string "%f".
        dt = datetime.fromtimestamp(record.created, tz=timezone.utc)
        return dt.strftime(datefmt or _ISO_FMT)

    def format(self, record: logging.LogRecord) -> str:
        entry: Dict[str, Any] = {
            "ts": self.formatTime(record, _ISO_FMT),
            "level": record.levelname,
            "logger": record.name,
            "module": record.module,
            "line": record.lineno,
            "msg": record.getMessage(),
        }
        ctx = getattr(record, "_obs_ctx", {})
        if ctx:
            entry["ctx"] = ctx
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


# ---------------------------------------------------------------------------
# setup_logging() — call once at startup
# ---------------------------------------------------------------------------


def setup_logging(
    level: Optional[int | str] = None,
    log_dir: Optional[str | Path] = None,
    *,
    enable_file_handler: bool = True,
) -> None:
    """Configure root logger.  Respects LOG_LEVEL env / config log_level.

    - INFO  → compact single-line terminal output.
    - DEBUG → verbose multi-line output with structured context fields.

    Root logger is always set to DEBUG so the buffer and file handler capture
    everything; the StreamHandler is gated to the configured terminal level
    so the terminal stays quiet at INFO.
    """
    # ── resolve terminal level ─────────────────────────────────────────────
    if level is None:
        raw = os.getenv("LOG_LEVEL")
        if raw is not None:
            try:
                terminal_level = int(raw)
            except ValueError:
                terminal_level = getattr(logging, raw.upper(), logging.INFO)
        else:
            terminal_level = logging.INFO
    elif isinstance(level, str):
        terminal_level = getattr(logging, level.upper(), logging.INFO)
    else:
        terminal_level = int(level)

    debug_mode = terminal_level <= logging.DEBUG

    # ── persist debug mode so log_dump() and other helpers can check it ───
    global _debug_mode
    _debug_mode = debug_mode

    # ── root logger always DEBUG so buffer receives every record ───────────
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    existing = {type(h) for h in root.handlers}

    # ── terminal stream handler — gated to terminal_level ─────────────────
    if logging.StreamHandler not in existing:
        sh = logging.StreamHandler(sys.stdout)
        sh.setLevel(terminal_level)
        sh.setFormatter(_VerboseFormatter() if debug_mode else _CompactFormatter())
        root.addHandler(sh)

    # ── buffer handler — always DEBUG ─────────────────────────────────────
    if _BufferHandler not in existing:
        bh = _BufferHandler()
        bh.setLevel(logging.DEBUG)
        root.addHandler(bh)

    # ── rotating NDJSON file — always enabled at INFO+ level ───────────────
    # Written regardless of terminal log level so orchestrator events and
    # pipeline milestones are always persisted for post-run analysis.
    if enable_file_handler and logging.handlers.RotatingFileHandler not in existing:
        ld = (
            Path(log_dir)
            if log_dir
            else Path(os.getenv("LOG_DIR", str(_DEFAULT_LOG_DIR)))
        )
        ld.mkdir(parents=True, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(
            ld / "backend.ndjson",
            maxBytes=10 * 1024 * 1024,  # 10 MB per rotation
            backupCount=5,
            encoding="utf-8",
        )
        fh.setLevel(logging.INFO)
        fh.setFormatter(_NdjsonFormatter())
        root.addHandler(fh)

    # ── silence noisy third-party libs ────────────────────────────────────
    for lib in (
        "httpx",
        "httpcore",
        "urllib3",
        "asyncio",
        "websockets",
        "openai._base_client",
        "openai.resources",
    ):
        logging.getLogger(lib).setLevel(logging.WARNING)

    logging.getLogger(__name__).info(
        "Logging initialized — level=%s  debug=%s",
        logging.getLevelName(terminal_level),
        debug_mode,
    )
