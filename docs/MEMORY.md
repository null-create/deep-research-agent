# Agent Memory — Research Assistant

Compressed, curated knowledge of the project's history, decisions, and patterns. Loaded fully at the start of every session. Updated only when something worth preserving is learned.

## [2026-09-29]
- **Dead files on disk (awaiting operator `rm`):** `src/components/MCPServerManger.tsx` (typo), `src/components/MCPServerManager.tsx`, `src/components/ReportViewer.tsx`, `backend/metrics.py`. The 2026-03-18 changelog entry incorrectly claimed `ReportViewer.tsx` was deleted — it was not. Corrected in PROJECT-KNOWLEDGE.md.
- **`_recover_session_from_checkpoint` (api_server.py)** has 4 recovery cases: (1) synthesis recovery, (2) execute recovery (state=executing, plan_checkpoint present, no synthesis_checkpoint), (3) plan-approval recovery, (4) replay-only. Case 2 was previously untested — now covered by `TestRecoverExecutingSession` in `unit_tests.py`.
- **WS resume integration tests** now live in `unit_tests.py::TestWebSocketResume` using Starlette `TestClient` + injected app state (bypasses lifespan). Pattern: create a fresh `FastAPI()`, copy the WS route, inject mocked state directly onto `app.state`.
- **Frontend test infra**: vitest 1.x is incompatible with vite 7.x (ESM conflict). Solution: keep a separate `vitest.config.ts` importing from `vitest/config` (not `vite`), which vitest prefers over `vite.config.ts`. Upgraded vitest to ^3.0.0.
- **App multi-session routing tests** (`src/components/__tests__/App.multisession.test.tsx`): mock `useWebSocket` at module level with a shared `wsState` object; call `rerender(<App />)` after bumping `wsState.messageCount` to trigger the message-processing `useEffect`. Assert routing by reading `localStorage` (where `useConversations` persists state). To test `pendingQueryConvIdRef`, mock `ChatInput` to capture the `onSendMessage` prop (not `onSend`) and expose an imperative `triggerSend()` helper.
- **DEFERRED items still open**: §2 (dedup curated context — needs benchmark), §6 (sentence-transformer dedup — optional), §7 (analyst-fallback truncation fix — needs log data).

## [YYYY-MM-DD]
- <insight>
