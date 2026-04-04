"""
cli.py

Textual-based TUI for the Research Assistant.

Three modes are available:
    research      — full Orchestrator pipeline: plan → execute → synthesise
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

import os
import asyncio
import argparse
import sys
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
    ProgressBar,
    RichLog,
    Static,
)

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

# ═══════════════════════════════════════════════════════════════════════════════
# CSS
# ═══════════════════════════════════════════════════════════════════════════════

APP_CSS = """
Screen {
    background: $surface;
}

/* ── Mode Picker ─────────────────────────────────────────────────── */
#mode-picker {
    align: center middle;
    height: 100%;
}

#mode-picker-card {
    width: 64;
    height: auto;
    border: round $primary;
    padding: 2 4;
    background: $panel;
}

#mode-picker-title {
    text-align: center;
    text-style: bold;
    color: $primary;
    margin-bottom: 1;
}

#mode-picker-subtitle {
    text-align: center;
    color: $text-muted;
    margin-bottom: 2;
}

.mode-btn {
    width: 100%;
    margin-bottom: 1;
}

#btn-research { background: $primary; }
#btn-chat     { background: $success; }
#btn-optimize { background: $warning; }

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
# Mode-Picker Screen
# ═══════════════════════════════════════════════════════════════════════════════


class ModePickerScreen(Screen):
    """Full-screen mode selection shown on startup."""

    BINDINGS = [
        Binding("1", "pick_research", "Research"),
        Binding("2", "pick_chat", "Chat"),
        Binding("3", "pick_optimize", "Self-Optimize"),
        Binding("q", "quit_app", "Quit"),
    ]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Container(id="mode-picker"):
            with Vertical(id="mode-picker-card"):
                yield Label("🔬  Research Assistant", id="mode-picker-title")
                yield Label(
                    "Select an operating mode  (or press 1 / 2 / 3)",
                    id="mode-picker-subtitle",
                )
                yield Button(
                    "1  Research         — Deep multi-agent investigation",
                    id="btn-research",
                    classes="mode-btn",
                )
                yield Button(
                    "2  Chat             — Interactive Q&A with the agent",
                    id="btn-chat",
                    classes="mode-btn",
                )
                yield Button(
                    "3  Self-Optimize    — Analyse memories & evolve methods",
                    id="btn-optimize",
                    classes="mode-btn",
                )
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        actions = {
            "btn-research": self.action_pick_research,
            "btn-chat": self.action_pick_chat,
            "btn-optimize": self.action_pick_optimize,
        }
        handler = actions.get(event.button.id)
        if handler:
            handler()

    def action_pick_research(self) -> None:
        self.app.push_screen(ResearchScreen(self.app.orchestrator, self.app.config))

    def action_pick_chat(self) -> None:
        self.app.push_screen(ChatScreen(self.app.orchestrator, self.app.config))

    def action_pick_optimize(self) -> None:
        self.app.push_screen(
            SelfOptimizeScreen(
                self.app.config, self.app.mcp_registry, self.app.long_term_memory
            )
        )

    def action_quit_app(self) -> None:
        self.app.exit()


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

    # ── Workers ───────────────────────────────────────────────────────────────

    @work(exclusive=True, thread=False)
    async def _run_plan(self, query: str) -> None:
        self._log(f"[bold blue]🔍 Research query:[/bold blue] {query}")
        self._set_progress(5)

        try:
            async for msg in self.orchestrator.plan(query):
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
# Root Application
# ═══════════════════════════════════════════════════════════════════════════════


class ResearchApp(App):
    """Root Textual application — holds shared state passed to all screens."""

    CSS = APP_CSS
    TITLE = "Research Assistant"
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
    ) -> None:
        super().__init__()
        self.orchestrator = orchestrator
        self.config = config
        self.mcp_registry = mcp_registry
        self.long_term_memory = long_term_memory
        self._initial_mode = initial_mode
        self._initial_query = initial_query

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
            self.push_screen(ModePickerScreen())

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

    app = ResearchApp(
        orchestrator=orchestrator,
        config=config,
        mcp_registry=mcp_registry,
        long_term_memory=long_term_memory,
        initial_mode=args.mode,
        initial_query=initial_query,
    )
    app.run()


if __name__ == "__main__":
    main()
