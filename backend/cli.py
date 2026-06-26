"""
cli.py

Textual-based TUI for the Research Assistant.

Three modes are available:
    research      — full Orchestrator pipeline: plan → execute → synthesize
    chat          — single-turn Q&A via the Root agent
    self-optimize — SelfOptimizingAgent memory-analysis and method development

Usage
-----
    python cli.py                       # mode picker
    python cli.py research              # jump straight to research mode
    python cli.py research "my query"   # run a query directly
    python cli.py chat                  # jump to chat mode
    python cli.py self-optimize         # jump to self-optimize mode

Options
-------
    --backend  {openai,ollama,bedrock,azure,huggingface,gcp}
    --model    <model-name>
    --depth    {shallow,moderate,deep}  (default: shallow)
"""

from __future__ import annotations

import json
import os
import asyncio
import argparse
import sys
from pathlib import Path
from typing import Optional, List

from dotenv import load_dotenv

from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, ScrollableContainer, Vertical
from textual.css.query import NoMatches
from textual.screen import Screen
from textual.widgets import (
    Button,
    Footer,
    Header,
    Input,
    Label,
    Markdown,
    OptionList,
    ProgressBar,
    RichLog,
    Static,
)
from textual.widgets.option_list import Option

from art import text2art
from rich.text import Text

load_dotenv()

# ── Backend imports ──────────────────────────────────────────────────────────

from config import Config
from model_backend import create_model_backend
from mcp_client import create_mcp_registry, MCPServerRegistry
from orchestrator import Orchestrator, AgentPool
from optimization import SelfOptimizingAgent
from long_term_memory import AsyncLongTermMemory
from models import Message, ResearchPlan, ResearchStep, StepStatus
from session_store import SessionStore

# ═══════════════════════════════════════════════════════════════════════════════
# Local Document Discovery
# ═══════════════════════════════════════════════════════════════════════════════

# File extensions considered for local document scanning.
_SUPPORTED_DOC_EXTENSIONS = frozenset(
    {".pdf", ".docx", ".odt", ".txt", ".md", ".csv", ".json"}
)


def _extract_text_snippet(file_path: Path, max_chars: int = 500) -> str:
    """
    Extract a short text snippet from a file for relevance checking.

    For plain-text formats, reads the first *max_chars* directly.
    For binary formats (PDF, DOCX, ODT), attempts extraction via optional
    libraries — falls back to empty string if unavailable.
    """
    suffix = file_path.suffix.lower()
    try:
        if suffix in (".txt", ".md", ".csv", ".json"):
            return file_path.read_text(encoding="utf-8", errors="replace")[:max_chars]
        elif suffix == ".pdf":
            try:
                import pypdf
            except ImportError:
                return ""
            reader = pypdf.PdfReader(str(file_path))
            text = ""
            for page in reader.pages:
                text += page.extract_text() or ""
                if len(text) >= max_chars:
                    break
            return text[:max_chars]
        elif suffix == ".docx":
            try:
                import docx
            except ImportError:
                return ""
            doc = docx.Document(str(file_path))
            text = ""
            for para in doc.paragraphs:
                text += para.text + "\n"
                if len(text) >= max_chars:
                    break
            return text[:max_chars]
        elif suffix == ".odt":
            try:
                from odf import text as odf_text, teletype
                from odf.opendocument import load
            except ImportError:
                return ""
            doc = load(str(file_path))
            paragraphs = [
                teletype.extractText(p) for p in doc.getElementsByType(odf_text.P)
            ]
            return "\n".join(paragraphs)[:max_chars]
    except Exception:
        return ""
    return ""


def _read_full_document(file_path: Path, max_chars: int = 10000) -> str:
    """
    Read the full text content of a document (up to *max_chars*).

    Used after user approval to inject document content into the research
    pipeline.
    """
    suffix = file_path.suffix.lower()
    try:
        if suffix in (".txt", ".md", ".csv", ".json"):
            content = file_path.read_text(encoding="utf-8", errors="replace")
        elif suffix == ".pdf":
            try:
                import pypdf
            except ImportError:
                return ""
            reader = pypdf.PdfReader(str(file_path))
            content = "\n\n".join(p.extract_text() or "" for p in reader.pages)
        elif suffix == ".docx":
            try:
                import docx
            except ImportError:
                return ""
            doc = docx.Document(str(file_path))
            content = "\n\n".join(
                para.text for para in doc.paragraphs if para.text.strip()
            )
        elif suffix == ".odt":
            try:
                from odf import text as odf_text, teletype
                from odf.opendocument import load
            except ImportError:
                return ""
            doc = load(str(file_path))
            paragraphs = [
                teletype.extractText(p) for p in doc.getElementsByType(odf_text.P)
            ]
            content = "\n\n".join(p for p in paragraphs if p.strip())
        else:
            return ""
    except Exception:
        return ""

    if len(content) > max_chars:
        content = content[:max_chars] + "\n[truncated]"
    return content


def scan_local_documents(directory: str, query: str, max_files: int = 50) -> List[dict]:
    """
    Scan *directory* recursively for supported documents and return those
    whose filename or content snippet is relevant to *query*.

    Returns a list of dicts: {"path": str, "name": str, "size_bytes": int,
    "match_reason": str}.
    """
    root = Path(directory).resolve()
    if not root.is_dir():
        return []

    # Tokenize query into lowercase keywords for matching
    query_terms = [t.lower() for t in query.split() if len(t) >= 3]
    if not query_terms:
        # No meaningful terms — return all supported files
        query_terms = []

    results: List[dict] = []
    for file_path in root.rglob("*"):
        if not file_path.is_file():
            continue
        if file_path.suffix.lower() not in _SUPPORTED_DOC_EXTENSIONS:
            continue
        if len(results) >= max_files:
            break

        name_lower = file_path.stem.lower()
        match_reasons: List[str] = []

        # Check filename match
        for term in query_terms:
            if term in name_lower:
                match_reasons.append(f"filename contains '{term}'")

        # Check content snippet match (only if no filename match yet)
        if not match_reasons and query_terms:
            snippet = _extract_text_snippet(file_path)
            snippet_lower = snippet.lower()
            for term in query_terms:
                if term in snippet_lower:
                    match_reasons.append(f"content contains '{term}'")
                    break  # one content match is enough

        # If no query terms provided, include all files
        if not query_terms:
            match_reasons.append("supported document type")

        if match_reasons:
            results.append(
                {
                    "path": str(file_path),
                    "name": file_path.name,
                    "size_bytes": file_path.stat().st_size,
                    "match_reason": "; ".join(match_reasons),
                }
            )

    return results


# ═══════════════════════════════════════════════════════════════════════════════
# CSS
# ═══════════════════════════════════════════════════════════════════════════════

