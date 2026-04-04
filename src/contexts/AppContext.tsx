import React, { createContext, useContext, useState, useEffect, useCallback } from 'react';
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
  } = useConversations();

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
      setIsResearching(false);
      setPendingPlan(null);
      setPlanStatus('none');
    },
    [setActiveConversationId]
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
    createConversation();
    setIsResearching(false);
    setPendingPlan(null);
    setPlanStatus('none');
  }, [createConversation]);

  useEffect(() => {
    const savedResearching = sessionStorage.getItem('deep_research_is_researching');
    if (savedResearching === 'true') {
      setIsResearching(true);
    }
  }, []);

  useEffect(() => {
    sessionStorage.setItem('deep_research_is_researching', String(isResearching));
  }, [isResearching]);

  return (
    <AppContext.Provider
      value={{
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
      }}
    >
      {children}
    </AppContext.Provider>
  );
};

export const useApp = () => {
  const ctx = useContext(AppContext);
  if (!ctx) throw new Error('useApp must be used within AppProvider');
  return ctx;
};