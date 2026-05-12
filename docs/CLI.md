# CLI — Textual TUI Reference

## Table of Contents

1. [Overview](#overview)
2. [Requirements](#requirements)
3. [Quick Start](#quick-start)
4. [Invocation & Arguments](#invocation--arguments)
   - [Positional Arguments](#positional-arguments)
   - [Options](#options)
   - [Research Depth](#research-depth)
   - [Backend & Model Overrides](#backend--model-overrides)
5. [Home Screen & Slash Commands](#home-screen--slash-commands)
6. [Research Mode](#research-mode)
   - [Layout](#research-layout)
   - [Local Document Discovery](#local-document-discovery)
   - [Plan Review Workflow](#plan-review-workflow)
   - [Agent Log Colours](#agent-log-colours)
   - [Progress Bar](#progress-bar)
   - [Final Report](#final-report)
   - [Saving the Report](#saving-the-report)
7. [Chat Mode](#chat-mode)
   - [Layout](#chat-layout)
   - [Multi-Turn History](#multi-turn-history)
8. [Self-Optimize Mode](#self-optimize-mode)
   - [Layout](#self-optimize-layout)
   - [Pipeline Steps](#pipeline-steps)
   - [Results Panel](#results-panel)
9. [Keyboard Shortcuts](#keyboard-shortcuts)
10. [Architecture Notes](#architecture-notes)
    - [Bootstrap Sequence](#bootstrap-sequence)
    - [Async Workers](#async-workers)
    - [Screen Stack](#screen-stack)
11. [Relationship to the Web UI](#relationship-to-the-web-ui)
12. [Troubleshooting](#troubleshooting)

---

## Overview

`cli.py` is a fully interactive **terminal user interface (TUI)** for the Research Assistant, built with [Textual](https://textual.textualize.io/). It exposes all three operating modes of the agent in a navigable, keyboard-driven interface without requiring a browser or running the API server.

```
┌──────────────────────────────────────────────────────┐
│  🔬  Research Assistant                    12:34:56  │
├──────────────────────────────────────────────────────┤
│                                                      │
│         Select an operating mode                     │
│                                                      │
│  [ 1  Research   — Deep multi-agent investigation ]  │
│  [ 2  Chat       — Interactive Q&A with the agent ]  │
│  [ 3  Self-Optimize — Analyse memories & evolve   ]  │
│                                                      │
└──────────────────────────────────────────────────────┘
```

The three modes map directly onto the agent subsystems:

| Mode | Agent subsystem used |
|---|---|
| **Research** | `Orchestrator` — full multi-agent pipeline |
| **Chat** | `Orchestrator._root_backend` — single-model Q&A |
| **Self-Optimize** | `SelfOptimizingAgent` — memory analysis & method development |

---

## Requirements

| Dependency | Minimum version | Purpose |
|---|---|---|
| `textual` | 0.80.0 | TUI framework |
| `rich` | 13.0.0 | Rich text / markup in logs |
| `python-dotenv` | 1.0.0 | `.env` file loading |

All other dependencies are shared with the API server (see `requirements.txt`).

Install everything with:

```bash
pip install -r backend/requirements.txt
```

---

## Quick Start

```bash
# From the backend/ directory

# Open the home screen (slash-command interface)
python cli.py

# Jump straight to a mode
python cli.py research
python cli.py chat
python cli.py self-optimize

# Pre-fill a research query and run it immediately
python cli.py research "What are the latest advances in fusion energy?"

# Set research depth (shallow / moderate / deep)
python cli.py research --depth moderate
python cli.py research --depth deep "Detailed analysis of quantum computing"

# Override the model backend at launch
python cli.py research --backend ollama --model llama3.2
python cli.py chat --backend openai --model gpt-4o

# Scan a local directory for relevant documents during research
python cli.py research --docs-dir ./my-papers "survey of transformer architectures"
python cli.py research --docs-dir ~/Documents/research-data
```

---

## Invocation & Arguments

```
python cli.py [mode] [query ...] [--backend BACKEND] [--model MODEL] [--depth DEPTH] [--docs-dir PATH]
```

### Positional Arguments

| Argument | Required | Description |
|---|---|---|
| `mode` | No | One of `research`, `chat`, `self-optimize`. Omit to show the mode picker. |
| `query` | No | One or more words that form the research query. Only meaningful in `research` mode. If provided, the query is pre-filled and execution starts automatically. |

### Options

| Flag | Description |
|---|---|
| `--backend` | Override the model backend. Choices: `openai`, `ollama`, `bedrock`, `azure`, `huggingface`, `gcp`. |
| `--model` | Override the specific model name within the chosen backend. |
| `--depth` | Research depth. Choices: `shallow` (default), `moderate`, `deep`. Controls whether the QA loop runs and how many retries it gets. See [Research Depth](#research-depth) below. |
| `--docs-dir` | Path to a local directory to scan for relevant documents. When set, the CLI discovers matching files and asks for your permission before including them in the research pipeline. See [Local Document Discovery](#local-document-discovery). |
| `-h`, `--help` | Print the help message and exit. |

### Research Depth

| Depth | QA loop | Max QA retries | Notes |
|---|---|---|---|
| `shallow` (default) | Skipped | 0 | Search → Analyst only. Fastest and lowest cost. |
| `moderate` | Enabled | 2 | Standard pipeline: Search → Analyst → QA. |
| `deep` | Enabled | 3 | As moderate, plus depth-hint prompts injected into Search and Analyst — requires claims be backed by multiple independent sources. |

The depth is fixed at startup and applies to every research run in that CLI session.

### Backend & Model Overrides

When `--backend` and/or `--model` are provided, the CLI sets the corresponding environment variable *before* `Config` is constructed, so the override propagates through the entire agent stack without any `.env` changes.

The mapping of `--backend` to environment variable:

| `--backend` value | Environment variable set |
|---|---|
| `openai` | `OPENAI_MODEL` |
| `ollama` | `OLLAMA_MODEL` |
| `bedrock` | `AWS_MODEL` |
| `azure` | `AZURE_OPENAI_DEPLOYMENT` |
| `gcp` | `GCP_MODEL` |
| `huggingface` | `HUGGINGFACE_MODEL` |

---

## Home Screen & Slash Commands

The home screen is the default landing screen. It displays an ASCII banner, status line (showing active backend, model, MCP server count, and docs directory if set), and an input field for slash commands.

**Available slash commands:**

| Command | Description |
|---|---|
| `/research <query>` | Open Research mode, optionally with a pre-filled query |
| `/chat` | Open Chat mode |
| `/optimize` | Open Self-Optimize mode |
| `/docs <path>` | Set or change the local documents directory for research |
| `/models` | View / change the model backend |
| `/mcp` | View / manage MCP servers |
| `/sessions` | Browse & resume past sessions |
| `/new` | Start a fresh session (resets orchestrator state) |
| `/help` | Show the command list |
| `/exit` | Quit the application |

The input field provides **autocomplete** — as you type a `/` prefix, a dropdown shows matching commands.

**Key bindings on the home screen:**

| Key | Action |
|---|---|
| `1` | Open Research mode |
| `2` | Open Chat mode |
| `3` | Open Self-Optimize mode |
| `q` | Quit the application |

### The `/docs` Command

Use `/docs <path>` to set a local directory that will be scanned for relevant documents whenever you start a research run. The path can be absolute or relative (supports `~` expansion).

```
/docs ~/Documents/research-papers
/docs ./data/references
/docs                              # shows the currently set directory
```

When a docs directory is active, the status line at the top of the home screen shows the directory name. The setting persists for the duration of the CLI session.

You can also set this via the `--docs-dir` CLI argument or the `DOCS_DIR` environment variable (in `.env` or exported).

---

## Research Mode

Research mode runs the full **Orchestrator** pipeline:

```
Query → [Document Discovery → Permission] → plan() → [Approve / Modify / Deny] → execute() → synthesise() → Report
```

### Research Layout

```
┌─────────────────────┬──────────────────────────────────────────┐
│  📋 Research Plan   │  [ query input field       ] [▶ Research]│
│                     │                                           │
│  Goal: …            │  ┌─ 📂 Local Documents Found ──────────┐ │
│  Steps:             │  │  Found 3 relevant document(s):      │ │
│    1. … (pending)   │  │  • paper.pdf (245.3 KB) — filename  │ │
│    2. … (active)    │  │    contains 'transformer'           │ │
│    3. … (done ✓)    │  │  • notes.md (12.1 KB) — content     │ │
│                     │  │    contains 'attention'              │ │
│                     │  └──────────────────────────────────────┘ │
│                     │  [✔ Use Documents] [✘ Skip]              │
│                     │                                           │
│                     │  ┌─ Research Plan ─────────────────────┐ │
│                     │  │  Goal: …                            │ │
│                     │  │  - Step 1: …                        │ │
│                     │  │  - Step 2: …                        │ │
│                     │  └─────────────────────────────────────┘ │
│                     │                                           │
│                     │  [✔ Approve] [✏ Modify] [✘ Deny]         │
│                     │                                           │
│                     │  ████████████░░░░  65%                   │
│                     │                                           │
│                     │  ┌─ Agent Log ─────────────────────────┐ │
│                     │  │  [dim] Scanning local documents…    │ │
│                     │  │  [green] Loaded 3 document(s)…      │ │
│                     │  │  [dim] Orchestrator: analysing…     │ │
│                     │  │  [cyan] [Search] Gathering data…    │ │
│                     │  └─────────────────────────────────────┘ │
│                     │                                           │
│                     │  ┌─ 📄 Research Report ────────────────┐ │
│                     │  │  ## Executive Summary               │ │
│                     │  │  …                                  │ │
│                     │  └─────────────────────────────────────┘ │
└─────────────────────┴──────────────────────────────────────────┘
```

- **Left sidebar** — live plan step tracker; each step label updates its colour as the pipeline progresses.
- **Documents panel** — appears only when a docs directory is set and relevant files are found. Shows matched files with sizes and match reasons. Hidden again after the user responds.
- **Plan panel** — shows the goal and step list as formatted text; updated in-place when the plan is modified.
- **Action bar** — only visible after a plan is generated or after a modification.
- **Progress bar** — hidden until research starts; updates at key milestones.
- **Agent log** — scrollable, colour-coded stream of all agent activity.
- **Report panel** — hidden until synthesis is complete; renders the final Markdown report inline.

### Local Document Discovery

When a docs directory is configured (via `--docs-dir`, `/docs`, or the `DOCS_DIR` environment variable), the CLI automatically scans it before generating a research plan. The workflow is:

1. **Scan** — Recursively walks the directory for supported file types (`.pdf`, `.docx`, `.odt`, `.txt`, `.md`, `.csv`, `.json`), up to a configurable cap (default: 50 files, controlled by `MAX_LOCAL_DOCS`).

2. **Relevance filter** — Matches query keywords against filenames and the first ~500 characters of file content. Only files with at least one keyword match are shown. If the query has no meaningful terms (< 3 chars per word), all supported files are included.

3. **Permission prompt** — Displays the discovered documents in a panel with:
   - File name and size
   - Match reason (e.g., "filename contains 'quantum'" or "content contains 'entanglement'")
   - Two action buttons: **✔ Use Documents** and **✘ Skip**

4. **Content injection** — If approved, full file contents are read (up to `MAX_LOCAL_DOC_CHARS` per file, default: 10,000) and passed to `Orchestrator.plan()`. The orchestrator injects them into the planning prompt as "Local reference documents" — treated as primary sources that the agent avoids re-researching.

5. **Skip** — If denied, research proceeds normally without local document context.

**Supported file types and extraction:**

| Extension | Extraction method |
|---|---|
| `.txt`, `.md`, `.csv`, `.json` | Direct UTF-8 read |
| `.pdf` | `pypdf` page-by-page text extraction |
| `.docx` | `python-docx` paragraph extraction |
| `.odt` | `odfpy` paragraph extraction |

> **Note:** PDF/DOCX/ODT extraction requires the corresponding Python libraries (`pypdf`, `python-docx`, `odfpy`). If a library is not installed, files of that type are silently skipped during scanning. These are already in `requirements.txt`.

**Configuration options:**

| Variable | Default | Description |
|---|---|---|
| `DOCS_DIR` | (unset) | Default directory to scan. Overridden by `--docs-dir` or `/docs`. |
| `MAX_LOCAL_DOCS` | 50 | Maximum number of files to discover per scan. |
| `MAX_LOCAL_DOC_CHARS` | 10000 | Maximum characters read per document for injection. |

### Plan Review Workflow

After the Orchestrator returns a plan, three action buttons appear:

| Button | Keyboard shortcut | Behaviour |
|---|---|---|
| **✔ Approve** | Click | Calls `Orchestrator.execute()` then `Orchestrator.synthesise()`. |
| **✏ Modify** | Click | Reveals a feedback input field. Type a description of the desired changes and click **Send Feedback** (or press Enter) to call `Orchestrator.modify_plan()`. The updated plan replaces the previous one and the action bar reappears for another review cycle. |
| **✘ Deny** | Click | Calls `Orchestrator.deny_plan()`, clears all state, and returns to the idle query input. |

There is no limit on the number of modify/re-review cycles before approval.

### Agent Log Colours

Every message written to the agent log is colour-coded by the sub-agent that produced it:

| Colour | Sub-agent / event |
|---|---|
| `dim` (grey) | General status messages, orchestrator planning |
| `cyan` | `SearchAgent` — web retrieval |
| `blue` | `AnalystAgent` — claim extraction & cross-referencing |
| `yellow` | `LoopAgent` (QA) — contradiction detection |
| `orange` | QA retry loop — re-investigation of flagged contradictions |
| `green` | Step/phase completion, report ready |
| `red` | Errors |
| `bold blue` | Research query echo |

### Progress Bar

The progress bar tracks five milestones across the three pipeline phases:

| Milestone | Progress |
|---|---|
| Query submitted, planning started | 5 % |
| Plan generated, awaiting approval | 15 % |
| Each step completed | +`60 / total_steps` % (capped at 75 %) |
| All steps done, synthesis starting | 80–85 % |
| Report complete | 100 % |

### Final Report

Once synthesis completes, the report panel scrolls into view beneath the log. The report is rendered as **Markdown** directly in the terminal using Textual's `Markdown` widget — headings, bullet lists, bold/italic text, and code blocks are all rendered natively.

The report panel is bordered in green to distinguish it visually from the log.

### Saving the Report

Press `Ctrl+S` at any time after the report is generated to write it to `research_report.md` in the current working directory. A confirmation message is written to the agent log.

---

## Chat Mode

Chat mode provides a fast, conversational interface backed by the **Orchestrator's root model backend** directly. It does not invoke the multi-agent pipeline (no Search, Analyst, QA, or ReportComposer agents), making it much faster and lower-cost for exploratory questions and follow-ups.

### Chat Layout

```
┌─────────────────────┬──────────────────────────────────────────┐
│  💬 Chat            │  ┌─ Conversation ────────────────────────┐│
│                     │  │                                       ││
│  Session info       │  │  You:  What is retrieval-augmented    ││
│  3 turns            │  │        generation?                    ││
│                     │  │                                       ││
│  Tips:              │  │  Assistant:                           ││
│   Ctrl+L  clear     │  │  RAG is a technique that combines…    ││
│   Esc     back      │  │                                       ││
│                     │  └───────────────────────────────────────┘│
│  For deep research, │                                           │
│  go back and choose │  [ Ask anything… (Enter to send)  ] [Send]│
│  Research mode.     │                                           │
└─────────────────────┴──────────────────────────────────────────┘
```

- **Left sidebar** — shows the current turn count and keyboard tips.
- **Conversation panel** — scrollable log of the full session. User turns are shown in bold green; assistant replies in cyan.
- **Input bar** — press `Enter` or click **Send ↵** to submit.

### Multi-Turn History

The full conversation history is kept in-memory for the session. Every message (user + assistant) is prepended with a fixed system prompt and sent to the model on each turn, giving the model full context of the conversation.

History is **not persisted** between CLI sessions. Use `Ctrl+L` to clear it mid-session.

---

## Self-Optimize Mode

Self-Optimize mode runs the `SelfOptimizingAgent` pipeline, which analyses the agent's stored memories to identify patterns and derive new research methods.

### Self-Optimize Layout

```
┌─────────────────────┬──────────────────────────────────────────┐
│  ⚙  Self-Optimize  │  [⚙ Start Self-Optimization]             │
│                     │                                           │
│  Pipeline steps:    │  ┌─ Activity Log ────────────────────────┐│
│    1. Read methods  │  │  [dim] Reading current methods…       ││
│    2. Retrieve      │  │  [dim] Retrieved 42 memories…         ││
│       memories      │  │  [green] ✔ Memory analysis complete.  ││
│    3. Analyse       │  │  [dim] Developing new methods…        ││
│       patterns      │  │  [green] ✔ 3 new method(s) developed. ││
│    4. Develop       │  │  [green] ✔ RESEARCH-METHODS.md updated││
│       methods       │  └───────────────────────────────────────┘│
│    5. Save to       │                                           │
│       memory        │  ┌─ 💡 Results ──────────────────────────┐│
│                     │  │  ## Memory Analysis Insights          ││
│                     │  │  …                                    ││
│                     │  │  ## Developed Research Methods        ││
│                     │  │  **1.** …                             ││
│                     │  └───────────────────────────────────────┘│
└─────────────────────┴──────────────────────────────────────────┘
```

- **Left sidebar** — live checklist of the five pipeline steps; each step cycles through `pending → active → done` (or `error`).
- **Activity log** — streams all status events from the agent.
- **Results panel** — rendered Markdown showing the memory analysis insights and the list of new research methods. Hidden until the pipeline completes.

### Pipeline Steps

| Step | Phase | What happens |
|---|---|---|
| **1. Read methods** | `read_methods` | Reads `backend/instructions/RESEARCH-METHODS.md` to load the current research playbook as baseline context. |
| **2. Retrieve memories** | `retrieve_memories` | Queries the in-process long-term memory store (ChromaDB) for up to 100 flat memory entries, plus entity/relationship/community context from the knowledge graph. |
| **3. Analyse patterns** | `analyze` | Sends the memories and current methods to the model to identify recurring patterns, failure modes, coverage gaps, and emerging strategies. The knowledge graph layer highlights entity clusters and relationship patterns across sessions. |
| **4. Develop methods** | `develop` | Uses the analysis insights to generate a JSON list of actionable new research method descriptions. |
| **5. Save to memory** | `update_methods` | Rewrites `RESEARCH-METHODS.md` to incorporate the new methods, then persists an `optimization_insight` entry to the long-term memory store for use in future sessions. |

> **Note:** Long-term memory is an in-process ChromaDB store — no separate MCP server is required. If the store has not been initialised or is unavailable, the pipeline gracefully skips memory retrieval and the analysis phase works from `RESEARCH-METHODS.md` alone.

### Results Panel

After the pipeline finishes, the results panel renders:

1. **Memory Analysis Insights** — a free-text narrative summarising what patterns and themes were found across all stored memories.
2. **Developed Research Methods** — a numbered list of concrete, actionable method descriptions the agent generated based on the insights.

Both sections are rendered as Markdown. If the agent has no stored memories yet (i.e., no research runs have been completed), a message is shown explaining that more memories are needed before meaningful optimisation can occur.

---

## Keyboard Shortcuts

### Global

| Key | Action |
|---|---|
| `q` | Quit the application (from the mode picker) |

### Mode Picker

| Key | Action |
|---|---|
| `1` | Open Research mode |
| `2` | Open Chat mode |
| `3` | Open Self-Optimize mode |

### Research Mode

| Key | Action |
|---|---|
| `Escape` | Go back to mode picker |
| `Ctrl+S` | Save the completed report to `research_report.md` |

### Chat Mode

| Key | Action |
|---|---|
| `Escape` | Go back to mode picker |
| `Ctrl+L` | Clear conversation history |
| `Enter` (in input) | Send message |

### Self-Optimize Mode

| Key | Action |
|---|---|
| `Escape` | Go back to mode picker |

---

## Architecture Notes

### Bootstrap Sequence

When `cli.py` is invoked, the `main()` function runs the following setup *before* the TUI launches:

```
main()  (--depth passed as research_depth, --docs-dir resolved)
  └── asyncio.run(_bootstrap(research_depth))
        ├── Config()                                  # reads .env + env vars
        ├── create_mcp_registry(config)               # connects to MCP servers (best-effort)
        ├── AsyncLongTermMemory(persist_dir, …)       # opens ChromaDB store
        ├── long_term_memory.async_init()             # initialises collections + KG
        ├── create_model_backend(config)              # instantiates LLM client
        ├── AgentPool.from_single_backend(backend)    # creates sub-agent stubs
        ├── agent_pool.async_init(mcp_registry)       # populates tool specs per sub-agent
        └── Orchestrator(config, mcp_registry,        # final orchestrator instance
                         agent_pool=agent_pool,
                         long_term_memory=ltm,
                         research_depth=research_depth)
  └── Resolve docs_dir (--docs-dir > Config.docs_dir > None)
  └── ResearchApp(orchestrator, config, mcp_registry, # TUI application
                  long_term_memory, initial_mode,
                  initial_query, docs_dir)
```

All four objects (`orchestrator`, `config`, `mcp_registry`, `long_term_memory`) are passed into `ResearchApp` and shared across screens via `self.app.*`. The `docs_dir` is stored on `self.app.docs_dir` and can be changed at runtime via the `/docs` slash command. `SelfOptimizeScreen` receives `long_term_memory` directly so it can pass it to its `SelfOptimizingAgent` instance.

MCP server connections are attempted at startup but failures are non-fatal — the CLI will start even if MCP servers are offline, though features that depend on them (web search, file handling, memory) will not work until the servers are available.

### Async Workers

Every long-running operation (plan generation, execution, synthesis, chat, self-optimization) is dispatched as a **Textual worker** (`@work(exclusive=True, thread=False)`). This keeps the event loop responsive — the UI remains fully interactive (scroll, click, type) while agents are running.

Workers are `exclusive=True`, meaning only one research pipeline can run at a time per screen instance. The query input and run button are disabled while a worker is active to prevent concurrent pipeline invocations.

### Screen Stack

Textual uses a screen stack. Navigation works as follows:

```
ResearchApp
  └── ModePickerScreen          (pushed on mount if no --mode given)
        └── ResearchScreen      (pushed when Research selected)
         or ChatScreen          (pushed when Chat selected)
         or SelfOptimizeScreen  (pushed when Self-Optimize selected)
```

`Escape` pops the top screen, returning to the mode picker. The `Orchestrator` instance is shared across all screens — research context from a completed run persists if you navigate back to the mode picker and re-enter Research mode (until a new query is submitted, which resets the orchestrator's internal state).

---

## Relationship to the Web UI

The CLI and the web UI (`api_server.py` + `src/`) share the same `Orchestrator` class and identical `ResponseMessage` event types. The key differences:

| | CLI | Web UI |
|---|---|---|
| Transport | In-process async generator | WebSocket (`/ws/research`) |
| Plan approval | Buttons in TUI | Buttons in React frontend |
| Report rendering | Textual `Markdown` widget | `ReportViewer` React component |
| Report export | `Ctrl+S` → `.md` file | Download / copy in browser |
| Chat | Root backend only | Full chat via WebSocket |
| Self-Optimize | `SelfOptimizingAgent` pipeline | Not currently exposed in web UI |
| MCP servers | Required for full functionality | Required for full functionality |

The CLI is self-contained — it does **not** require the API server to be running.

---

## Troubleshooting

### `[ERROR] Initialisation failed: …`

Printed to stderr before the TUI launches. Common causes:

- **Missing `.env` file** — copy `.env.example` to `.env` and fill in the required keys for your chosen backend.
- **MCP server connection timeout** — the CLI will still start but agent tools will be unavailable. Start the MCP servers with `docker-compose up` or equivalent.
- **Wrong Python version** — the CLI requires Python 3.10+ for `match`/`case`-free type annotations.

### The TUI opens but research hangs indefinitely

- Check that your model backend is reachable. For Ollama, verify `ollama serve` is running and the model is pulled (`ollama pull <model>`).
- Check `backend/logs/` for detailed error output from the agent calls.

### `ModuleNotFoundError: No module named 'textual'`

```bash
pip install textual>=0.80.0
# or
pip install -r backend/requirements.txt
```

### Plan modification returns `"No pending plan to modify"`

This happens if the plan was denied or if a new query was submitted between generating the plan and clicking **Modify**. Re-submit the query to generate a fresh plan.

### Self-Optimize produces no results

The `SelfOptimizingAgent` requires at least some stored memories to work with. Run one or more research queries first — the Orchestrator stores intermediate findings in the in-process ChromaDB long-term memory store automatically. The store is located at the path set by `CHROMA_PERSIST_DIR` (default: `./chroma_data`). No separate memory server needs to be running.