APP_CSS = """
Screen {
    background: $surface;
}

/* ── Home Screen ──────────────────────────────────────────────────── */
#home-outer {
    align: center middle;
    height: 100%;
}

#home-card {
    width: 90;
    max-width: 100%;
    height: auto;
    padding: 1 2;
    background: $panel;
    border: round $primary;
}

#home-banner {
    text-align: center;
    color: $primary;
    margin-bottom: 0;
}

#home-version {
    text-align: center;
    color: $text-muted;
    margin-bottom: 1;
}

#home-status {
    text-align: center;
    color: $success;
    margin-bottom: 1;
}

#home-commands {
    color: $text-muted;
    margin-bottom: 1;
    padding: 1 2;
}

#home-hint {
    text-align: center;
    color: $text-muted;
    text-style: italic;
    margin-bottom: 1;
}

#home-input-bar {
    height: 3;
    layout: horizontal;
    dock: bottom;
}

#home-input {
    width: 1fr;
}

#command-dropdown {
    max-height: 12;
    height: auto;
    border: round $primary-darken-2;
    background: $panel;
    display: none;
    margin-top: 0;
    padding: 0 1;
}

#command-dropdown > .option-list--option-highlighted {
    background: $primary-darken-1;
}

#home-feedback {
    text-align: center;
    color: $warning;
    height: auto;
    margin-top: 1;
}

/* ── Models Screen ───────────────────────────────────────────────── */
#models-panel {
    height: auto;
    max-height: 12;
    border: round $primary-darken-2;
    padding: 1;
    margin-bottom: 1;
}

#models-current {
    text-style: bold;
    color: $success;
    margin-bottom: 1;
}

.models-section {
    color: $primary;
    text-style: bold;
    margin-top: 1;
    margin-bottom: 0;
}

#models-backend-list {
    height: auto;
    max-height: 10;
    margin-bottom: 1;
}

.backend-item {
    padding: 0 1;
    margin-bottom: 0;
}

.backend-item.selected {
    color: $success;
    text-style: bold;
}

#models-form {
    height: auto;
    border: round $primary-darken-2;
    padding: 1;
    margin-bottom: 1;
}

.models-label {
    margin-bottom: 0;
    color: $text-muted;
}

#models-input-model {
    margin-bottom: 1;
}

#models-input-api-key {
    margin-bottom: 1;
}

#models-input-base-url {
    margin-bottom: 1;
}

#models-actions {
    layout: horizontal;
    height: 3;
}

#btn-models-apply {
    width: 20;
    background: $success;
    margin-right: 1;
}

#btn-models-cancel {
    width: 20;
    background: $error;
}

/* ── MCP Screen ──────────────────────────────────────────────────── */
#mcp-server-list {
    height: 1fr;
    border: round $primary-darken-2;
    padding: 1;
    margin-bottom: 1;
}

.mcp-server-entry {
    padding: 0 1;
    margin-bottom: 0;
}

.mcp-server-entry.connected { color: $success; }
.mcp-server-entry.disconnected { color: $error; }

#mcp-tools-panel {
    height: 1fr;
    border: round $success;
    padding: 1;
    margin-bottom: 1;
}

#mcp-tools-title {
    text-style: bold;
    color: $success;
    margin-bottom: 1;
}

#mcp-add-form {
    height: auto;
    border: round $warning;
    padding: 1;
    margin-bottom: 1;
    display: none;
}

.mcp-label {
    margin-bottom: 0;
    color: $text-muted;
}

#mcp-input-name {
    margin-bottom: 1;
}

#mcp-input-transport {
    margin-bottom: 1;
}

#mcp-input-url {
    margin-bottom: 1;
}

#mcp-add-actions {
    layout: horizontal;
    height: 3;
}

#btn-mcp-add {
    width: 20;
    background: $warning;
    margin-right: 1;
}

#btn-mcp-show-add {
    width: 26;
    background: $warning;
    margin-bottom: 1;
}

#btn-mcp-remove {
    width: 26;
    background: $error;
    margin-bottom: 1;
}

/* ── Local Documents Approval ────────────────────────────────────── */
#docs-panel {
    height: auto;
    max-height: 12;
    border: round $warning;
    padding: 1;
    margin-bottom: 1;
    display: none;
}

#docs-panel-title {
    text-style: bold;
    color: $warning;
    margin-bottom: 1;
}

#docs-actions {
    layout: horizontal;
    height: 3;
    margin-bottom: 1;
    display: none;
}

#btn-approve-docs {
    width: 18;
    background: $success;
    margin-right: 1;
}

#btn-deny-docs {
    width: 18;
    background: $error;
}

/* ── Sessions Screen ─────────────────────────────────────────────── */
#sessions-list {
    height: 1fr;
    border: round $primary-darken-2;
    padding: 1;
    margin-bottom: 1;
}

.session-entry {
    padding: 0 1;
    margin-bottom: 0;
}

.session-entry.session-complete { color: $success; }
.session-entry.session-active { color: $warning; text-style: bold; }
.session-entry.session-error { color: $error; }

#sessions-detail {
    height: auto;
    max-height: 10;
    border: round $success;
    padding: 1;
    margin-bottom: 1;
    display: none;
}

#sessions-actions {
    layout: horizontal;
    height: 3;
    margin-bottom: 1;
}

#btn-session-new {
    width: 20;
    background: $primary;
    margin-right: 1;
}

#btn-session-resume {
    width: 20;
    background: $success;
    margin-right: 1;
    display: none;
}

/* ── Shared layout ───────────────────────────────────────────────── */
#main-layout {
    layout: horizontal;
    height: 1fr;
}

#sidebar {
    width: 30;
    min-width: 28;
    border-right: solid $primary-darken-2;
    padding: 1;
    background: $panel;
}

#sidebar-title {
    text-style: bold;
    color: $primary;
    margin-bottom: 1;
}

.sidebar-section {
    color: $text-muted;
    text-style: italic;
    margin-top: 1;
    margin-bottom: 0;
}

.sidebar-item {
    padding-left: 1;
    margin-bottom: 0;
}

.sidebar-item.done    { color: $success; }
.sidebar-item.active  { color: $warning; text-style: bold; }
.sidebar-item.error   { color: $error; }
.sidebar-item.pending { color: $text-muted; }

#content-area {
    width: 1fr;
    padding: 1 2;
}

/* ── Research mode ───────────────────────────────────────────────── */
#research-input-bar {
    height: 3;
    layout: horizontal;
    margin-bottom: 1;
}

#query-input {
    width: 1fr;
}

#btn-run-research {
    width: 16;
    margin-left: 1;
    background: $primary;
}

#plan-panel {
    height: auto;
    max-height: 18;
    border: round $primary-darken-2;
    padding: 1;
    margin-bottom: 1;
}

#plan-panel-title {
    text-style: bold;
    color: $primary;
    margin-bottom: 1;
}

#plan-actions {
    layout: horizontal;
    height: 3;
    margin-bottom: 1;
    display: none;
}

#btn-approve-plan {
    width: 14;
    background: $success;
    margin-right: 1;
}

#btn-modify-plan {
    width: 14;
    background: $warning;
    margin-right: 1;
}

#btn-deny-plan {
    width: 14;
    background: $error;
}

#modify-feedback {
    height: 3;
    layout: horizontal;
    margin-bottom: 1;
    display: none;
}

#modify-input {
    width: 1fr;
}

#btn-send-feedback {
    width: 18;
    margin-left: 1;
    background: $warning;
}

#progress-bar {
    margin-bottom: 1;
    display: none;
}

#log-panel {
    height: 1fr;
    border: round $primary-darken-2;
    padding: 1;
    margin-bottom: 1;
}

#report-panel {
    height: 1fr;
    border: round $success;
    padding: 1;
    display: none;
}

#report-title {
    text-style: bold;
    color: $success;
    margin-bottom: 1;
}

/* ── Chat mode ───────────────────────────────────────────────────── */
#chat-history {
    height: 1fr;
    border: round $primary-darken-2;
    padding: 1;
    margin-bottom: 1;
}

#chat-input-bar {
    height: 3;
    layout: horizontal;
}

#chat-input {
    width: 1fr;
}

#btn-send-chat {
    width: 12;
    margin-left: 1;
    background: $success;
}

/* ── Self-Optimize mode ──────────────────────────────────────────── */
#btn-start-optimize {
    width: 30;
    background: $warning;
    margin-bottom: 1;
}

#optimize-log {
    height: 1fr;
    border: round $warning;
    padding: 1;
    margin-bottom: 1;
}

#insights-panel {
    height: 1fr;
    border: round $success;
    padding: 1;
    display: none;
}

#insights-title {
    text-style: bold;
    color: $success;
    margin-bottom: 1;
}
"""


# ═══════════════════════════════════════════════════════════════════════════════
# Home Screen (replaces Mode Picker)
# ═══════════════════════════════════════════════════════════════════════════════

_ASCII_BANNER = text2art("Deep Research", font="small")

_COMMAND_HELP = """\
  [bold cyan]/research[/] [dim]<query>[/dim]  — Deep multi-agent investigation
  [bold cyan]/chat[/]                — Interactive Q&A with the agent
  [bold cyan]/optimize[/]            — Analyse memories & evolve methods
  [bold cyan]/docs[/] [dim]<path>[/dim]       — Set local documents directory
  [bold cyan]/models[/]              — View / change the model backend
  [bold cyan]/mcp[/]                 — View / manage MCP servers
  [bold cyan]/sessions[/]            — Browse & resume past sessions
  [bold cyan]/new[/]                 — Start a fresh session
  [bold cyan]/help[/]                — Show this command list
  [bold cyan]/exit[/]                — Quit the application\
"""


