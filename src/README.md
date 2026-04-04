# Frontend — Research Assistant UI

React/TypeScript single-page application that connects to the backend over WebSocket and provides the research chat interface, real-time agent graph, and report viewer.

---

## Tech Stack

| Tool | Version | Role |
|------|---------|------|
| React | 18 | UI framework |
| TypeScript | 5 | Type safety |
| Vite | 5 | Dev server + bundler |
| Tailwind CSS | 3 | Utility-first styling |
| lucide-react | 0.294 | Icon library |
| react-markdown + remark-gfm | — | Markdown rendering (reports, messages) |
| @react-pdf/renderer | 4 | PDF export for research reports |
| vitest | 1 | Unit test runner |

---

## Directory Layout

```
src/
├── App.tsx                    Root component — WebSocket dispatch, top-level state wiring
├── index.tsx                  Entry point
├── index.css                  Global styles (Tailwind base)
├── index.html                 HTML shell
│
├── api/
│   └── client.ts              REST client (plan approval, MCP server management)
│
├── components/
│   ├── ChatContainer.tsx       Message list rendering
│   ├── ChatInput.tsx           Query input, mode toggle (research/chat), Self-Optimize button
│   ├── ChatMessage.tsx         Individual message bubble
│   ├── PlanMessage.tsx         Plan approval / modify / deny UI
│   ├── SynthesisMessage.tsx    Synthesis and section-draft message rendering
│   ├── ResearchReportViewer.tsx Full report display + PDF export
│   ├── ResearchReportDocument.tsx @react-pdf document definition
│   ├── GraphView.tsx           Real-time agent execution graph (teal nodes for optimize)
│   ├── ResearchControlBar.tsx  Pause / resume / stop controls
│   ├── Sidebar.tsx             Conversation history list + tab nav (conversations/files/servers/settings)
│   ├── StepResultViewer.tsx    Per-step result panel (collapsible inline)
│   ├── MCPServerManger.tsx     MCP server status and management UI — shows builtins + user-added servers;
│   │                             transport selector (Streamable HTTP / SSE / stdio) in add form;
│   │                             delete for user-added servers, lock icon for builtins
│   ├── FileUploader.tsx        File upload for file-handler MCP context
│   ├── ProgressSpinner.tsx     Research progress spinner
│   └── ErrorBoundary.tsx       Top-level React error boundary
│
├── contexts/
│   └── AppContext.tsx          Global state: messages, conversations, research/plan status
│
├── hooks/
│   ├── useWebSocket.ts         WebSocket lifecycle (connect, reconnect, send)
│   ├── useConversations.ts     Conversation CRUD (local state)
│   ├── useResearchControl.ts   Pause/resume/stop via research_control WS messages
│   ├── useConversationGraphs.ts Per-conversation agent execution graph state
│   ├── useTypingEffect.ts      Streaming text typing animation
│   └── useTheme.ts             Light/dark theme toggle (persisted to localStorage)
│
├── types/                      Shared TypeScript type declarations
│
├── config/
│   └── theme.ts                Theme tokens
│
├── vite.config.ts              Vite config (port 3232, API/WS proxy, alias @→src/)
├── tailwind.config.js          Tailwind config
├── tsconfig.json               TypeScript config
└── postcss.config.js           PostCSS config
```

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `VITE_API_URL` | `http://localhost:9999` | Backend REST API base URL |
| `VITE_WS_URL` | `ws://localhost:9999/ws/research` | Backend WebSocket endpoint |

Create a `.env.local` file in `src/` (or the repo root, Vite resolves both) to override:

```env
VITE_WS_URL=ws://localhost:9999/ws/research
```

In Docker the value is set via the `nginx.conf` environment substitution at container startup — see the `src/Dockerfile`.

---

## Development

```bash
# From the repo root
make run-fe          # Installs deps and starts the Vite dev server on :3232
```

Or manually:

```bash
cd src
npm install
npm run dev          # http://localhost:3232
```

The dev server proxies:

- `/api/*` → `http://localhost:9999` (backend REST)
- `/ws/*` → `ws://localhost:9999` (backend WebSocket)

so the backend must be running locally (or in Docker) on port `9999`.

---

## Available Scripts

| Command | Description |
|---------|-------------|
| `npm run dev` | Start Vite dev server on port 3232 |
| `npm run build` | Type-check then build to `dist/` |
| `npm run preview` | Serve the production build locally |
| `npm run test` | Run tests with vitest |
| `npm run test:ui` | Run tests with vitest UI |
| `npm run lint` | TypeScript type-check (no emit) |

---

## Production Build (Docker)

The frontend is served by nginx inside a multi-stage Docker image:

```bash
# From the repo root — builds and starts all services
make run-all
```

The nginx config (`src/nginx.conf`) proxies `/api` and `/ws` to the backend container and serves the built static assets. The compiled output lands in `/usr/share/nginx/html` inside the container.

---

## WebSocket Message Handling

All incoming WebSocket messages are dispatched in `App.tsx`. Key types:

| Type | Effect |
|------|--------|
| `session_created` / `session_resumed` | Stores backend session ID for reconnection |
| `plan` | Renders `PlanMessage` — user must approve/modify/deny before research starts |
| `step_start` / `step_complete` | Updates graph nodes and step progress |
| `synthesis` / `section_draft` | Streams intermediate synthesis content |
| `report` / `research_complete` | Renders the final `ResearchReportViewer` |
| `research_stopped` / `error` | Terminates the research timer and shows status |
| `optimize_started` / `optimize_progress` / `optimize_complete` | Self-optimization workflow (teal graph nodes) |

Outgoing message types sent by the frontend:

| Type | Trigger |
|------|---------|
| `research_query` | User submits a query |
| `plan_approved` / `plan_modified` / `plan_denied` | User interacts with the plan card |
| `research_control` | Pause / resume / stop from `ResearchControlBar` |
| `self_optimize` | "Self-Optimize" button in `ChatInput` |

---

## State Architecture

```
AppContext (AppProvider)
  └─ messages[]            — flat list for the active conversation view
  └─ conversations[]       — all conversation metadata
  └─ activeConversationId  — currently visible conversation
  └─ isResearching         — true while a pipeline is running
  └─ planStatus            — 'pending' | 'approved' | 'denied' | null

App.tsx
  └─ useWebSocket          — manages the WS connection
  └─ useResearchControl    — sends pause/resume/stop
  └─ useConversationGraphs — graph state per conversation
  └─ useTheme              — light/dark
```

All per-conversation graph state is keyed by `conversationId` and lives outside `AppContext` to avoid unnecessary re-renders on every graph update.
