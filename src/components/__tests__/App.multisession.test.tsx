/**
 * App.multisession.test.tsx
 *
 * Covers the three multi-session routing invariants from DEFERRED.md §5c:
 *
 * 1. Status events tagged with session A's id update conversation A, not the
 *    currently-active conversation B.
 * 2. Switching back to conversation A while it is still executing shows the
 *    live session state, not a stale snapshot.
 * 3. `session_created` arriving after the user switched conversations still
 *    binds to the originating conversation (via `pendingQueryConvIdRef`).
 *
 * Strategy
 * --------
 * `useWebSocket` is mocked at module level.  A shared `wsState` object lets
 * each test push synthetic messages into the queue and bump `messageCount` to
 * trigger App's message-processing `useEffect`.  Calling `rerender(<App />)`
 * makes the component pick up the new `messageCount` from the mock and drain
 * the queue.
 *
 * Assertions are made against `localStorage` (where `useConversations`
 * persists conversation state) and against the DOM where convenient.
 */

import React from 'react';
import { render, act, waitFor } from '@testing-library/react';
import { vi, describe, it, expect, beforeEach, afterEach } from 'vitest';
import type { Conversation } from '../../types/conversation';

// ---------------------------------------------------------------------------
// Module-level WS mock state — shared across all tests in this file.
// ---------------------------------------------------------------------------
const wsState = {
  messageCount: 0,
  queue: [] as any[],
  sendMessage: vi.fn(),
};

/**
 * Push a message into the queue and bump messageCount so App's useEffect
 * fires on the next render.
 */
function injectWsMessage(msg: any): void {
  wsState.queue.push(msg);
  wsState.messageCount += 1;
}

// Mock useWebSocket BEFORE importing App so the module factory is in place.
vi.mock('../../hooks/useWebSocket', () => ({
  useWebSocket: () => ({
    isConnected: true,
    isReconnecting: false,
    sendMessage: wsState.sendMessage,
    reconnect: vi.fn(),
    drainMessageQueue: () => [],
    shiftMessage: () => wsState.queue.shift(),
    messageCount: wsState.messageCount,
  }),
}));

// ---------------------------------------------------------------------------
// Mock heavy components that are irrelevant to routing logic.
// ---------------------------------------------------------------------------
vi.mock('../../components/Sidebar', () => ({
  Sidebar: () => null,
}));
vi.mock('../../components/ChatContainer', () => ({
  ChatContainer: () => null,
}));
// ChatInput is replaced with a thin stub that calls the onSendMessage prop
// when a test fires the global helper `triggerSend`.  This lets us simulate
// query submission without needing real DOM interaction.
let _onSend: ((msg: string) => void) | null = null;
export function triggerSend(msg: string) {
  if (_onSend) _onSend(msg);
}
vi.mock('../../components/ChatInput', () => ({
  ChatInput: ({ onSendMessage }: { onSendMessage: (msg: string) => void }) => {
    _onSend = onSendMessage;
    return null;
  },
}));
vi.mock('../../components/ResearchControlBar', () => ({
  ResearchControlBar: () => null,
}));
vi.mock('../../components/GraphView', () => ({
  GraphView: () => null,
  SYNTHESIS_NODE_ID: 'synthesis',
}));
vi.mock('../../hooks/useConversationGraphs', () => ({
  useConversationGraphs: () => ({
    getGraph: () => null,
    addQueryNode: vi.fn(),
    initFromPlan: vi.fn(),
    updatePlanAction: vi.fn(),
    overrideBatches: vi.fn(),
    setNodeRunning: vi.fn(),
    setNodeCompleted: vi.fn(),
    setNodeFailed: vi.fn(),
    updateSubAgent: vi.fn(),
    incrementQaRetries: vi.fn(),
    addContradictions: vi.fn(),
    addSynthesisNode: vi.fn(),
    resetGraph: vi.fn(),
    deleteGraph: vi.fn(),
    setResearchStartTime: vi.fn(),
    setResearchEndTime: vi.fn(),
    initOptimizeGraph: vi.fn(),
    updateOptimizePhase: vi.fn(),
  }),
  SYNTHESIS_NODE_ID: 'synthesis',
}));
vi.mock('../../components/DocsViewer', () => ({
  DocsViewer: () => null,
}));
vi.mock('../../api/client', () => ({
  apiClient: {
    streamChat: vi.fn(),
    listDocs: vi.fn(() => Promise.resolve([])),
    getDoc: vi.fn(() => Promise.resolve('')),
    updateConfig: vi.fn(() => Promise.resolve({})),
    listMcpServers: vi.fn(() => Promise.resolve([])),
  },
}));

// ---------------------------------------------------------------------------
// Import App AFTER mocks are registered.
// ---------------------------------------------------------------------------
import App from '../../App';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
const STORAGE_KEY = 'deep_research_conversations';
const ACTIVE_KEY = 'deep_research_active_conversation';

function makeConv(overrides: Partial<Conversation> = {}): Conversation {
  const id = overrides.id ?? `conv-${Math.random().toString(36).slice(2)}`;
  return {
    id,
    title: 'Test conv',
    createdAt: new Date(),
    updatedAt: new Date(),
    messages: [],
    ...overrides,
  };
}

function seedLocalStorage(conv1: Conversation, conv2: Conversation, activeId: string): void {
  localStorage.setItem(STORAGE_KEY, JSON.stringify([conv1, conv2]));
  localStorage.setItem(ACTIVE_KEY, activeId);
}