class HomeScreen(Screen):
    """ASCII-art home with slash-command input, inspired by Copilot / Claude Code."""

    BINDINGS = [
        Binding("1", "shortcut_research", "Research", show=False),
        Binding("2", "shortcut_chat", "Chat", show=False),
        Binding("3", "shortcut_optimize", "Self-Optimize", show=False),
        Binding("q", "quit_app", "Quit"),
    ]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Container(id="home-outer"):
            with Vertical(id="home-card"):
                yield Static(_ASCII_BANNER, id="home-banner")
                yield Label("v1.0.0", id="home-version")
                yield Label(self._status_line(), id="home-status")
                yield Static(_COMMAND_HELP, id="home-commands", markup=True)
                yield Label(
                    "Type a /command below, or press 1 / 2 / 3 for quick access",
                    id="home-hint",
                )
                yield Label("", id="home-feedback")
                with Horizontal(id="home-input-bar"):
                    yield Input(
                        placeholder="Enter a /command…",
                        id="home-input",
                    )
                yield OptionList(id="command-dropdown")
        yield Footer()

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _status_line(self) -> str:
        cfg = self.app.config
        backend = cfg.model_backend
        model_env_map = {
            "openai": cfg.openai_model,
            "ollama": cfg.ollama_model,
            "azure": cfg.azure_deployment_name,
            "bedrock": cfg.aws_model,
            "aws": cfg.aws_model,
            "gcp": cfg.gcp_model,
            "anthropic": cfg.anthropic_model,
            "huggingface": cfg.huggingface_model,
        }
        model = model_env_map.get(backend, "unknown")
        n_servers = len(self.app.mcp_registry.server_configs)
        docs_dir = self.app.docs_dir
        status = f"Backend: {backend}  |  Model: {model}  |  MCP servers: {n_servers}"
        if docs_dir:
            status += f"  |  Docs: {Path(docs_dir).name}/"
        return status

    def _show_feedback(self, text: str) -> None:
        self.query_one("#home-feedback", Label).update(text)

    def _refresh_status(self) -> None:
        self.query_one("#home-status", Label).update(self._status_line())

    # ── Command dropdown (autocomplete) ───────────────────────────────────────

    _ALL_COMMANDS: list[tuple[str, str]] = [
        ("/research", "Deep multi-agent investigation"),
        ("/chat", "Interactive Q&A with the agent"),
        ("/optimize", "Analyse memories & evolve methods"),
        ("/docs", "Set local documents directory"),
        ("/models", "View / change the model backend"),
        ("/mcp", "View / manage MCP servers"),
        ("/sessions", "Browse & resume past sessions"),
        ("/new", "Start a fresh session"),
        ("/help", "Show command list"),
        ("/exit", "Quit the application"),
    ]

    @on(Input.Changed, "#home-input")
    def _filter_dropdown(self, event: Input.Changed) -> None:
        dropdown = self.query_one("#command-dropdown", OptionList)
        value = event.value.strip()
        if not value.startswith("/"):
            dropdown.styles.display = "none"
            return
        dropdown.clear_options()
        prefix = value.lower()
        matches = [
            (cmd, desc) for cmd, desc in self._ALL_COMMANDS if cmd.startswith(prefix)
        ]
        if matches:
            for cmd, desc in matches:
                dropdown.add_option(Option(f"{cmd}  — {desc}", id=cmd))
            dropdown.styles.display = "block"
        else:
            dropdown.styles.display = "none"

    @on(OptionList.OptionSelected, "#command-dropdown")
    def _dropdown_selected(self, event: OptionList.OptionSelected) -> None:
        cmd = event.option_id
        inp = self.query_one("#home-input", Input)
        inp.value = cmd
        self.query_one("#command-dropdown", OptionList).styles.display = "none"
        inp.focus()

    # ── Slash-command dispatcher ──────────────────────────────────────────────

    @on(Input.Submitted, "#home-input")
    def handle_command(self, event: Input.Submitted) -> None:
        raw = event.value.strip()
        inp = self.query_one("#home-input", Input)
        inp.value = ""
        self.query_one("#command-dropdown", OptionList).styles.display = "none"
        if not raw:
            return

        if not raw.startswith("/"):
            self._show_feedback("Type /help for commands, or /chat to start chatting.")
            return

        parts = raw.split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""

        dispatch = {
            "/exit": lambda: self.app.exit(),
            "/quit": lambda: self.app.exit(),
            "/help": lambda: self._show_help(),
            "/research": lambda: self._open_research(arg),
            "/chat": lambda: self._open_chat(),
            "/optimize": lambda: self._open_optimize(),
            "/docs": lambda: self._set_docs_dir(arg),
            "/models": lambda: self._open_models(),
            "/mcp": lambda: self._open_mcp(),
            "/sessions": lambda: self._open_sessions(),
            "/new": lambda: self._new_session(),
        }

        handler = dispatch.get(cmd)
        if handler:
            handler()
        else:
            self._show_feedback(f"Unknown command: {cmd}  — type /help")

    def _show_help(self) -> None:
        self._show_feedback("")
        self.query_one("#home-commands", Static).update(_COMMAND_HELP)

    def _set_docs_dir(self, path: str = "") -> None:
        if not path:
            current = self.app.docs_dir or "(not set)"
            self._show_feedback(f"Current docs dir: {current}. Usage: /docs <path>")
            return
        resolved = Path(path).expanduser().resolve()
        if not resolved.is_dir():
            self._show_feedback(f"Not a valid directory: {resolved}")
            return
        self.app.docs_dir = str(resolved)
        self._show_feedback(f"✔ Docs directory set: {resolved}")

    def _open_research(self, query: str = "") -> None:
        screen = ResearchScreen(self.app.orchestrator, self.app.config)
        self.app.push_screen(screen)
        if query:
            self.app.call_after_refresh(lambda: self.app._auto_run_query(screen, query))

    def _open_chat(self) -> None:
        self.app.push_screen(ChatScreen(self.app.orchestrator, self.app.config))

    def _open_optimize(self) -> None:
        self.app.push_screen(
            SelfOptimizeScreen(
                self.app.config, self.app.mcp_registry, self.app.long_term_memory
            )
        )

    def _open_models(self) -> None:
        self.app.push_screen(ModelsScreen())

    def _open_mcp(self) -> None:
        self.app.push_screen(MCPScreen())

    def _open_sessions(self) -> None:
        self.app.push_screen(SessionsScreen())

    def _new_session(self) -> None:
        self._show_feedback("New session started — state reset.")
        # Rebuild orchestrator with a clean slate
        self.app.call_after_refresh(self._rebuild_orchestrator)

    @work(exclusive=True, thread=False)
    async def _rebuild_orchestrator(self) -> None:
        cfg = self.app.config
        model_backend = create_model_backend(cfg)
        agent_pool = AgentPool.from_single_backend(model_backend)
        await agent_pool.async_init(self.app.mcp_registry)
        self.app.orchestrator = Orchestrator(
            config=cfg,
            mcp_registry=self.app.mcp_registry,
            agent_pool=agent_pool,
            long_term_memory=self.app.long_term_memory,
            research_depth="shallow",
        )
        self._show_feedback("✔ New session ready.")

    # ── Keyboard shortcuts (legacy 1/2/3) ─────────────────────────────────────

    def action_shortcut_research(self) -> None:
        self._open_research()

    def action_shortcut_chat(self) -> None:
        self._open_chat()

    def action_shortcut_optimize(self) -> None:
        self._open_optimize()

    def action_quit_app(self) -> None:
        self.app.exit()

    def on_screen_resume(self) -> None:
        """Refresh status line when returning from a sub-screen."""
        self._refresh_status()


# ═══════════════════════════════════════════════════════════════════════════════
# Research Screen
# ═══════════════════════════════════════════════════════════════════════════════


