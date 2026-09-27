import React, { useEffect, useRef, useState, useCallback } from 'react';
import { AppProvider, useApp } from './contexts/AppContext';
import { useWebSocket } from './hooks/useWebSocket';
import { useTheme } from './hooks/useTheme';
import { Sidebar, ResearchDepth } from './components/Sidebar';
import { ChatContainer } from './components/ChatContainer';
import { ChatInput } from './components/ChatInput';
import { ErrorBoundary } from './components/ErrorBoundary';
import { useResearchControl } from './hooks/useResearchControl';
import { ResearchControlBar } from './components/ResearchControlBar';
import { GraphView } from './components/GraphView';
import { useConversationGraphs, SYNTHESIS_NODE_ID } from './hooks/useConversationGraphs';
import { DocsViewer } from './components/DocsViewer';
import { apiClient } from './api/client';

const WS_URL = import.meta.env.VITE_WS_URL || `${location.protocol.replace('http', 'ws')}//${location.host}/ws/research`;

// localStorage keys — survive browser close so the session can be resumed.
const LS_SESSION_ID = 'deep_research_session_id';
const LS_CONV_ID = 'deep_research_conv_id';

/**
 * crypto.randomUUID() requires a secure context (https or localhost).
 * Fall back to a simple pseudo-random id on plain http (e.g. LAN dev).
 */