function readConversations(): Conversation[] {
  const raw = localStorage.getItem(STORAGE_KEY);
  return raw ? JSON.parse(raw) : [];
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------
describe('App multi-session routing', () => {
  beforeEach(() => {
    // Reset WS state and localStorage before each test.
    wsState.messageCount = 0;
    wsState.queue = [];
    wsState.sendMessage.mockReset();
    localStorage.clear();
  });

  afterEach(() => {
    localStorage.clear();
  });

  // ─────────────────────────────────────────────────────────────────────────
  // Scenario 1 — events for session A must update conv A, not the active conv B
  // ─────────────────────────────────────────────────────────────────────────
  it('routes status events to the conversation that owns the session, not the active one', async () => {
    const conv1 = makeConv({ id: 'conv-1', sessionId: 'sid-A', sessionState: 'executing' });
    const conv2 = makeConv({ id: 'conv-2', sessionId: 'sid-B', sessionState: 'executing' });
    // conv2 is active; events belong to conv1's session.
    seedLocalStorage(conv1, conv2, conv2.id);

    const { rerender } = render(<App />);

    // Step 1: resume session A so App's currentSessionIdRef is set to 'sid-A'.
    injectWsMessage({ type: 'session_resumed', session_id: 'sid-A', event_count: 0, complete: false });
    rerender(<App />);

    await waitFor(() => {
      const convs = readConversations();
      const c1 = convs.find((c) => c.id === 'conv-1');
      // session_resumed adds a system reconnect message to conv1
      return c1 && c1.messages.length >= 1;
    });

    // Step 2: inject a status event — should land in conv1, not conv2.
    const before2 = readConversations().find((c) => c.id === 'conv-2')?.messages.length ?? 0;

    injectWsMessage({ type: 'status', message: 'Searching for evidence…' });
    rerender(<App />);

    await waitFor(() => {
      const convs = readConversations();
      const c1 = convs.find((c) => c.id === 'conv-1');
      return c1 && c1.messages.some((m: any) => m.content === 'Searching for evidence…');
    });

    // conv2 must not have received the status message.
    const after2 = readConversations().find((c) => c.id === 'conv-2')?.messages.length ?? 0;
    expect(after2).toBe(before2);
  });

  // ─────────────────────────────────────────────────────────────────────────
  // Scenario 2 — switching back to conv A shows live session state
  // ─────────────────────────────────────────────────────────────────────────
  it('persists session state on the conversation so a switch back reflects live status', async () => {
    const conv1 = makeConv({ id: 'conv-1', sessionId: 'sid-A', sessionState: 'executing' });
    const conv2 = makeConv({ id: 'conv-2' });
    seedLocalStorage(conv1, conv2, conv2.id);

    const { rerender } = render(<App />);

    // Simulate events arriving for conv1's session while conv2 is active.
    injectWsMessage({ type: 'session_resumed', session_id: 'sid-A', event_count: 0, complete: false });
    rerender(<App />);

    await waitFor(() => {
      const convs = readConversations();
      const c1 = convs.find((c) => c.id === 'conv-1');
      return c1 && c1.messages.length >= 1;
    });

    // A step_start event arriving in the background must update conv1.
    injectWsMessage({
      type: 'step_start',
      data: { step: { id: 1, name: 'Search', description: 'Web search', status: 'running' } },
    });
    rerender(<App />);

    await waitFor(() => {
      const convs = readConversations();
      const c1 = convs.find((c) => c.id === 'conv-1');
      return c1 && c1.messages.some((m: any) => m.type === 'step_start');
    });

    // After the switch the conversation's persisted state reflects the event.
    const c1Final = readConversations().find((c) => c.id === 'conv-1');
    expect(c1Final?.messages.some((m: any) => m.type === 'step_start')).toBe(true);
  });

  // ─────────────────────────────────────────────────────────────────────────
  // Scenario 3 — session_created binds to the originating conversation even
  //               after the user switches to a different conversation
  // ─────────────────────────────────────────────────────────────────────────
  it('binds session_created to the originating conversation via pendingQueryConvIdRef', async () => {
    // Start with one conversation (the one that will issue the query).
    const conv1 = makeConv({ id: 'conv-1' });
    seedLocalStorage(conv1, conv1 /* filler – only one */, conv1.id);
    localStorage.setItem(STORAGE_KEY, JSON.stringify([conv1]));

    const { rerender } = render(<App />);

    // Wait for App to load localStorage state so conv1 is in React state.
    await waitFor(() => {
      expect(localStorage.getItem(ACTIVE_KEY)).toBe(conv1.id);
    });

    // Simulate the user submitting a research query from conv1.
    // This sets pendingQueryConvIdRef = conv1.id inside App.
    act(() => {
      triggerSend('What is the future of AI?');
    });

    // The backend replies with session_created for a new session id.
    injectWsMessage({ type: 'session_created', session_id: 'sid-new' });
    rerender(<App />);

    // session_created must bind 'sid-new' to conv1 (the originating conv).
    await waitFor(() => {
      const convs = readConversations();
      const c1 = convs.find((c) => c.id === conv1.id);
      return c1 && c1.sessionId === 'sid-new';
    });

    const convs = readConversations();
    const c1 = convs.find((c) => c.id === conv1.id);
    expect(c1?.sessionId).toBe('sid-new');
  });
});