class ResearchScreen(Screen):
    """
    Full research pipeline TUI: query → plan review → execute → report.

    Layout
    ------
    Left sidebar  : live plan step tracker
    Right content :
        • Query input bar
        • Scrollable plan panel with Approve / Modify / Deny controls
        • Progress bar
        • Streaming agent log (colour-coded by sub-agent)
        • Final report viewer (Markdown, hidden until complete)
    """

    BINDINGS = [
        Binding("escape", "go_back", "← Back"),
        Binding("ctrl+s", "save_report", "Save Report"),
    ]

    def __init__(self, orchestrator: Orchestrator, config: Config) -> None:
        super().__init__()
        self.orchestrator = orchestrator
        self.config = config
        self._plan: Optional[ResearchPlan] = None
        self._report: str = ""
        self._step_widgets: dict[int, Label] = {}
        self._busy = False
        # Local document discovery state
        self._discovered_docs: List[dict] = []
        self._docs_approved: Optional[bool] = None
        self._docs_event: Optional[asyncio.Event] = None

    # ── Layout ───────────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="main-layout"):
            # ── Sidebar ──────────────────────────────────────────────────────
            with ScrollableContainer(id="sidebar"):
                yield Label("📋 Research Plan", id="sidebar-title")
                yield Label(
                    "No plan yet.", id="sidebar-no-plan", classes="sidebar-item pending"
                )

            # ── Content ──────────────────────────────────────────────────────
            with Vertical(id="content-area"):
                with Horizontal(id="research-input-bar"):
                    yield Input(
                        placeholder="Enter your research query…", id="query-input"
                    )
                    yield Button("▶  Research", id="btn-run-research")

                with ScrollableContainer(id="docs-panel"):
                    yield Label("📂 Local Documents Found", id="docs-panel-title")
                    yield Static("", id="docs-body")

                with Horizontal(id="docs-actions"):
                    yield Button("✔  Use Documents", id="btn-approve-docs")
                    yield Button("✘  Skip", id="btn-deny-docs")

                with ScrollableContainer(id="plan-panel"):
                    yield Label("Research Plan", id="plan-panel-title")
                    yield Static(
                        "Submit a query above to generate a research plan.",
                        id="plan-body",
                    )

                with Horizontal(id="plan-actions"):
                    yield Button("✔  Approve", id="btn-approve-plan")
                    yield Button("✏  Modify", id="btn-modify-plan")
                    yield Button("✘  Deny", id="btn-deny-plan")

                with Horizontal(id="modify-feedback"):
                    yield Input(
                        placeholder="Describe changes to the plan…",
                        id="modify-input",
                    )
                    yield Button("Send Feedback", id="btn-send-feedback")

                yield ProgressBar(total=100, id="progress-bar", show_eta=False)

                with ScrollableContainer(id="log-panel"):
                    yield RichLog(id="research-log", highlight=True, markup=True)

                with ScrollableContainer(id="report-panel"):
                    yield Label("📄 Research Report", id="report-title")
                    yield Markdown("", id="report-markdown")

        yield Footer()

    # ── UI helpers ────────────────────────────────────────────────────────────

    def _log(self, message: str, style: str = "") -> None:
        log = self.query_one("#research-log", RichLog)
        if style:
            log.write(Text(message, style=style))
        else:
            log.write(message)

    def _update_sidebar(self, plan: ResearchPlan) -> None:
        sidebar = self.query_one("#sidebar", ScrollableContainer)
        try:
            sidebar.query_one("#sidebar-no-plan").remove()
        except NoMatches:
            pass
        for w in list(self._step_widgets.values()):
            try:
                w.remove()
            except Exception:
                pass
        self._step_widgets = {}

        goal_lbl = Label(
            f"Goal: {plan.goal[:40]}{'…' if len(plan.goal) > 40 else ''}",
            classes="sidebar-item pending",
        )
        sidebar.mount(goal_lbl)

        steps_hdr = Label("Steps:", classes="sidebar-section")
        sidebar.mount(steps_hdr)

        for step in plan.steps:
            short = step.description
            if len(short) > 24:
                short = short[:24] + "…"
            lbl = Label(f"  {step.id}. {short}", classes="sidebar-item pending")
            self._step_widgets[step.id] = lbl
            sidebar.mount(lbl)

    def _set_step_status(self, step_id: int, status: str) -> None:
        lbl = self._step_widgets.get(step_id)
        if lbl:
            lbl.remove_class("active", "done", "error", "pending")
            lbl.add_class(status)

    def _show_plan_actions(self, visible: bool) -> None:
        self.query_one("#plan-actions").display = visible

    def _show_modify_bar(self, visible: bool) -> None:
        self.query_one("#modify-feedback").display = visible

    def _set_progress(self, pct: int) -> None:
        bar = self.query_one("#progress-bar", ProgressBar)
        bar.display = True
        bar.update(progress=pct)

    def _show_report(self, document: str) -> None:
        self._report = document
        self.query_one("#report-panel").display = True
        self.query_one("#report-markdown", Markdown).update(document)
        self._log("[bold green]✔ Report complete — scroll down to read.[/bold green]")

    # ── Buttons ───────────────────────────────────────────────────────────────

    @on(Button.Pressed, "#btn-run-research")
    def handle_run_research(self) -> None:
        if self._busy:
            return
        inp = self.query_one("#query-input", Input)
        query = inp.value.strip()
        if not query:
            self._log("[red]Please enter a research query.[/red]")
            return
        self._busy = True
        self.query_one("#btn-run-research").disabled = True
        self._show_plan_actions(False)
        self._show_modify_bar(False)
        self.query_one("#report-panel").display = False
        self._run_plan(query)

    @on(Input.Submitted, "#query-input")
    def handle_query_enter(self, _event) -> None:
        self.handle_run_research()

    @on(Button.Pressed, "#btn-approve-plan")
    def handle_approve(self) -> None:
        self._show_plan_actions(False)
        self._log("[bold green]Plan approved — executing…[/bold green]")
        self._run_execute()

    @on(Button.Pressed, "#btn-modify-plan")
    def handle_modify(self) -> None:
        self._show_modify_bar(True)

    @on(Button.Pressed, "#btn-send-feedback")
    def handle_send_feedback(self) -> None:
        inp = self.query_one("#modify-input", Input)
        feedback = inp.value.strip()
        if not feedback:
            return
        inp.value = ""
        self._show_modify_bar(False)
        self._show_plan_actions(False)
        self._log(f"[yellow]Requesting plan modification: {feedback}[/yellow]")
        self._run_modify_plan(feedback)

    @on(Input.Submitted, "#modify-input")
    def handle_modify_enter(self, _event) -> None:
        self.handle_send_feedback()

    @on(Button.Pressed, "#btn-deny-plan")
    def handle_deny(self) -> None:
        self._show_plan_actions(False)
        self._run_deny_plan()

    @on(Button.Pressed, "#btn-approve-docs")
    def handle_approve_docs(self) -> None:
        self._docs_approved = True
        self._show_docs_panel(False)
        self._log(
            "[bold green]✔ Local documents approved — will include in research.[/bold green]"
        )
        if self._docs_event:
            self._docs_event.set()

    @on(Button.Pressed, "#btn-deny-docs")
    def handle_deny_docs(self) -> None:
        self._docs_approved = False
        self._show_docs_panel(False)
        self._log("[dim]Local documents skipped.[/dim]")
        if self._docs_event:
            self._docs_event.set()

    def _show_docs_panel(self, visible: bool) -> None:
        self.query_one("#docs-panel").display = visible
        self.query_one("#docs-actions").display = visible

    # ── Workers ───────────────────────────────────────────────────────────────

    @work(exclusive=True, thread=False)
    async def _run_plan(self, query: str) -> None:
        self._log(f"[bold blue]🔍 Research query:[/bold blue] {query}")
        self._set_progress(5)

        # ── Local document discovery ──────────────────────────────────────
        local_documents: Optional[List[dict]] = None
        docs_dir = getattr(self.app, "docs_dir", None)
        if docs_dir:
            self._log(f"[dim]Scanning local documents in {docs_dir}…[/dim]")
            max_files = self.config.max_local_docs
            discovered = scan_local_documents(docs_dir, query, max_files=max_files)

            if discovered:
                self._discovered_docs = discovered
                # Show discovered documents to user
                lines = [f"Found **{len(discovered)}** relevant document(s):\n"]
                for doc in discovered[:20]:  # Show at most 20 in the panel
                    size_kb = doc["size_bytes"] / 1024
                    lines.append(
                        f"  • {doc['name']} ({size_kb:.1f} KB) — {doc['match_reason']}"
                    )
                if len(discovered) > 20:
                    lines.append(f"  … and {len(discovered) - 20} more")
                lines.append("\nAllow the agent to read and use these during research?")
                self.query_one("#docs-body", Static).update("\n".join(lines))
                self._show_docs_panel(True)

                # Wait for user response
                self._docs_event = asyncio.Event()
                self._docs_approved = None
                await self._docs_event.wait()
                self._docs_event = None

                if self._docs_approved:
                    # Read full contents of approved documents
                    max_chars = self.config.max_local_doc_chars
                    local_documents = []
                    for doc_info in discovered:
                        content = _read_full_document(
                            Path(doc_info["path"]), max_chars=max_chars
                        )
                        if content.strip():
                            local_documents.append(
                                {"name": doc_info["name"], "content": content}
                            )
                    self._log(
                        f"[green]Loaded {len(local_documents)} document(s) as context.[/green]"
                    )
            else:
                self._log("[dim]No relevant local documents found.[/dim]")

        try:
            async for msg in self.orchestrator.plan(
                query, local_documents=local_documents
            ):
                if msg.type == "status":
                    self._log(f"[dim]{msg.message}[/dim]")

                elif msg.type == "plan":
                    plan_data = msg.plan or {}
                    steps = [
                        ResearchStep(
                            id=s["id"],
                            name=s.get("name", f"Step {s['id']}"),
                            description=s["description"],
                            status=StepStatus.PENDING,
                        )
                        for s in plan_data.get("steps", [])
                    ]
                    self._plan = ResearchPlan(
                        goal=plan_data.get("goal", ""), steps=steps
                    )
                    self._plan.id = plan_data.get("id", self._plan.id)

                    # Render plan in sidebar and main panel
                    self._update_sidebar(self._plan)
                    md_lines = [f"**Goal:** {self._plan.goal}\n"]
                    for s in self._plan.steps:
                        md_lines.append(f"- **Step {s.id}:** {s.description}")
                    self.query_one("#plan-body", Static).update("\n".join(md_lines))

                    self._log(
                        "[bold green]📋 Plan generated — please review and approve, modify, or deny.[/bold green]"
                    )
                    self._set_progress(15)
                    self._show_plan_actions(True)

                elif msg.type == "error":
                    self._log(f"[bold red]Error: {msg.message}[/bold red]")

        except Exception as exc:
            self._log(f"[bold red]Planning failed: {exc}[/bold red]")
        finally:
            self._busy = False
            # Keep the run button disabled while a plan is awaiting user approval.
            # The approve / deny / modify handlers re-enable it when appropriate.
            if self._plan is None:
                self.query_one("#btn-run-research").disabled = False

    @work(exclusive=True, thread=False)
    async def _run_modify_plan(self, feedback: str) -> None:
        if not self._plan:
            return
        self._busy = True
        try:
            response = await self.orchestrator.modify_plan(self._plan.id, feedback)

            if response.type == "plan":
                plan_data = response.plan or {}
                steps = [
                    ResearchStep(
                        id=s["id"],
                        name=s.get("name", f"Step {s['id']}"),
                        description=s["description"],
                        status=StepStatus.PENDING,
                    )
                    for s in plan_data.get("steps", [])
                ]
                self._plan = ResearchPlan(goal=plan_data.get("goal", ""), steps=steps)
                self._plan.id = plan_data.get("id", self._plan.id)
                self._update_sidebar(self._plan)
                md_lines = [f"**Goal:** {self._plan.goal}\n"]
                for s in self._plan.steps:
                    md_lines.append(f"- **Step {s.id}:** {s.description}")
                self.query_one("#plan-body", Static).update("\n".join(md_lines))
                self._log(f"[yellow]✏ Plan updated:[/yellow] {response.message}")
            else:
                self._log(f"[red]Modification failed: {response.message}[/red]")

        except Exception as exc:
            self._log(f"[bold red]Modify plan failed: {exc}[/bold red]")
        finally:
            self._busy = False
            self._show_plan_actions(True)

    @work(exclusive=True, thread=False)
    async def _run_deny_plan(self) -> None:
        if not self._plan:
            return
        self._busy = True
        try:
            response = await self.orchestrator.deny_plan(self._plan.id)
            self._log(f"[red]✘ Plan denied.[/red] {response.message}")
            self._plan = None
            self.query_one("#plan-body", Static).update(
                "Plan denied. Submit a new query to start over."
            )
            for w in list(self._step_widgets.values()):
                try:
                    await w.remove()
                except Exception:
                    pass
            self._step_widgets = {}
        except Exception as exc:
            self._log(f"[bold red]Deny plan failed: {exc}[/bold red]")
        finally:
            self._busy = False
            self.query_one("#btn-run-research").disabled = False

    @work(exclusive=True, thread=False)
    async def _run_execute(self) -> None:
        if not self._plan:
            return
        self._busy = True
        total = len(self._plan.steps)
        step_pct = 60 // max(total, 1)
        progress = 15

        try:
            async for msg in self.orchestrator.execute():
                t = msg.type
                text = msg.message

                if t == "status":
                    if "[Search]" in text:
                        self._log(f"[cyan]{text}[/cyan]")
                    elif "[Analyst]" in text:
                        self._log(f"[blue]{text}[/blue]")
                    elif "[QA]" in text or "contradiction" in text.lower():
                        self._log(f"[yellow]{text}[/yellow]")
                    elif "retry" in text.lower():
                        self._log(f"[orange1]{text}[/orange1]")
                    else:
                        self._log(f"[dim]{text}[/dim]")

                elif t == "step_start":
                    step_data = (msg.data or {}).get("step", {})
                    sid = step_data.get("id")
                    if sid is not None:
                        self._set_step_status(sid, "active")
                    self._log(
                        f"[bold]→ Step {sid}:[/bold] {step_data.get('description', '')}"
                    )

                elif t == "step_complete":
                    step_data = (msg.data or {}).get("step", {})
                    sid = step_data.get("id")
                    if sid is not None:
                        self._set_step_status(sid, "done")
                    progress = min(progress + step_pct, 75)
                    self._set_progress(progress)
                    self._log(f"[green]✔ Step {sid} complete[/green]")

                elif t == "step_failed":
                    step_data = (msg.data or {}).get("step", {})
                    sid = step_data.get("id")
                    if sid is not None:
                        self._set_step_status(sid, "error")
                    self._log(f"[red]✘ Step {sid} failed: {text}[/red]")

                elif t == "research_complete":
                    self._set_progress(80)
                    self._log(
                        "[bold green]✔ All steps complete — synthesising report…[/bold green]"
                    )

                elif t == "error":
                    self._log(f"[red]Error: {text}[/red]")

            # Synthesise
            self._set_progress(85)
            async for msg in self.orchestrator.synthesize():
                if msg.type == "status":
                    self._log(f"[dim]{msg.message}[/dim]")
                elif msg.type == "section_draft":
                    data = msg.data or {}
                    idx = data.get("section_index", "?")
                    total = data.get("total_sections", "?")
                    title = data.get("section_title", "")
                    pct = (
                        int(85 + (int(idx) / max(int(total), 1)) * 12)
                        if str(idx).isdigit() and str(total).isdigit()
                        else 90
                    )
                    self._set_progress(pct)
                    self._log(f"[cyan]  § Section {idx}/{total}: {title}[/cyan]")
                elif msg.type == "report":
                    document = (msg.data or {}).get("document", "")
                    self._set_progress(100)
                    self._show_report(document)
                elif msg.type == "error":
                    self._log(f"[red]{msg.message}[/red]")

        except Exception as exc:
            self._log(f"[bold red]Unexpected error during execution: {exc}[/bold red]")
        finally:
            self._busy = False
            self.query_one("#btn-run-research").disabled = False

    # ── Actions ───────────────────────────────────────────────────────────────

    def action_go_back(self) -> None:
        self.app.pop_screen()

    def action_save_report(self) -> None:
        if not self._report:
            self._log("[yellow]No report to save yet.[/yellow]")
            return
        path = "research_report.md"
        try:
            with open(path, "w") as f:
                f.write(self._report)
            self._log(f"[green]✔ Report saved to[/green] {path}")
        except OSError as exc:
            self._log(f"[red]Save failed: {exc}[/red]")