function generateId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}-${Math.random().toString(36).slice(2)}`;
}

const AppContent: React.FC = () => {
  const {
    messages,
    addMessage,
    updateMessage,
    createConversation,
    addMessageToConv,
    updateMessageInConv,
    patchMessageInConv,
    isResearching,
    setIsResearching,
    conversations,
    activeConversationId,
    selectConversation,
    startNewChat,
    renameConversation,
    deleteConversation: deleteConversationFromCtx,
    planStatus,
    setPlanStatus,
    setEventIndex,
    getEventIndex,
  } = useApp();

  const [inputMode, setInputMode] = useState<'research' | 'chat'>('research');
  // Tracks which conversation owns the currently running research session.
  // Pre-populated from localStorage so a page refresh (or browser re-open)
  // can resume seamlessly.
  const researchConvIdRef = useRef<string | null>(localStorage.getItem(LS_CONV_ID));
  // Tracks the active backend session_id so we can resume after a disconnect.
  // Pre-populated from localStorage so it survives browser close.
  const activeSessionIdRef = useRef<string | null>(localStorage.getItem(LS_SESSION_ID));
  // Counts how many replay events are still outstanding after a resume.
  // addMessageToConv calls are suppressed while this is > 0 to prevent
  // duplicating messages already persisted in localStorage conversation history.
  const replayCountRef = useRef<number>(0);
  // Total events in the current replay batch (set from session_resumed.event_count).
  // Used to detect when replay finishes and update storedEventIndex.
  const replayTotalRef = useRef<number>(0);
  // The event_count from the most recent session_resumed message, retained so we
  // can persist it to the Conversation's _eventIndex once replay completes.
  const pendingTotalEventsRef = useRef<number>(0);
  // Running count of ALL non-control WebSocket events received for the current
  // session.  Incremented during normal operation and during replay (for
  // non-suppressed events only).  Flushed to the Conversation's _eventIndex
  // field in localStorage so it survives browser close.
  const activeEventIndexRef = useRef<number>(0);

  const [currentStatus, setCurrentStatus] = useState<string>('Researching');
  const [sidebarOpen, setSidebarOpen] = useState<boolean>(() =>
    typeof window !== 'undefined' && window.innerWidth >= 768
  );
  const [researchDepth, setResearchDepth] = useState<ResearchDepth>('shallow');
  const { theme, toggleTheme } = useTheme();
  const [currentPlanID, setCurrentPlanID] = useState<string | null>(null);
  const [activeView, setActiveView] = useState<'chat' | 'graph'>('chat');
  const [isOptimizing, setIsOptimizing] = useState<boolean>(false);
  const [showDocs, setShowDocs] = useState<boolean>(false);
  // Tracks the conversation that hosts the current self-optimize workflow graph
  const optimizeConvIdRef = useRef<string | null>(null);

  const {
    getGraph,
    addQueryNode,
    initFromPlan,
    updatePlanAction,
    overrideBatches,
    setNodeRunning,
    setNodeCompleted,
    setNodeFailed,
    updateSubAgent,
    incrementQaRetries,
    addContradictions,
    addSynthesisNode,
    resetGraph,
    deleteGraph,
    setResearchStartTime,
    setResearchEndTime,
    initOptimizeGraph,
    updateOptimizePhase,
  } = useConversationGraphs();

  // Wrap deleteConversation to also clean up the stored graph for that conversation
  const deleteConversation = useCallback(
    (id: string) => {
      deleteConversationFromCtx(id);
      deleteGraph(id);
    },
    [deleteConversationFromCtx, deleteGraph]
  );

  // Called by useWebSocket whenever the socket re-connects after a previous
  // disconnect.  If a session was active we ask the backend to resume it so
  // the user picks up exactly where they left off.
  const handleReconnected = useCallback(() => {
    const sessionId = activeSessionIdRef.current;
    if (!sessionId) return;
    console.log('[ws] Re-connected — resuming session', sessionId);
    sendMessageRef.current?.({ type: 'resume', session_id: sessionId });
  }, []);

  // Called by useWebSocket on the very first connection (i.e. page load / hard
  // refresh).  If a session_id was persisted from before the refresh we ask
  // the backend to resume it, which causes the full replay log to be streamed
  // back so the UI can restore its live state.
  const handleInitialConnect = useCallback(() => {
    const sessionId = localStorage.getItem(LS_SESSION_ID);
    if (!sessionId) return;
    console.log('[ws] Initial connect — resuming stored session', sessionId);
    sendMessageRef.current?.({ type: 'resume', session_id: sessionId });
  }, []);

  // Needed so handleReconnected can call sendMessage before it's assigned.
  const sendMessageRef = useRef<((msg: any) => void) | null>(null);

  const {
    isConnected,
    isReconnecting,
    drainMessageQueue,
    sendMessage,
    reconnect,
    messageCount,
    shiftMessage,
  } = useWebSocket(WS_URL, { onReconnected: handleReconnected, onInitialConnect: handleInitialConnect });

  // Keep the ref in sync so handleReconnected can always see the latest sendMessage.
  useEffect(() => {
    sendMessageRef.current = sendMessage;
  }, [sendMessage]);

  // ── Research control (pause / resume / stop) ─────────────────────────────
  const {
    researchStatus,
    startResearch,
    pauseResearch,
    resumeResearch,
    stopResearch,
    resetResearch,
  } = useResearchControl({
    sendMessage,
    activeConversationId,
    setIsResearching,
  });

  // Helper: find the most recent plan_approval message in chat history
  const findPlanMessage = useCallback(() => {
    return [...messages].reverse().find((m) => m.type === 'plan_approval');
  }, [messages]);

  // Whenever we switch to a different conversation, reset research state
  // useEffect(() => {
  //   resetResearch();
  //   setIsResearching(false);
  // }, [activeConversationId]);

  // =====================
  // WebSocket message handler
  // =====================

  useEffect(() => {
    const rawMessage = shiftMessage();
    if (!rawMessage) return;

    const { type, data, message, plan } = rawMessage;
    console.log('Received WebSocket message:', rawMessage);

    if (!type) {
      console.warn('Received message with no type:', rawMessage);
      return;
    }

    // Track event index: every non-control event from the backend
    // (whether from the initial stream or from a replay drain) counts
    // as one event.  We use this to know how many events are already
    // persisted in localStorage after a future reconnect.
    //
    // During replay the total event count is known upfront
    // (replayTotalRef.current).  Events with an index below
    // replayCountRef.current are already in localStorage and are
    // suppressed to avoid duplication.  Events above that threshold
    // are new (emitted while we were disconnected) and flow through.
    let isReplayedEvent = false;
    const isControlEvent =
      type === 'session_created' || type === 'session_resumed';
    if (!isControlEvent) {
      if (replayTotalRef.current > 0) {
        replayTotalRef.current--;
      }
      if (replayCountRef.current > 0) {
        replayCountRef.current--;
        isReplayedEvent = true;
      }
    }

    switch (type) {
      // ── Session lifecycle ──────────────────────────────────────────────
      case 'session_created':
        // Store the session_id + conv_id in localStorage so we can send a
        // resume message on reconnect, hard refresh, or even browser re-open.
        activeSessionIdRef.current = rawMessage.session_id ?? null;
        if (rawMessage.session_id) {
          localStorage.setItem(LS_SESSION_ID, rawMessage.session_id);
        }
        // Reset the event index for this brand-new session
        activeEventIndexRef.current = 0;
        if (researchConvIdRef.current) {
          setEventIndex(researchConvIdRef.current, 0);
        }
        console.log('[ws] Session created:', rawMessage.session_id);
        break;

      case 'session_resumed': {
        const totalEvents = rawMessage.event_count ?? 0;
        console.log('[ws] Session resumed:', rawMessage.session_id, '— total events:', totalEvents);
        // Restore session refs that may have been lost on a hard page refresh
        // or browser re-open.
        if (rawMessage.session_id) {
          activeSessionIdRef.current = rawMessage.session_id;
        }
        const storedConvId = localStorage.getItem(LS_CONV_ID);
        if (!researchConvIdRef.current && storedConvId) {
          researchConvIdRef.current = storedConvId;
        }
        // Decide how many replay events to suppress.  Events with an index
        // below the storedEventIndex are already in localStorage and should
        // NOT produce duplicate messages.  Events above it are new (emitted
        // while the browser was closed) and MUST flow through normally.
        const storedIndex = researchConvIdRef.current
          ? getEventIndex(researchConvIdRef.current)
          : 0;
        const eventsToSuppress = Math.min(storedIndex, totalEvents);
        replayCountRef.current = eventsToSuppress;
        replayTotalRef.current = totalEvents;
        pendingTotalEventsRef.current = totalEvents;
        setCurrentStatus(
          rawMessage.complete ? 'Research Complete' : 'Reconnected — resuming…'
        );
        // Keep the researching flag true during resume if the session is
        // still live (so the UI shows the spinner / control bar).
        if (!rawMessage.complete) {
          setIsResearching(true);
        }
        // Notify the user that we've reconnected successfully.
        if (researchConvIdRef.current) {
          addMessageToConv(researchConvIdRef.current, {
            id: generateId(),
            role: 'system',
            type: 'system',
            content: rawMessage.complete
              ? '🔄 Reconnected. Research had already finished — replaying results.'
              : '🔄 Reconnected to backend. Resuming research in progress…',
            timestamp: new Date(),
          });
        }
        break;
      }

      case 'status':
        console.log('Status update:', message);
        setCurrentStatus(message || data?.message || 'Researching');

        // Graph: explicit batch data from the v2 orchestrator
        if (data?.batches) {
          overrideBatches(researchConvIdRef.current!, data.batches);
        }
        // Graph: parse sub-agent progress lines emitted by the orchestrator
        if (message) {
          // [Search] any message for step N → search running
          // Matches both initial gather and targeted re-search
          const searchMatch = message.match(/\[Search\].*step\s+(\d+)/i);
          if (searchMatch) {
            updateSubAgent(researchConvIdRef.current!, parseInt(searchMatch[1], 10), 'search', 'running');
          }
          // [Analyst] any message for step N → search completed, analyst running
          // Matches both "Extracting claims" and "Re-extracting claims"
          const analystMatch = message.match(/\[Analyst\].*step\s+(\d+)/i);
          if (analystMatch) {
            updateSubAgent(researchConvIdRef.current!, parseInt(analystMatch[1], 10), 'search', 'completed');
            updateSubAgent(researchConvIdRef.current!, parseInt(analystMatch[1], 10), 'analyst', 'running');
          }
          // [QA] Auditing → analyst completed, QA running
          const qaAuditMatch = message.match(/\[QA\] Auditing.*step\s+(\d+)/i);
          if (qaAuditMatch) {
            updateSubAgent(researchConvIdRef.current!, parseInt(qaAuditMatch[1], 10), 'analyst', 'completed');
            updateSubAgent(researchConvIdRef.current!, parseInt(qaAuditMatch[1], 10), 'qa', 'running');
          }
          // [QA] contradiction(s) flagged → attach contradiction data
          const qaFlagMatch = message.match(/\[QA\].*contradiction.*step\s+(\d+)/i);
          if (qaFlagMatch) {
            addContradictions(researchConvIdRef.current!, parseInt(qaFlagMatch[1], 10), data?.contradictions ?? []);
          }
          // [QA retry N/N] → increment retry badge; search/analyst transitions
          // are handled by the subsequent [Search] and [Analyst] messages above
          const qaRetryMatch = message.match(/\[QA retry.*?\].*step\s+(\d+)/i);
          if (qaRetryMatch) {
            incrementQaRetries(researchConvIdRef.current!, parseInt(qaRetryMatch[1], 10));
          }
          // Synthesis phases starting — synthesis node is already running
          // (set to running in the research_complete handler below)
        }
        break;

      case 'plan': {
        console.log('Received research plan:', plan);
        setCurrentStatus('Planning');
        setIsResearching(false);
        setPlanStatus('pending');

        // Build initial graph from the incoming plan only for live events;
        // during replay the graph is already restored from localStorage.
        if (plan && !isReplayedEvent) {
          initFromPlan(researchConvIdRef.current!, plan);
        }

        if (!isReplayedEvent) {
          addMessageToConv(researchConvIdRef.current!, {
            id: generateId(),
            role: 'assistant',
            type: 'plan_approval',
            content: 'I have created a research plan. Please review it below.',
            data: {
              plan: plan,
              planAction: 'pending',
            },
            timestamp: new Date(),
          });
        }
        break;
      }

      case 'plan_denied':
        console.log('Plan denied by user.');
        setPlanStatus('denied');
        setIsResearching(false);
        setCurrentStatus('Plan Denied');
        activeSessionIdRef.current = null;
        localStorage.removeItem(LS_SESSION_ID);
        localStorage.removeItem(LS_CONV_ID);
        resetResearch();
        resetGraph(researchConvIdRef.current!);

        if (!isReplayedEvent) {
          addMessageToConv(researchConvIdRef.current!, {
            id: generateId(),
            role: 'assistant',
            type: 'system',
            content: 'The research plan was denied. Please submit a new query to start again.',
            timestamp: new Date(),
          });
        }
        break;

      case 'step_start':
        console.log('Starting step:', data?.step);
        setIsResearching(true);
        setCurrentStatus('Starting Step: ' + (data?.step?.name || 'Unknown Step'));
        // Graph: mark this node as running
        if (data?.step?.id != null) {
          setNodeRunning(researchConvIdRef.current!, data.step.id);
        }

        if (!isReplayedEvent) {
          addMessageToConv(researchConvIdRef.current!, {
            id: generateId(),
            role: 'assistant',
            type: 'step_start',
            content: `Starting step: ${data?.step?.description || 'Unknown Step'}`,
            data,
            timestamp: new Date(),
          });
        }
        break;

      case 'step_complete':
        console.log('Completed step:', data?.step);
        setCurrentStatus('Step Completed: ' + (data?.step?.name || 'Unknown Step'));
        // Graph: mark node completed, attach result & tools
        if (data?.step?.id != null) {
          setNodeCompleted(
            researchConvIdRef.current!,
            data.step.id,
            data.step.result,
            Array.isArray(data.tools_used) ? data.tools_used : undefined
          );
        }
        if (!isReplayedEvent) {
          addMessageToConv(researchConvIdRef.current!, {
            id: generateId(),
            role: 'assistant',
            type: 'step_complete',
            content: data?.skipped_synthesis
              ? `Skipped step: ${data?.step?.description || 'synthesis step'} — report synthesis is handled automatically by the ReportComposer.`
              : `Completed step: ${data?.step?.name || 'Unknown Step'}`,
            data,
            timestamp: new Date(),
          });
        }
        break;

      case 'step_failed': {
        // The backend sends `error` and `message` at the top level; `data` may be null
        const stepError = rawMessage.error;
        console.log('Step failed:', message, 'Error:', stepError);
        setCurrentStatus('Step Failed');
        // Graph: mark node failed
        if (data?.step?.id != null) {
          setNodeFailed(researchConvIdRef.current!, data.step.id, stepError ?? undefined);
        }
        if (!isReplayedEvent) {
          addMessageToConv(researchConvIdRef.current!, {
            id: generateId(),
            role: 'assistant',
            type: 'step_failed',
            content: `${message || 'Step failed'}. Error: ${stepError || 'Unknown error.'}`,
            data: {
              step: {
                name: message || 'Unknown Step',
                description: message || '',
                error: stepError || 'Unknown error.',
              },
            },
            timestamp: new Date(),
          });
        }
        break;
      }

      case 'research_complete':
        console.log('Research complete:', data);
        setIsResearching(false);
        setCurrentStatus('Complete');
        // Freeze the timer — pipeline is done
        setResearchEndTime(researchConvIdRef.current!, new Date().toISOString());
        // Add the synthesis node and mark it running only for live events;
        // during replay the graph node already exists in localStorage.
        if (!isReplayedEvent) {
          addSynthesisNode(researchConvIdRef.current!);
          setNodeRunning(researchConvIdRef.current!, SYNTHESIS_NODE_ID);
        }
        // Session is still alive until 'report' arrives; keep activeSessionIdRef.
        if (!isReplayedEvent) {
          addMessageToConv(researchConvIdRef.current!, {
            id: generateId(),
            role: 'assistant',
            type: 'research_complete',
            content: message || (data?.content ?? 'Research complete.'),
            data,
            timestamp: new Date(),
          });
        }
        break;

      case 'synthesis': {
        console.log('Received synthesis result:', data);
        setCurrentStatus('Synthesized Results');
        // Compose a complete markdown summary so the PDF report captures all sections
        const synthParts: string[] = [];
        if (data?.summary) synthParts.push(`## Summary\n${data.summary}`);
        if (data?.key_insights?.length) synthParts.push(`## Key Insights\n${(data.key_insights as string[]).map((i: string) => `- ${i}`).join('\n')}`);
        if (data?.patterns?.length) synthParts.push(`## Patterns\n${(data.patterns as string[]).map((p: string) => `- ${p}`).join('\n')}`);
        if (data?.recommendations?.length) synthParts.push(`## Recommendations\n${(data.recommendations as string[]).map((r: string) => `- ${r}`).join('\n')}`);
        if (data?.creative_applications?.length) synthParts.push(`## Creative Applications\n${(data.creative_applications as string[]).map((a: string) => `- ${a}`).join('\n')}`);
        if (data?.knowledge_gaps?.length) synthParts.push(`## Knowledge Gaps\n${(data.knowledge_gaps as string[]).map((g: string) => `- ${g}`).join('\n')}`);
        const formattedSummary = synthParts.join('\n\n') || data?.summary || message || 'No synthesis data available.';
        if (!isReplayedEvent) {
          addMessageToConv(researchConvIdRef.current!, {
            id: generateId(),
            role: 'assistant',
            type: 'synthesis',
            content: message || 'Synthesis complete.',
            data: {
              synthesis: {
                title: 'Research Report',
                summary: formattedSummary,
                generatedAt: new Date().toISOString(),
                key_insights: data?.key_insights,
                patterns: data?.patterns,
                recommendations: data?.recommendations,
                creative_applications: data?.creative_applications,
                knowledge_gaps: data?.knowledge_gaps,
              },
            },
            timestamp: new Date(),
          });
        }
        break;
      }

      case 'error':
        console.error('Error received:', message, data);
        setIsResearching(false);
        setCurrentStatus('Step Failed');
        // If the error came from a failed resume attempt, clear the stored
        // session so we don't keep retrying on the next page load.
        localStorage.removeItem(LS_SESSION_ID);
        localStorage.removeItem(LS_CONV_ID);
        activeSessionIdRef.current = null;
        if (researchConvIdRef.current) {
          setResearchEndTime(researchConvIdRef.current, new Date().toISOString());
        }
        if (!isReplayedEvent && researchConvIdRef.current) {
          addMessageToConv(researchConvIdRef.current, {
            id: generateId(),
            role: 'assistant',
            type: 'error',
            content: message || (data?.message ?? 'An unexpected error occurred.'),
            data: data ?? {},
            timestamp: new Date(),
          });
        }
        break;

      case 'synthesis_progress': {
        const secIdx = data?.section_index as number | undefined;
        const secTitle = data?.section_title as string | undefined;
        const totalSecs = data?.total_sections as number | undefined;
        const progressMsg = secIdx && totalSecs
          ? `Drafting section ${secIdx} of ${totalSecs}: ${secTitle ?? ''}…`
          : message || 'Drafting report…';
        setCurrentStatus(progressMsg);
        // Re-enable the "in progress" spinner so users see activity between
        // research_complete and the final report event.
        if (secIdx === 1) {
          setIsResearching(true);
        }
        break;
      }

      case 'report': {
        // v2 Orchestrator endpoint — full document in data.document
        console.log('Received research report:', data);
        setIsResearching(false);
        setCurrentStatus('Report Ready');
        // Session is fully complete — clear stored IDs so future page loads
        // do not attempt to resume a finished session.
        activeSessionIdRef.current = null;
        localStorage.removeItem(LS_SESSION_ID);
        localStorage.removeItem(LS_CONV_ID);
        // Mark the synthesis graph node as completed
        setNodeCompleted(researchConvIdRef.current!, SYNTHESIS_NODE_ID);
        // Timer: mark end time
        setResearchEndTime(researchConvIdRef.current!, new Date().toISOString());
        if (!isReplayedEvent) {
          addMessageToConv(researchConvIdRef.current!, {
            id: generateId(),
            role: 'assistant',
            type: 'report',
            content: message || 'Research report complete.',
            data: {
              synthesis: {
                title: data?.title ?? 'Research Report',
                summary: data?.document ?? message ?? 'No report content available.',
                key_findings: data?.key_findings,
                sources: data?.sources,
                generatedAt: new Date().toISOString(),
              },
            },
            timestamp: new Date(),
          });
        }
        break;
      }

      case 'research_paused':
        console.log('Research paused acknowledged:', message);
        setCurrentStatus('Paused');
        break;

      case 'research_resumed':
        console.log('Research resumed acknowledged:', message);
        setCurrentStatus('Researching');
        break;

      case 'research_stopped':
        console.log('Research stopped acknowledged:', message);
        setIsResearching(false);
        setCurrentStatus('');
        activeSessionIdRef.current = null;
        localStorage.removeItem(LS_SESSION_ID);
        localStorage.removeItem(LS_CONV_ID);
        setResearchEndTime(researchConvIdRef.current!, new Date().toISOString());
        resetResearch();
        resetGraph(researchConvIdRef.current!);
        break;

      // ── Self-optimization workflow ──────────────────────────────────────
      case 'optimize_started':
        console.log('[optimize] Workflow started');
        setIsOptimizing(true);
        setCurrentStatus('Self-Optimizing…');
        setActiveView('graph');
        break;

      case 'optimize_progress': {
        if (isReplayedEvent) break;
        const phase = data?.phase;
        const phaseStatus = data?.status;
        console.log('[optimize] Phase:', phase, phaseStatus);
        if (phase && phaseStatus && optimizeConvIdRef.current) {
          const nodeStatus = phaseStatus === 'running' ? 'running'
            : phaseStatus === 'completed' ? 'completed'
              : 'failed';
          updateOptimizePhase(optimizeConvIdRef.current, phase, nodeStatus);
        }
        setCurrentStatus(message || 'Optimizing…');
        addMessageToConv(optimizeConvIdRef.current!, {
          id: generateId(),
          role: 'assistant',
          type: 'system',
          content: message || 'Optimization in progress…',
          timestamp: new Date(),
        });
        break;
      }

      case 'optimize_complete': {
        if (isReplayedEvent) break;
        console.log('[optimize] Complete:', data);
        setIsOptimizing(false);
        setCurrentStatus('Optimization Complete');
        const methodsCount = data?.new_methods_count ?? 0;
        addMessageToConv(optimizeConvIdRef.current!, {
          id: generateId(),
          role: 'assistant',
          type: 'system',
          content: `✅ Self-optimization complete. ${methodsCount} new research method(s) integrated into RESEARCH-METHODS.md.`,
          timestamp: new Date(),
        });
        break;
      }

      default:
        console.warn('Unknown WebSocket message type:', type);
        break;
    }

    // ── Event index tracking ───────────────────────────────────────
    // Every non-control non-replayed event increments the running
    // counter so we always know how many events are accounted for
    // in the persisted conversation state.
    if (!isControlEvent && !isReplayedEvent) {
      activeEventIndexRef.current++;
    }

    // ── Post-batch bookkeeping ───────────────────────────────────────
    // If a replay was in progress and all events have been drained,
    // persist the final total so future reconnects are accurate.
    if (
      pendingTotalEventsRef.current > 0 &&
      replayTotalRef.current === 0 &&
      researchConvIdRef.current
    ) {
      setEventIndex(researchConvIdRef.current, pendingTotalEventsRef.current);
      pendingTotalEventsRef.current = 0;
    }

    // Flush the running event index to conversation metadata every
    // event so the persisted index is always up-to-date.
    if (researchConvIdRef.current) {
      setEventIndex(researchConvIdRef.current, activeEventIndexRef.current);
    }
  }, [
    messageCount,
    shiftMessage,
    drainMessageQueue,
    addMessageToConv,
    setIsResearching,
    setPlanStatus,
    resetResearch,
    addQueryNode,
    initFromPlan,
    updatePlanAction,
    overrideBatches,
    setNodeRunning,
    setNodeCompleted,
    setNodeFailed,
    updateSubAgent,
    incrementQaRetries,
    addContradictions,
    addSynthesisNode,
    resetGraph,
    setResearchStartTime,
    setResearchEndTime,
    updateOptimizePhase,
    getEventIndex,
    setEventIndex,
    // researchConvIdRef / optimizeConvIdRef / replayTotalRef /
    // pendingTotalEventsRef / activeEventIndexRef are refs;
    // no need to list them
  ]);

  // =====================
  // Self-optimize trigger
  // =====================
  const handleSelfOptimize = useCallback(() => {
    if (isOptimizing || isResearching) return;
    // Create or reuse a conversation for the optimize workflow
    let convId = activeConversationId;
    if (!convId) {
      const newConv = createConversation();
      convId = newConv.id;
    }
    optimizeConvIdRef.current = convId;
    // Build the 5-phase graph so it appears immediately in the graph view
    initOptimizeGraph(convId);
    setActiveView('graph');
    addMessageToConv(convId, {
      id: generateId(),
      role: 'user',
      type: 'user',
      content: '⚙ Run self-optimization workflow',
      timestamp: new Date(),
    });
    sendMessage({ type: 'self_optimize' });
  }, [
    isOptimizing,
    isResearching,
    activeConversationId,
    createConversation,
    initOptimizeGraph,
    addMessageToConv,
    sendMessage,
  ]);

  // =====================
  // User query handler
  // =====================
  const handleSendMessage = useCallback((content: string) => {
    if (inputMode === 'research') {
      // Ensure there's a conversation to hold research messages
      let convId = activeConversationId;
      if (!convId) {
        const newConv = createConversation();
        convId = newConv.id;
      }
      researchConvIdRef.current = convId;
      localStorage.setItem(LS_CONV_ID, convId);

      addMessageToConv(convId, {
        id: generateId(),
        role: 'user',
        type: 'user',
        content,
        timestamp: new Date(),
      });

      // Graph: add the query node immediately so the graph tab shows activity
      addQueryNode(convId, content);

      sendMessage({ type: 'query', content, conversationId: convId, research_depth: researchDepth });
      setIsResearching(true);
    } else {
      // Chat mode — route to /chat REST endpoint
      // If research is active, open a fresh thread so research keeps its own conversation
      let targetConvId = activeConversationId;
      if (isResearching || planStatus === 'pending' || !targetConvId) {
        const newConv = createConversation();
        targetConvId = newConv.id;
      }

      addMessageToConv(targetConvId, {
        id: generateId(),
        role: 'user',
        type: 'user',
        content,
        timestamp: new Date(),
      });

      const responseMsgId = generateId();
      addMessageToConv(targetConvId, {
        id: responseMsgId,
        role: 'assistant',
        type: 'chat_response',
        content: '',
        timestamp: new Date(),
      });

      let accumulated = '';
      apiClient.streamChat(
        content,
        (chunk) => {
          accumulated += chunk;
          patchMessageInConv(targetConvId!, responseMsgId, { content: accumulated });
        },
        () => { /* stream done */ },
        (err) => {
          patchMessageInConv(targetConvId!, responseMsgId, { content: `Error: ${err}` });
        }
      );
    }
  }, [inputMode, activeConversationId, createConversation, addMessageToConv, patchMessageInConv, sendMessage, isResearching, planStatus, setIsResearching, researchDepth]);

  // =====================
  // PLAN ACTION HANDLERS
  // These are passed down through ChatContainer -> ChatMessage -> PlanMessage
  // =====================

  const handleApprovePlan = useCallback(
    (planId: string) => {
      // Update the plan message in-place to show approved state,
      // so the buttons disappear and a green badge shows instead [3]
      const planMsg = findPlanMessage();
      if (planMsg) {
        updateMessage(planMsg.id, { planAction: 'approved' });
      }

      // Notify the backend to proceed with research execution 
      sendMessage({
        type: 'approve_plan',
        planId,
        conversationId: activeConversationId,
      });

      // Graph: mark plan node as approved
      updatePlanAction(researchConvIdRef.current!, 'approved');
      // Timer: record when execution started
      setResearchStartTime(researchConvIdRef.current!, new Date().toISOString());

      setPlanStatus('approved');
      setIsResearching(true);

      addMessage({
        id: generateId(),
        role: 'system',
        type: 'system',
        content: '✅ Research plan approved. Starting research...',
        timestamp: new Date(),
      });
    },
    [findPlanMessage, updateMessage, sendMessage, activeConversationId, setPlanStatus, setIsResearching, addMessage, setResearchStartTime]
  );

  const handleModifyPlan = useCallback(
    (planId: string, feedback: string) => {
      // Mark the current plan as modified in the chat history
      const planMsg = findPlanMessage();
      if (planMsg) {
        updateMessage(planMsg.id, { planAction: 'modified' });
      }

      // Send modification feedback to backend so it can regenerate
      // the plan incorporating the user's requested changes
      sendMessage({
        type: 'modify_plan',
        planId,
        feedback,
        conversationId: activeConversationId,
      });

      // Graph: mark current plan node as being modified (amber / running)
      updatePlanAction(researchConvIdRef.current!, 'modified');

      setPlanStatus('pending');
      setIsResearching(true);
      setCurrentStatus('Regenerating Plan');

      addMessage({
        id: generateId(),
        role: 'system',
        type: 'system',
        content: `✏️ Plan modification requested: "${feedback}". Generating updated plan...`,
        timestamp: new Date(),
      });
    },
    [findPlanMessage, updateMessage, sendMessage, activeConversationId, setPlanStatus, setIsResearching, addMessage]
  );

  const handleDenyPlan = useCallback(
    (planId: string) => {
      const planMsg = findPlanMessage();
      if (planMsg) {
        updateMessage(planMsg.id, { planAction: 'denied' });
      }

      sendMessage({
        type: 'deny_plan',
        planId,
        conversationId: activeConversationId,
      });

      // Graph: mark plan node as denied
      updatePlanAction(researchConvIdRef.current!, 'denied');

      setPlanStatus('denied');
      setIsResearching(false);
      setCurrentStatus('Plan Denied');
    },
    [findPlanMessage, updateMessage, sendMessage, activeConversationId, setPlanStatus, setIsResearching, updatePlanAction]
  );

  const handleRegeneratePlan = useCallback(
    (planId: string) => {
      // Mark old plan as denied in chat history
      const planMsg = findPlanMessage();
      if (planMsg) {
        updateMessage(planMsg.id, { planAction: 'denied' });
      }

      // Deny the current plan on the backend (backend has no 'regenerate_plan' type)
      sendMessage({
        type: 'deny_plan',
        planId,
        conversationId: activeConversationId,
      });

      // Re-submit the original user query so the backend generates a new plan
      const originalQuery = [...messages].reverse().find(
        (m) => m.type === 'user' && m.role === 'user'
      )?.content;
      if (originalQuery) {
        sendMessage({
          type: 'query',
          content: originalQuery,
          conversationId: activeConversationId,
        });
      }

      setPlanStatus('pending');
      setIsResearching(true);
      setCurrentStatus('Regenerating Plan');

      addMessage({
        id: generateId(),
        role: 'system',
        type: 'system',
        content: '🔄 Plan denied. Generating a new research plan...',
        timestamp: new Date(),
      });
    },
    [findPlanMessage, updateMessage, sendMessage, activeConversationId, setPlanStatus, setIsResearching, addMessage, messages]
  );

  const handleEndResearch = useCallback(
    (planId: string) => {
      // Mark the plan as ended — user chose not to continue
      const planMsg = findPlanMessage();
      if (planMsg) {
        updateMessage(planMsg.id, { planAction: 'ended' });
      }

      // Notify backend that the research inquiry is over [7]
      sendMessage({
        type: 'deny_plan',
        planId,
        conversationId: activeConversationId,
      });

      setPlanStatus('denied');
      setIsResearching(false);
      setCurrentStatus('');

      addMessage({
        id: generateId(),
        role: 'system',
        type: 'system',
        content: '🛑 Research inquiry ended. Submit a new query to start again.',
        timestamp: new Date(),
      });
    },
    [findPlanMessage, updateMessage, sendMessage, activeConversationId, setPlanStatus, setIsResearching, addMessage]
  );

  return (
    <>
      <div className="flex h-screen [height:100dvh] overflow-hidden bg-gray-100 dark:bg-gray-950 text-gray-900 dark:text-gray-100">
        {/* Sidebar */}
        {sidebarOpen && (
          <Sidebar
            conversations={conversations}
            activeConversationId={activeConversationId}
            onSelectConversation={selectConversation}
            onNewChat={startNewChat}
            onRenameConversation={renameConversation}
            onDeleteConversation={deleteConversation}
            researchDepth={researchDepth}
            onResearchDepthChange={setResearchDepth}
            isResearching={isResearching}
            onMobileClose={() => setSidebarOpen(false)}
          />
        )}

        {/* Main Content Area */}
        <div className="flex flex-col flex-1 min-w-0">
          {/* Top Header Bar */}
          <header className="flex items-center justify-between px-4 py-2 bg-white dark:bg-gray-900 border-b border-gray-200 dark:border-gray-700 shadow-sm">
            <div className="flex items-center gap-3">
              <button
                onClick={() => setSidebarOpen((prev) => !prev)}
                className="p-1.5 rounded-md hover:bg-gray-200 dark:hover:bg-gray-700 transition-colors"
                title={sidebarOpen ? 'Close sidebar' : 'Open sidebar'}
              >
                <svg className="w-5 h-5 text-gray-600 dark:text-gray-300" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 12h16M4 18h16" />
                </svg>
              </button>
              <h1 className="text-lg font-semibold text-gray-900 dark:text-white truncate min-w-0">
                Deep Research Agent
              </h1>
            </div>
            <div className="flex items-center gap-2 sm:gap-4">
              <button
                onClick={toggleTheme}
                className="p-1.5 rounded-md hover:bg-gray-200 dark:hover:bg-gray-700 transition-colors"
                title={`Switch to ${theme === 'dark' ? 'light' : 'dark'} mode`}
              >
                {theme === 'dark' ? (
                  <svg className="w-5 h-5 text-yellow-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                      d="M12 3v1m0 16v1m9-9h-1M4 12H3m15.364 6.364l-.707-.707M6.343 6.343l-.707-.707m12.728 0l-.707.707M6.343 17.657l-.707.707M16 12a4 4 0 11-8 0 4 4 0 018 0z" />
                  </svg>
                ) : (
                  <svg className="w-5 h-5 text-gray-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                      d="M20.354 15.354A9 9 0 018.646 3.646 9.003 9.003 0 0012 21a9.003 9.003 0 008.354-5.646z" />
                  </svg>
                )}
              </button>
              {/* Docs button - hidden on mobile */}
              <button
                onClick={() => setShowDocs(true)}
                className="hidden sm:block px-3 py-1 rounded-md text-xs font-medium text-gray-600 dark:text-gray-300 hover:bg-gray-200 dark:hover:bg-gray-700 transition-colors"
                title="View documentation"
              >
                Docs
              </button>
              {/* Chat / Graph view toggle - compact on mobile */}
              <div className="flex items-center bg-gray-100 dark:bg-gray-800 rounded-lg p-0.5 gap-0.5">
                <button
                  onClick={() => setActiveView('chat')}
                  className={`px-2 sm:px-3 py-1 rounded-md text-xs font-medium transition-all whitespace-nowrap ${activeView === 'chat'
                    ? 'bg-white dark:bg-gray-700 text-gray-900 dark:text-white shadow-sm'
                    : 'text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200'
                    }`}
                  title="Switch to Chat view"
                >
                  <span className="hidden sm:inline">Chat</span>
                  <span className="sm:hidden">💬</span>
                </button>
                <button
                  onClick={() => setActiveView('graph')}
                  className={`px-2 sm:px-3 py-1 rounded-md text-xs font-medium transition-all whitespace-nowrap ${activeView === 'graph'
                    ? 'bg-white dark:bg-gray-700 text-gray-900 dark:text-white shadow-sm'
                    : 'text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200'
                    }`}
                  title="Switch to Execution Graph view"
                >
                  <span className="hidden sm:inline">Graph</span>
                  <span className="sm:hidden">📊</span>
                </button>
              </div>
              <div className="flex items-center gap-1 sm:gap-2">
                <div
                  className={`w-2 h-2 rounded-full ${isConnected
                    ? 'bg-green-500'
                    : isReconnecting
                      ? 'bg-yellow-500 animate-pulse'
                      : 'bg-red-500'
                    }`}
                />
                <span className="hidden sm:inline text-xs text-gray-500 dark:text-gray-400">
                  {isConnected
                    ? 'Connected'
                    : isReconnecting
                      ? 'Reconnecting...'
                      : 'Disconnected'}
                </span>
                <button
                  onClick={reconnect}
                  disabled={isReconnecting || isConnected}
                  className="p-1 rounded-md hover:bg-gray-200 dark:hover:bg-gray-700
                           transition-colors duration-200 disabled:opacity-30 disabled:cursor-not-allowed"
                  title="Refresh connection"
                >
                  <svg
                    className={`w-4 h-4 text-gray-500 dark:text-gray-400 ${isReconnecting ? 'animate-spin' : ''}`}
                    fill="none"
                    stroke="currentColor"
                    viewBox="0 0 24 24"
                  >
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      strokeWidth={2}
                      d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"
                    />
                  </svg>
                </button>
              </div>
            </div>
          </header>

          {/* Research Control Bar (Pause / Resume / Stop) */}
          {isResearching && (
            <ResearchControlBar
              researchStatus={researchStatus}
              onPause={pauseResearch}
              onResume={resumeResearch}
              onStop={stopResearch}
            />
          )}

          {/* Chat Container or Execution Graph */}
          {activeView === 'chat' ? (
            <ChatContainer
              messages={messages}
              isLoading={isResearching}
              currentStatus={currentStatus}
              onSendMessage={handleSendMessage}
              onApprovePlan={handleApprovePlan}
              onModifyPlan={handleModifyPlan}
              onDenyPlan={handleDenyPlan}
              onRegeneratePlan={handleRegeneratePlan}
              onEndResearch={handleEndResearch}
            />
          ) : (
            <GraphView graphState={getGraph(activeConversationId || '')} />
          )}

          {/* Chat Input */}
          <div className="border-t border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 p-4">
            <ChatInput
              onSendMessage={handleSendMessage}
              disabled={inputMode === 'research'
                ? (isResearching || !isConnected || planStatus === 'pending')
                : !isConnected}
              inputMode={inputMode}
              onToggleMode={() => setInputMode((m) => m === 'research' ? 'chat' : 'research')}
              onSelfOptimize={handleSelfOptimize}
              isOptimizing={isOptimizing}
            />
            {!isConnected && !isReconnecting && (
              <p className="text-xs text-red-500 mt-2 text-center">
                Not connected to server. Please click the refresh button to reconnect.
              </p>
            )}
            {inputMode === 'research' && isResearching && (
              <p className="text-xs text-gray-400 dark:text-gray-500 mt-2 text-center">
                Research in progress — switch to Chat mode to send a message while research runs in the background.
              </p>
            )}
            {inputMode === 'research' && planStatus === 'pending' && (
              <p className="text-xs text-yellow-500 mt-2 text-center">
                Awaiting your approval for the research plan. Please review the plan above.
              </p>
            )}
            {inputMode === 'chat' && (isResearching || planStatus === 'pending') && (
              <p className="text-xs text-purple-500 dark:text-purple-400 mt-2 text-center">
                Research is running in the background. Your message will open a new chat thread.
              </p>
            )}
          </div>
        </div>
      </div>

      {/* Docs viewer overlay */}
      {showDocs && <DocsViewer onClose={() => setShowDocs(false)} />}
    </>
  );
};

const App: React.FC = () => {
  return (
    <ErrorBoundary>
      <AppProvider>
        <AppContent />
      </AppProvider>
    </ErrorBoundary>
  );
};

export default App;