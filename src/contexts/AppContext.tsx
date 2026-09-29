import React, { createContext, useContext, useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { Conversation, Message } from '../types/conversation';
import { useConversations } from '../hooks/useConversations';

interface AppContextType {
  // Chat state
  messages: Message[];
  addMessage: (msg: Message) => void;
  updateMessage: (messageId: string, dataUpdate: Record<string, any>) => void;
  // Targeted conversation helpers (for background research routing)
  createConversation: (title?: string) => Conversation;
  addMessageToConv: (convId: string, msg: Message) => void;
  updateMessageInConv: (convId: string, msgId: string, update: Record<string, any>) => void;
  patchMessageInConv: (convId: string, msgId: string, patch: Partial<Message>) => void;
  // File management
  files: File[];
  setFiles: React.Dispatch<React.SetStateAction<File[]>>;
  // Research state
  isResearching: boolean;
  setIsResearching: React.Dispatch<React.SetStateAction<boolean>>;
  // Conversation management
  conversations: Conversation[];
  activeConversationId: string | null;
  selectConversation: (id: string) => void;
  startNewChat: () => void;
  renameConversation: (id: string, title: string) => void;
  deleteConversation: (id: string) => void;
  // Plan state
  pendingPlan: any | null;
  setPendingPlan: React.Dispatch<React.SetStateAction<any | null>>;
  planStatus: string;
  setPlanStatus: React.Dispatch<React.SetStateAction<string>>;
  // Event index tracking (for accurate replay after browser close)
  setEventIndex: (convId: string, index: number) => void;
  getEventIndex: (convId: string) => number;
  // Per-conversation session tracking (P0-1 fix — replaces singleton
  // ``deep_research_session_id`` localStorage key so multiple conversations
  // can hold independent backend sessions).
  setSessionId: (convId: string, sessionId: string | null) => void;
  setSessionState: (convId: string, state: Conversation['sessionState']) => void;
  findConvBySessionId: (sessionId: string) => string | null;
  // A callback registered by the WebSocket layer that switches the backend's
  // active drain to a given session id.  Called by ``selectConversation`` so
  // switching conversations transparently resumes the correct session.
  onActivateSession: React.MutableRefObject<((sessionId: string) => void) | null>;
}

const AppContext = createContext<AppContextType | undefined>(undefined);

export const AppProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const {
    conversations,
    activeConversationId,
    setActiveConversationId,
    createConversation,
    deleteConversation: deleteConv,
    renameConversation: renameConv,
    addMessageToConversation,
    updateMessageInConversation,
    patchMessageInConversation,
    getActiveConversation,
    setEventIndex,
    getEventIndex,
    setSessionId,
    setSessionState,
    findConvBySessionId,
  } = useConversations();

  // ── Session activation bridge (P0-1) ──────────────────────────────────
  // The WebSocket layer registers a handler here that sends a ``resume``
  // message for a given session id.  ``selectConversation`` invokes it
  // when the newly-active conversation has a live session so the backend
  // switches its drain to the right pipeline.
  const onActivateSession = useRef<((sessionId: string) => void) | null>(null);

  const addMessageToConv = useCallback(
    (convId: string, msg: Message) => addMessageToConversation(convId, msg),
    [addMessageToConversation]
  );

  const updateMessageInConv = useCallback(
    (convId: string, msgId: string, update: Record<string, any>) =>
      updateMessageInConversation(convId, msgId, update),
    [updateMessageInConversation]
  );

  const patchMessageInConv = useCallback(
    (convId: string, msgId: string, patch: Partial<Message>) =>
      patchMessageInConversation(convId, msgId, patch),
    [patchMessageInConversation]
  );

  const [files, setFiles] = useState<File[]>([]);
  const [isResearching, setIsResearching] = useState(false);
  const [pendingPlan, setPendingPlan] = useState<any | null>(null);
  const [planStatus, setPlanStatus] = useState('none');

  // Derive messages from active conversation
  const activeConversation = getActiveConversation();
  const messages = activeConversation?.messages || [];

  const addMessage = useCallback(
    (msg: Message) => {
      let convId = activeConversationId;
      if (!convId) {
        const newConv = createConversation();
        convId = newConv.id;
      }
      addMessageToConversation(convId!, msg);
    },
    [activeConversationId, createConversation, addMessageToConversation]
  );

  const updateMessage = useCallback(
    (messageId: string, dataUpdate: Record<string, any>) => {
      if (!activeConversationId) return;
      updateMessageInConversation(activeConversationId, messageId, dataUpdate);
    },
    [activeConversationId, updateMessageInConversation]
  );

  const selectConversation = useCallback(
    (id: string) => {
      setActiveConversationId(id);
      const target = conversations.find((c) => c.id === id);
      const liveStates: Array<Conversation['sessionState']> = [
        'planning',
        'awaiting_approval',
        'executing',
      ];
      // If the selected conversation still has a running backend session,
      // ask the WebSocket layer to resume it so its events start flowing
      // again.  Reset the local plan / researching flags first — the
      // ``session_resumed`` reply will re-populate them.
      if (target?.sessionId && liveStates.includes(target.sessionState)) {
        setIsResearching(false);
        setPendingPlan(null);
        setPlanStatus('none');
        onActivateSession.current?.(target.sessionId);
      } else {
        setIsResearching(false);
        setPendingPlan(null);
        setPlanStatus('none');
      }
    },
    [conversations, setActiveConversationId]
  );

  const deleteConversation = useCallback(
    (id: string) => {
      deleteConv(id);
      if (activeConversationId === id) {
        setActiveConversationId(null);
        setIsResearching(false);
        setPendingPlan(null);
        setPlanStatus('none');
      }
    },
    [activeConversationId, deleteConv, setActiveConversationId]
  );

  const startNewChat = useCallback(() => {
    // Creating a new conversation must NOT stop any running research in the
    // previous conversation — the previous session keeps its ``sessionId``
    // and can be resumed by re-selecting it.  We only reset the *local*
    // plan / researching UI flags for the new (empty) conversation.
    createConversation();
    setIsResearching(false);
    setPendingPlan(null);
    setPlanStatus('none');
  }, [createConversation]);

  useEffect(() => {
    const savedResearching = localStorage.getItem('deep_research_is_researching');
    if (savedResearching === 'true') {
      setIsResearching(true);
    }
  }, []);

  useEffect(() => {
    localStorage.setItem('deep_research_is_researching', String(isResearching));
  }, [isResearching]);

  const contextValue = useMemo<AppContextType>(() => ({
    messages,
    addMessage,
    updateMessage,
    createConversation,
    addMessageToConv,
    updateMessageInConv,
    patchMessageInConv,
    files,
    setFiles,
    isResearching,
    setIsResearching,
    conversations,
    activeConversationId,
    selectConversation,
    startNewChat,
    renameConversation: renameConv,
    deleteConversation,
    pendingPlan,
    setPendingPlan,
    planStatus,
    setPlanStatus,
    setEventIndex,
    getEventIndex,
    setSessionId,
    setSessionState,
    findConvBySessionId,
    onActivateSession,
  }), [
    messages, addMessage, updateMessage, createConversation,
    addMessageToConv, updateMessageInConv, patchMessageInConv,
    files, setFiles, isResearching, setIsResearching,
    conversations, activeConversationId, selectConversation,
    startNewChat, renameConv, deleteConversation,
    pendingPlan, setPendingPlan, planStatus, setPlanStatus,
    setEventIndex, getEventIndex,
    setSessionId, setSessionState, findConvBySessionId,
  ]);

  return (
    <AppContext.Provider value={contextValue}>
      {children}
    </AppContext.Provider>
  );
};

export const useApp = () => {
  const ctx = useContext(AppContext);
  if (!ctx) throw new Error('useApp must be used within AppProvider');
  return ctx;
};