# ═══════════════════════════════════════════════════════════════════════════════
# Chat Screen
# ═══════════════════════════════════════════════════════════════════════════════


class ChatScreen(Screen):
    """
    Interactive Q&A using the Orchestrator's root backend directly.

    The session history is kept in-memory for multi-turn coherence.
    This does NOT invoke the full multi-agent pipeline — it is a fast,
    single-model chat interface good for follow-up questions, brainstorming,
    or exploring a topic before launching a full research run.
    """

    BINDINGS = [
        Binding("escape", "go_back", "← Back"),
        Binding("ctrl+l", "clear_chat", "Clear Chat"),
    ]

    SYSTEM_PROMPT = """\
You are a knowledgeable research assistant. Answer questions clearly and
concisely. If you are uncertain, say so honestly. When the user asks for
deep analysis, note that a full Research run with the multi-agent pipeline
would provide primary-sourced, fact-checked findings.
"""

    def __init__(self, orchestrator: Orchestrator, config: Config) -> None:
        super().__init__()
        self.orchestrator = orchestrator
        self.config = config
        self._history: List[Message] = []
        self._busy = False

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="main-layout"):
            # Sidebar
            with Vertical(id="sidebar"):
                yield Label("💬 Chat", id="sidebar-title")
                yield Label("Session info", classes="sidebar-section")
                yield Label(
                    "0 turns", id="turn-counter", classes="sidebar-item pending"
                )
                yield Label("Tips:", classes="sidebar-section")
                yield Label(" Ctrl+L  clear history", classes="sidebar-item pending")
                yield Label(" Esc     back to menu", classes="sidebar-item pending")
                yield Label("", classes="sidebar-section")
                yield Label(
                    "For deep research,\ngo back and choose\nResearch mode.",
                    classes="sidebar-item pending",
                )

            # Content
            with Vertical(id="content-area"):
                with ScrollableContainer(id="chat-history"):
                    yield RichLog(id="chat-log", highlight=True, markup=True)
                with Horizontal(id="chat-input-bar"):
                    yield Input(
                        placeholder="Ask anything… (Enter to send)", id="chat-input"
                    )
                    yield Button("Send ↵", id="btn-send-chat")

        yield Footer()

    def _log_user(self, message: str) -> None:
        log = self.query_one("#chat-log", RichLog)
        log.write(Text(f"You:  {message}", style="bold green"))
        log.write("")

    def _log_assistant(self, message: str) -> None:
        log = self.query_one("#chat-log", RichLog)
        log.write(Text("Assistant:", style="bold cyan"))
        log.write(message)
        log.write("")

    def _update_turn_counter(self) -> None:
        turns = len(self._history) // 2
        self.query_one("#turn-counter", Label).update(
            f"{turns} turn{'s' if turns != 1 else ''}"
        )

    @on(Button.Pressed, "#btn-send-chat")
    def handle_send(self) -> None:
        if self._busy:
            return
        inp = self.query_one("#chat-input", Input)
        text = inp.value.strip()
        if not text:
            return
        inp.value = ""
        self._busy = True
        self.query_one("#btn-send-chat").disabled = True
        self._log_user(text)
        self._send_message(text)

    @on(Input.Submitted, "#chat-input")
    def handle_enter(self, _event: Input.Submitted) -> None:
        self.handle_send()

    @work(exclusive=False, thread=False)
    async def _send_message(self, user_text: str) -> None:
        self._history.append(Message(role="user", content=user_text))
        messages = [Message(role="system", content=self.SYSTEM_PROMPT)] + self._history
        try:
            response = await self.orchestrator._root_backend.generate(
                model=None,
                messages=messages,
            )
            reply = response.content
            self._history.append(Message(role="assistant", content=reply))
            self._log_assistant(reply)
            self._update_turn_counter()
        except Exception as exc:
            self._log_assistant(f"[Error communicating with model: {exc}]")
        finally:
            self._busy = False
            self.query_one("#btn-send-chat").disabled = False

    def action_go_back(self) -> None:
        self.app.pop_screen()

    def action_clear_chat(self) -> None:
        self._history.clear()
        self.query_one("#chat-log", RichLog).clear()
        self._update_turn_counter()


# ═══════════════════════════════════════════════════════════════════════════════
# Self-Optimize Screen
# ═══════════════════════════════════════════════════════════════════════════════


class SelfOptimizeScreen(Screen):
    """
    Runs the SelfOptimizingAgent pipeline.

    Steps (all streaming):
        1. Retrieve all memories from the memory MCP server.
        2. Analyse patterns and extract insights.
        3. Develop new research methods informed by those insights.
        4. Persist the new methods back to memory.

    The sidebar shows a live checklist; the right pane streams log events
    and then renders final insights in a Markdown panel.
    """

    BINDINGS = [
        Binding("escape", "go_back", "← Back"),
    ]

    _STEPS = [
        (1, "Read methods"),
        (2, "Retrieve memories"),
        (3, "Analyse patterns"),
        (4, "Develop methods"),
        (5, "Save to memory"),
    ]

    _PHASE_TO_STEP = {
        "read_methods": 1,
        "retrieve_memories": 2,
        "analyze": 3,
        "develop": 4,
        "update_methods": 5,
    }

    def __init__(
        self,
        config: Config,
        mcp_registry: MCPServerRegistry,
        long_term_memory: Optional[AsyncLongTermMemory] = None,
    ) -> None:
        super().__init__()
        self.config = config
        self.mcp_registry = mcp_registry
        self.long_term_memory = long_term_memory
        self._busy = False
        self._step_labels: dict[int, Label] = {}

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="main-layout"):
            # Sidebar
            with ScrollableContainer(id="sidebar"):
                yield Label("⚙  Self-Optimize", id="sidebar-title")
                yield Label("Pipeline steps:", classes="sidebar-section")
                for num, name in self._STEPS:
                    lbl = Label(f"  {num}. {name}", classes="sidebar-item pending")
                    self._step_labels[num] = lbl
                    yield lbl

            # Content
            with Vertical(id="content-area"):
                yield Button("⚙  Start Self-Optimization", id="btn-start-optimize")
                with ScrollableContainer(id="optimize-log"):
                    yield RichLog(id="opt-log", highlight=True, markup=True)
                with ScrollableContainer(id="insights-panel"):
                    yield Label("💡 Results", id="insights-title")
                    yield Markdown("", id="insights-markdown")

        yield Footer()

    def _log(self, message: str, style: str = "") -> None:
        log = self.query_one("#opt-log", RichLog)
        if style:
            log.write(Text(message, style=style))
        else:
            log.write(message)

    def _set_step(self, num: int, status: str) -> None:
        lbl = self._step_labels.get(num)
        if lbl:
            lbl.remove_class("active", "done", "error", "pending")
            lbl.add_class(status)

    @on(Button.Pressed, "#btn-start-optimize")
    def handle_start(self) -> None:
        if self._busy:
            return
        self._busy = True
        self.query_one("#btn-start-optimize").disabled = True
        self.query_one("#insights-panel").display = False
        for num in self._step_labels:
            self._set_step(num, "pending")
        self._run_optimize()

    @work(exclusive=True, thread=False)
    async def _run_optimize(self) -> None:
        model_backend = create_model_backend(self.config)
        agent = SelfOptimizingAgent(
            model_backend, self.mcp_registry, long_term_memory=self.long_term_memory
        )

        insights_text = ""
        new_methods: List[str] = []

        try:
            async for msg in agent.self_optimize():
                if msg.type == "optimize_progress":
                    phase = (msg.data or {}).get("phase", "")
                    status = (msg.data or {}).get("status", "")
                    step_num = self._PHASE_TO_STEP.get(phase)
                    if step_num:
                        if status == "running":
                            self._set_step(step_num, "active")
                        elif status == "completed":
                            self._set_step(step_num, "done")

                    if msg.data and "insights" in msg.data:
                        insights_text = msg.data["insights"]
                        self._log(
                            "[bold green]✔ Memory analysis complete.[/bold green]"
                        )
                    elif msg.data and "new_methods" in msg.data:
                        new_methods = msg.data["new_methods"]
                        self._log(
                            f"[bold green]✔ {len(new_methods)} new method(s) developed.[/bold green]"
                        )
                    else:
                        self._log(f"[dim]{msg.message}[/dim]")

                elif msg.type == "optimize_complete":
                    self._log(f"[bold green]✔ {msg.message}[/bold green]")

                elif msg.type == "error":
                    self._log(f"[red]✘ {msg.message}[/red]")
                    for num in self._step_labels:
                        if self._step_labels[num].has_class("active"):
                            self._set_step(num, "error")

            # Mark remaining active steps as done
            for num in self._step_labels:
                if self._step_labels[num].has_class("active"):
                    self._set_step(num, "done")

            # Render results
            if insights_text or new_methods:
                md = ""
                if insights_text:
                    md += "## Memory Analysis Insights\n\n"
                    md += insights_text + "\n\n"
                if new_methods:
                    md += "## Developed Research Methods\n\n"
                    for i, method in enumerate(new_methods, 1):
                        md += f"**{i}.** {method}\n\n"
                self.query_one("#insights-markdown", Markdown).update(md)
                self.query_one("#insights-panel").display = True
            else:
                self._log(
                    "[yellow]No significant insights or methods produced. "
                    "Add more memories via research runs first.[/yellow]"
                )

        except Exception as exc:
            self._log(f"[bold red]Self-optimization failed: {exc}[/bold red]")
            for num in self._step_labels:
                if self._step_labels[num].has_class("active"):
                    self._set_step(num, "error")
        finally:
            self._busy = False
            self.query_one("#btn-start-optimize").disabled = False

    def action_go_back(self) -> None:
        self.app.pop_screen()


# ═══════════════════════════════════════════════════════════════════════════════
# Models Screen
# ═══════════════════════════════════════════════════════════════════════════════

_ALL_BACKENDS = [
    "openai",
    "ollama",
    "anthropic",
    "azure",
    "bedrock",
    "aws",
    "gcp",
    "huggingface",
]


class ModelsScreen(Screen):
    """View and switch the active model backend."""

    BINDINGS = [
        Binding("escape", "go_back", "← Back"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._selected_backend: str = ""

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="main-layout"):
            with Vertical(id="sidebar"):
                yield Label("🤖  Models", id="sidebar-title")
                yield Label("Current config", classes="sidebar-section")
                yield Label(
                    f"  Backend: {self.app.config.model_backend}",
                    id="sidebar-backend",
                    classes="sidebar-item active",
                )
                yield Label("Backends:", classes="sidebar-section")
                for b in _ALL_BACKENDS:
                    cls = "selected" if b == self.app.config.model_backend else ""
                    yield Label(
                        f"  {b}",
                        classes=f"sidebar-item backend-item {cls}".strip(),
                        id=f"sidebar-b-{b}",
                    )

            with Vertical(id="content-area"):
                yield Label("", id="models-current")
                yield Label("Select a backend:", classes="models-section")
                with ScrollableContainer(id="models-backend-list"):
                    for b in _ALL_BACKENDS:
                        yield Button(
                            f"  {b}",
                            id=f"btn-backend-{b}",
                            classes="mode-btn",
                        )

                with Vertical(id="models-form"):
                    yield Label("Model name:", classes="models-label")
                    yield Input(
                        placeholder="e.g. gpt-4o, llama3.2…", id="models-input-model"
                    )
                    yield Label("API key (optional):", classes="models-label")
                    yield Input(
                        placeholder="sk-… (leave blank to keep current)",
                        id="models-input-api-key",
                        password=True,
                    )
                    yield Label("Base URL (optional):", classes="models-label")
                    yield Input(
                        placeholder="https://… (leave blank to keep current)",
                        id="models-input-base-url",
                    )

                with Horizontal(id="models-actions"):
                    yield Button("✔  Apply", id="btn-models-apply")
                    yield Button("✘  Cancel", id="btn-models-cancel")

                yield Label("", id="home-feedback")

        yield Footer()

    def on_mount(self) -> None:
        self._selected_backend = self.app.config.model_backend
        self._refresh_current_label()
        self._prefill_model()

    def _refresh_current_label(self) -> None:
        cfg = self.app.config
        model_map = {
            "openai": cfg.openai_model,
            "ollama": cfg.ollama_model,
            "azure": cfg.azure_deployment_name,
            "bedrock": cfg.aws_model,
            "aws": cfg.aws_model,
            "gcp": cfg.gcp_model,
            "anthropic": cfg.anthropic_model,
            "huggingface": cfg.huggingface_model,
        }
        model = model_map.get(cfg.model_backend, "unknown")
        self.query_one("#models-current", Label).update(
            f"Active: {cfg.model_backend} / {model}"
        )

    def _prefill_model(self) -> None:
        cfg = self.app.config
        model_map = {
            "openai": cfg.openai_model,
            "ollama": cfg.ollama_model,
            "azure": cfg.azure_deployment_name,
            "bedrock": cfg.aws_model,
            "aws": cfg.aws_model,
            "gcp": cfg.gcp_model,
            "anthropic": cfg.anthropic_model,
            "huggingface": cfg.huggingface_model,
        }
        self.query_one("#models-input-model", Input).value = (
            model_map.get(self._selected_backend, "") or ""
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if bid.startswith("btn-backend-"):
            backend = bid.removeprefix("btn-backend-")
            self._selected_backend = backend
            # Update sidebar highlight
            for b in _ALL_BACKENDS:
                lbl = self.query_one(f"#sidebar-b-{b}", Label)
                lbl.remove_class("selected")
            self.query_one(f"#sidebar-b-{backend}", Label).add_class("selected")
            self._prefill_model()

        elif bid == "btn-models-apply":
            self._apply_model()

        elif bid == "btn-models-cancel":
            self.app.pop_screen()

    @work(exclusive=True, thread=False)
    async def _apply_model(self) -> None:
        backend = self._selected_backend
        model = self.query_one("#models-input-model", Input).value.strip()
        api_key = self.query_one("#models-input-api-key", Input).value.strip()
        base_url = self.query_one("#models-input-base-url", Input).value.strip()

        if not model:
            self.query_one("#home-feedback", Label).update("Please enter a model name.")
            return

        # Update environment variables so Config picks them up
        os.environ["MODEL_BACKEND"] = backend
        env_model_map = {
            "openai": "OPENAI_MODEL",
            "ollama": "OLLAMA_MODEL",
            "bedrock": "AWS_MODEL",
            "aws": "AWS_MODEL",
            "azure": "AZURE_OPENAI_DEPLOYMENT",
            "gcp": "GCP_MODEL",
            "anthropic": "ANTHROPIC_MODEL",
            "huggingface": "HUGGINGFACE_MODEL",
        }
        os.environ[env_model_map.get(backend, "OLLAMA_MODEL")] = model

        if api_key:
            key_map = {
                "openai": "OPENAI_API_KEY",
                "azure": "AZURE_OPENAI_API_KEY",
                "anthropic": "ANTHROPIC_API_KEY",
                "aws": "AWS_API_KEY",
                "gcp": "GCP_API_KEY",
                "huggingface": "HUGGINGFACE_API_KEY",
            }
            env_key = key_map.get(backend)
            if env_key:
                os.environ[env_key] = api_key

        if base_url:
            url_map = {
                "openai": "OPENAI_BASE_URL",
                "ollama": "OLLAMA_BASE_URL",
                "azure": "AZURE_OPENAI_ENDPOINT",
                "gcp": "GCP_BASE_URL",
                "huggingface": "HUGGINGFACE_BASE_URL",
                "anthropic": "ANTHROPIC_BASE_URL",
                "aws": "AWS_BASE_URL",
            }
            env_url = url_map.get(backend)
            if env_url:
                os.environ[env_url] = base_url

        # Rebuild config → backend → pool → orchestrator
        try:
            self.app.config = Config()
            model_backend = create_model_backend(self.app.config)
            agent_pool = AgentPool.from_single_backend(model_backend)
            await agent_pool.async_init(self.app.mcp_registry)
            self.app.orchestrator = Orchestrator(
                config=self.app.config,
                mcp_registry=self.app.mcp_registry,
                agent_pool=agent_pool,
                long_term_memory=self.app.long_term_memory,
                research_depth="shallow",
            )
            self._refresh_current_label()
            self.query_one("#sidebar-backend", Label).update(f"  Backend: {backend}")
            self.query_one("#home-feedback", Label).update(
                f"✔ Switched to {backend} / {model}"
            )
        except Exception as exc:
            self.query_one("#home-feedback", Label).update(f"✘ Failed: {exc}")

    def action_go_back(self) -> None:
        self.app.pop_screen()


# ═══════════════════════════════════════════════════════════════════════════════
# MCP Screen
# ═══════════════════════════════════════════════════════════════════════════════


class MCPScreen(Screen):
    """View and manage MCP servers."""

    BINDINGS = [
        Binding("escape", "go_back", "← Back"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._selected_server: Optional[str] = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="main-layout"):
            with Vertical(id="sidebar"):
                yield Label("🔌  MCP Servers", id="sidebar-title")
                yield Label("Registered:", classes="sidebar-section")
                # Populated on mount
                yield Label(
                    "  (loading…)",
                    id="sidebar-mcp-loading",
                    classes="sidebar-item pending",
                )

            with Vertical(id="content-area"):
                yield Button("＋  Add Server", id="btn-mcp-show-add")
                yield Button("✘  Remove Selected", id="btn-mcp-remove")

                with ScrollableContainer(id="mcp-server-list"):
                    yield OptionList(id="mcp-log")

                with ScrollableContainer(id="mcp-tools-panel"):
                    yield Label("🔧  Tools", id="mcp-tools-title")
                    yield RichLog(id="mcp-tools-log", highlight=True, markup=True)

                with Vertical(id="mcp-add-form"):
                    yield Label("Server name:", classes="mcp-label")
                    yield Input(placeholder="my-server", id="mcp-input-name")
                    yield Label(
                        "Transport (streamable-http / sse / stdio):",
                        classes="mcp-label",
                    )
                    yield Input(placeholder="streamable-http", id="mcp-input-transport")
                    yield Label(
                        "URL (for http/sse) or command (for stdio):",
                        classes="mcp-label",
                    )
                    yield Input(
                        placeholder="http://localhost:8080/mcp", id="mcp-input-url"
                    )
                    with Horizontal(id="mcp-add-actions"):
                        yield Button("✔  Register", id="btn-mcp-add")

                yield Label("", id="home-feedback")

        yield Footer()

    def on_mount(self) -> None:
        self._refresh_servers()

    def _refresh_servers(self) -> None:
        servers = self.app.mcp_registry.get_all_server_info()

        # Update sidebar
        try:
            self.query_one("#sidebar-mcp-loading").remove()
        except NoMatches:
            pass
        sidebar = self.query_one("#sidebar", Vertical)
        # Remove old dynamic labels
        for child in list(sidebar.children):
            if (
                hasattr(child, "id")
                and child.id
                and child.id.startswith("sidebar-mcp-")
            ):
                child.remove()

        for info in servers:
            name = info["name"]
            status = info.get("status", "unknown")
            css_cls = "connected" if status == "connected" else "disconnected"
            icon = "●" if status == "connected" else "○"
            lbl = Label(
                f"  {icon} {name}",
                id=f"sidebar-mcp-{name}",
                classes=f"sidebar-item mcp-server-entry {css_cls}",
            )
            sidebar.mount(lbl)

        # Update main OptionList
        server_list = self.query_one("#mcp-log", OptionList)
        server_list.clear_options()
        if not servers:
            server_list.add_option(Option("No MCP servers registered.", id="__empty__"))
            return
        for info in servers:
            name = info["name"]
            transport = info.get("transport", "?")
            status = info.get("status", "unknown")
            tools_count = info.get("tools_count", 0)
            builtin = "builtin" if info.get("builtin") else "user"
            url = info.get("url", info.get("command", ""))
            style = "green" if status == "connected" else "red"
            server_list.add_option(
                Option(
                    Text(
                        f"  {name}  [{transport}]  {url}  — {tools_count} tool(s)  ({status}, {builtin})",
                        style=style,
                    ),
                    id=name,
                )
            )

    @on(OptionList.OptionSelected, "#mcp-log")
    def _server_selected(self, event: OptionList.OptionSelected) -> None:
        server_name = event.option_id
        if server_name and server_name != "__empty__":
            self._selected_server = server_name
            self._show_tools_for(server_name)

    def _show_tools_for(self, server_name: str) -> None:
        tools_log = self.query_one("#mcp-tools-log", RichLog)
        tools_log.clear()
        specs = self.app.mcp_registry.tool_specs.get(server_name, [])
        if not specs:
            tools_log.write(Text(f"No tools for {server_name}.", style="dim"))
            return
        tools_log.write(Text(f"Tools for {server_name}:", style="bold cyan"))
        for tool in specs:
            name = tool.get("function", {}).get("name", tool.get("name", "?"))
            desc = tool.get("function", {}).get(
                "description", tool.get("description", "")
            )
            tools_log.write(Text(f"  • {name}", style="bold"))
            if desc:
                tools_log.write(Text(f"    {desc[:80]}", style="dim"))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""

        if bid == "btn-mcp-show-add":
            form = self.query_one("#mcp-add-form")
            form.display = not form.display

        elif bid == "btn-mcp-add":
            self._register_server()

        elif bid == "btn-mcp-remove":
            self._remove_server()

    @work(exclusive=True, thread=False)
    async def _register_server(self) -> None:
        name = self.query_one("#mcp-input-name", Input).value.strip()
        transport = (
            self.query_one("#mcp-input-transport", Input).value.strip()
            or "streamable-http"
        )
        url_or_cmd = self.query_one("#mcp-input-url", Input).value.strip()
        feedback = self.query_one("#home-feedback", Label)

        if not name:
            feedback.update("Please enter a server name.")
            return
        if not url_or_cmd:
            feedback.update("Please enter a URL or command.")
            return

        if transport == "stdio":
            parts = url_or_cmd.split()
            cmd = parts[0]
            args = parts[1:] if len(parts) > 1 else []
            ok = await self.app.mcp_registry.register(
                name, command=cmd, args=args, transport="stdio"
            )
        else:
            ok = await self.app.mcp_registry.register(
                name, url=url_or_cmd, transport=transport
            )

        if ok:
            feedback.update(f"✔ Registered '{name}'")
            self.query_one("#mcp-add-form").display = False
            # Clear inputs
            self.query_one("#mcp-input-name", Input).value = ""
            self.query_one("#mcp-input-url", Input).value = ""
        else:
            feedback.update(f"✘ Failed to connect to '{name}' — stored as disconnected")

        self._refresh_servers()

    @work(exclusive=True, thread=False)
    async def _remove_server(self) -> None:
        feedback = self.query_one("#home-feedback", Label)
        if not self._selected_server:
            feedback.update("Select a server from the list first.")
            return
        # Prevent removing built-in servers
        servers = self.app.mcp_registry.get_all_server_info()
        info = next((s for s in servers if s["name"] == self._selected_server), None)
        if info and info.get("builtin"):
            feedback.update(f"'{self._selected_server}' is a built-in server and cannot be removed.")
            return
        target = self._selected_server
        await self.app.mcp_registry.unregister(target)
        self._selected_server = None
        feedback.update(f"✔ Removed '{target}'")
        self._refresh_servers()

    def action_go_back(self) -> None:
        self.app.pop_screen()


# ═══════════════════════════════════════════════════════════════════════════════
# Sessions Screen
# ═══════════════════════════════════════════════════════════════════════════════

# Default sessions directory — must match session_store.py
_SESSIONS_DIR = (
    Path(os.getenv("LOG_DIR", str(Path(__file__).parent / "logs"))) / "sessions"
)


class SessionsScreen(Screen):
    """Browse and resume past research sessions."""

    BINDINGS = [
        Binding("escape", "go_back", "← Back"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._session_ids: List[str] = []
        self._selected_index: int = -1

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="main-layout"):
            with Vertical(id="sidebar"):
                yield Label("📂  Sessions", id="sidebar-title")
                yield Label("Recent sessions:", classes="sidebar-section")
                yield Label(
                    "  (loading…)",
                    id="sidebar-sess-loading",
                    classes="sidebar-item pending",
                )

            with Vertical(id="content-area"):
                with Horizontal(id="sessions-actions"):
                    yield Button("＋  New Session", id="btn-session-new")
                    yield Button("▶  Resume Selected", id="btn-session-resume")

                with ScrollableContainer(id="sessions-list"):
                    yield OptionList(id="sessions-log")

                with ScrollableContainer(id="sessions-detail"):
                    yield Label("Session Detail", id="sessions-detail-title")
                    yield RichLog(id="sessions-detail-log", highlight=True, markup=True)

                yield Label("", id="home-feedback")

        yield Footer()

    def on_mount(self) -> None:
        self._load_sessions()

    def _load_sessions(self) -> None:
        """Scan disk checkpoints to list past sessions."""
        self._session_ids = []
        session_list = self.query_one("#sessions-log", OptionList)
        session_list.clear_options()

        try:
            self.query_one("#sidebar-sess-loading").remove()
        except NoMatches:
            pass

        sidebar = self.query_one("#sidebar", Vertical)
        # Remove old dynamic labels
        for child in list(sidebar.children):
            if (
                hasattr(child, "id")
                and child.id
                and child.id.startswith("sidebar-sess-")
            ):
                child.remove()

        if not _SESSIONS_DIR.exists():
            session_list.add_option(Option("No sessions found.", id="__empty__"))
            return

        # Collect checkpoint files
        files = sorted(
            _SESSIONS_DIR.glob("*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )

        if not files:
            session_list.add_option(Option("No sessions found.", id="__empty__"))
            return

        for i, f in enumerate(files[:20]):  # Show last 20
            sid = f.stem
            self._session_ids.append(sid)
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                state = data.get("state", "unknown")
                created = data.get("created_at", "?")[:19]
                # Extract goal from replay log
                goal = ""
                for ev in data.get("replay_log", []):
                    if ev.get("type") == "plan":
                        plan_data = ev.get("plan", ev.get("data", {}).get("plan", {}))
                        if isinstance(plan_data, dict):
                            goal = plan_data.get("goal", "")[:60]
                            break
                    elif ev.get("type") == "status" and not goal:
                        goal = ev.get("message", "")[:60]

            except Exception:
                state = "unknown"
                created = "?"
                goal = ""

            state_css = {
                "complete": "session-complete",
                "executing": "session-active",
                "error": "session-error",
            }.get(state, "")
            icon = {"complete": "✔", "error": "✘", "executing": "⏳"}.get(state, "○")

            display = f"  {icon} [{i + 1}] {sid[:14]}…  {state}  {created}"
            if goal:
                display += f"  |  {goal}"
            style = "green" if state == "complete" else "dim"
            session_list.add_option(Option(Text(display, style=style), id=str(i)))

            sidebar.mount(
                Label(
                    f"  {icon} {sid[:8]}…",
                    id=f"sidebar-sess-{i}",
                    classes=f"sidebar-item session-entry {state_css}",
                )
            )

    @on(OptionList.OptionSelected, "#sessions-log")
    def _session_selected(self, event: OptionList.OptionSelected) -> None:
        opt_id = event.option_id
        if opt_id and opt_id != "__empty__":
            self._selected_index = int(opt_id)
            self._show_session_detail(self._selected_index)
            self.query_one("#btn-session-resume").display = True

    def _show_session_detail(self, index: int) -> None:
        if index < 0 or index >= len(self._session_ids):
            return
        sid = self._session_ids[index]
        checkpoint_file = _SESSIONS_DIR / f"{sid}.json"
        detail_log = self.query_one("#sessions-detail-log", RichLog)
        detail_log.clear()
        self.query_one("#sessions-detail").display = True
        try:
            data = json.loads(checkpoint_file.read_text(encoding="utf-8"))
            goal = ""
            for ev in data.get("replay_log", []):
                if ev.get("type") == "plan":
                    plan_data = ev.get("plan", ev.get("data", {}).get("plan", {}))
                    if isinstance(plan_data, dict):
                        goal = plan_data.get("goal", "")
                        break
            state = data.get("state", "unknown")
            created = data.get("created_at", "?")[:19]
            replay_count = len(data.get("replay_log", []))
            detail_log.write(Text(f"Session ID: {sid}", style="bold"))
            detail_log.write(Text(f"State:      {state}  |  Created: {created}", style="dim"))
            detail_log.write(Text(f"Events:     {replay_count}", style="dim"))
            if goal:
                detail_log.write(Text(f"Goal:       {goal}", style="green"))
        except Exception as exc:
            detail_log.write(Text(f"Error reading session: {exc}", style="red"))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if bid == "btn-session-new":
            self._new_session()
        elif bid == "btn-session-resume":
            self._resume_selected()

    @work(exclusive=True, thread=False)
    async def _new_session(self) -> None:
        cfg = self.app.config
        model_backend = create_model_backend(cfg)
        agent_pool = AgentPool.from_single_backend(model_backend)
        await agent_pool.async_init(self.app.mcp_registry)
        self.app.orchestrator = Orchestrator(
            config=cfg,
            mcp_registry=self.app.mcp_registry,
            agent_pool=agent_pool,
            long_term_memory=self.app.long_term_memory,
            research_depth="shallow",
        )
        self.query_one("#home-feedback", Label).update("✔ New session created.")
        self.app.pop_screen()

    def _resume_selected(self) -> None:
        feedback = self.query_one("#home-feedback", Label)
        if not self._session_ids:
            feedback.update("No sessions available.")
            return
        # Resume the most recent session (first in list)
        # Users can also type /sessions and use the numbered list
        idx = max(self._selected_index, 0)
        if idx >= len(self._session_ids):
            feedback.update("Invalid selection.")
            return
        sid = self._session_ids[idx]
        self._do_resume(sid)

    @work(exclusive=True, thread=False)
    async def _do_resume(self, session_id: str) -> None:
        feedback = self.query_one("#home-feedback", Label)
        checkpoint_file = _SESSIONS_DIR / f"{session_id}.json"
        if not checkpoint_file.exists():
            feedback.update(f"Checkpoint not found for {session_id}.")
            return
        try:
            data = json.loads(checkpoint_file.read_text(encoding="utf-8"))
            feedback.update(f"Resuming session {session_id[:12]}…")
            # Push a research screen and replay the log into it
            screen = ResearchScreen(self.app.orchestrator, self.app.config)
            self.app.push_screen(screen)
            # Replay log entries into the research screen
            self.app.call_after_refresh(
                lambda: self._replay_log(screen, data.get("replay_log", []))
            )
        except Exception as exc:
            feedback.update(f"✘ Failed to resume: {exc}")

    @staticmethod
    def _replay_log(screen: ResearchScreen, replay_log: list) -> None:
        """Replay stored events into a ResearchScreen for visual continuity."""
        try:
            for event in replay_log:
                t = event.get("type", "")
                msg = event.get("message", "")
                if t == "status":
                    screen._log(f"[dim]{msg}[/dim]")
                elif t == "plan":
                    plan_data = event.get("plan", event.get("data", {}).get("plan", {}))
                    if isinstance(plan_data, dict):
                        goal = plan_data.get("goal", "")
                        md_lines = [f"**Goal:** {goal}\n"]
                        for s in plan_data.get("steps", []):
                            md_lines.append(
                                f"- **Step {s.get('id', '?')}:** {s.get('description', '')}"
                            )
                        try:
                            screen.query_one("#plan-body", Static).update(
                                "\n".join(md_lines)
                            )
                        except NoMatches:
                            pass
                elif t == "report":
                    doc = event.get("data", {}).get("document", "")
                    if doc:
                        screen._show_report(doc)
                elif t in ("step_complete", "step_start", "step_failed"):
                    screen._log(f"[dim](replay) {msg}[/dim]")
                elif t == "error":
                    screen._log(f"[red]{msg}[/red]")
        except Exception:
            pass  # Screen may not be fully mounted

    def action_go_back(self) -> None:
        self.app.pop_screen()


# ═══════════════════════════════════════════════════════════════════════════════
# Root Application
# ═══════════════════════════════════════════════════════════════════════════════


class ResearchApp(App):
    """Root Textual application — holds shared state passed to all screens."""

    CSS = APP_CSS
    TITLE = "Deep Research Agent"
    SUB_TITLE = "AI-powered multi-agent research"

    BINDINGS = [
        Binding("q", "quit", "Quit", show=True),
    ]

    def __init__(
        self,
        orchestrator: Orchestrator,
        config: Config,
        mcp_registry: MCPServerRegistry,
        long_term_memory: Optional[AsyncLongTermMemory] = None,
        initial_mode: Optional[str] = None,
        initial_query: Optional[str] = None,
        docs_dir: Optional[str] = None,
    ) -> None:
        super().__init__()
        self.orchestrator = orchestrator
        self.config = config
        self.mcp_registry = mcp_registry
        self.long_term_memory = long_term_memory
        self._initial_mode = initial_mode
        self._initial_query = initial_query
        self.docs_dir: Optional[str] = docs_dir
        self.session_store = SessionStore()

    def on_mount(self) -> None:
        if self._initial_mode == "research":
            screen = ResearchScreen(self.orchestrator, self.config)
            self.push_screen(screen)
            if self._initial_query:
                self.call_after_refresh(
                    lambda: self._auto_run_query(screen, self._initial_query)
                )
        elif self._initial_mode == "chat":
            self.push_screen(ChatScreen(self.orchestrator, self.config))
        elif self._initial_mode == "self-optimize":
            self.push_screen(
                SelfOptimizeScreen(
                    self.config, self.mcp_registry, self.long_term_memory
                )
            )
        else:
            self.push_screen(HomeScreen())

    @staticmethod
    def _auto_run_query(screen: ResearchScreen, query: str) -> None:
        try:
            inp = screen.query_one("#query-input", Input)
            inp.value = query
            screen.handle_run_research()
        except Exception:
            pass  # Screen may not be fully mounted yet; user can press Run


# ═══════════════════════════════════════════════════════════════════════════════
# Entry Point
# ═══════════════════════════════════════════════════════════════════════════════


async def _bootstrap(
    research_depth: str = "shallow",
) -> tuple[Orchestrator, Config, MCPServerRegistry, AsyncLongTermMemory]:
    """Async setup: config → LTM → MCP registry → AgentPool → Orchestrator."""
    config = Config()
    mcp_registry = await create_mcp_registry(config)

    long_term_memory = AsyncLongTermMemory(
        neo4j_uri=config.neo4j_uri,
        neo4j_user=config.neo4j_user,
        neo4j_password=config.neo4j_password,
        neo4j_database=config.neo4j_database,
        embedding_dimensions=config.neo4j_embedding_dimensions,
    )
    await long_term_memory.async_init()

    model_backend = create_model_backend(config)
    agent_pool = AgentPool.from_single_backend(model_backend)
    await agent_pool.async_init(mcp_registry)

    orchestrator = Orchestrator(
        config=config,
        mcp_registry=mcp_registry,
        agent_pool=agent_pool,
        long_term_memory=long_term_memory,
        research_depth=research_depth,
    )
    return orchestrator, config, mcp_registry, long_term_memory


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Research Assistant TUI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Modes:
  research      Multi-agent deep-research pipeline (plan → execute → report)
  chat          Interactive Q&A with the assistant
  self-optimize Analyse agent memories and evolve research methods

Examples:
  python cli.py
  python cli.py research
  python cli.py research "What are the latest advances in fusion energy?"
  python cli.py research --depth moderate
  python cli.py research --depth deep "Detailed analysis of quantum computing"
  python cli.py chat
  python cli.py self-optimize
  python cli.py research --backend ollama --model llama3.2
""",
    )
    parser.add_argument(
        "mode",
        nargs="?",
        choices=["research", "chat", "self-optimize"],
        help="Operating mode (omit to show mode picker)",
    )
    parser.add_argument(
        "query",
        nargs="*",
        help="Research query (research mode only)",
    )
    parser.add_argument(
        "--backend",
        choices=["openai", "ollama", "bedrock", "azure", "huggingface", "gcp"],
        help="Model backend override",
    )
    parser.add_argument("--model", help="Model name override")
    parser.add_argument(
        "--depth",
        choices=["shallow", "moderate", "deep"],
        default="shallow",
        help="Research depth (default: shallow)",
    )
    parser.add_argument(
        "--docs-dir",
        help="Local directory to scan for relevant documents",
    )

    args = parser.parse_args()

    # Apply overrides before Config is constructed
    if args.backend:
        os.environ["MODEL_BACKEND"] = args.backend
    if args.model:
        backend = args.backend or os.getenv("MODEL_BACKEND", "ollama")
        env_map = {
            "openai": "OPENAI_MODEL",
            "ollama": "OLLAMA_MODEL",
            "bedrock": "AWS_MODEL",
            "azure": "AZURE_OPENAI_DEPLOYMENT",
            "gcp": "GCP_MODEL",
            "huggingface": "HUGGINGFACE_MODEL",
        }
        os.environ[env_map.get(backend, "OLLAMA_MODEL")] = args.model

    try:
        orchestrator, config, mcp_registry, long_term_memory = asyncio.run(
            _bootstrap(research_depth=args.depth)
        )
    except Exception as exc:
        print(f"[ERROR] Initialization failed: {exc}", file=sys.stderr)
        sys.exit(1)

    initial_query = " ".join(args.query) if args.query else None

    # Resolve docs directory: CLI arg takes precedence over config/env var
    docs_dir = args.docs_dir
    if docs_dir:
        resolved = Path(docs_dir).expanduser().resolve()
        if not resolved.is_dir():
            print(
                f"[WARNING] --docs-dir path is not a valid directory: {resolved}",
                file=sys.stderr,
            )
            docs_dir = None
        else:
            docs_dir = str(resolved)
    elif config.docs_dir:
        docs_dir = config.docs_dir

    app = ResearchApp(
        orchestrator=orchestrator,
        config=config,
        mcp_registry=mcp_registry,
        long_term_memory=long_term_memory,
        initial_mode=args.mode,
        initial_query=initial_query,
        docs_dir=docs_dir,
    )
    app.run()


if __name__ == "__main__":
    main()
