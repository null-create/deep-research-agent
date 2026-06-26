import { useState, useCallback, useEffect } from 'react';
import { Conversation, Message } from '../types/conversation';

const STORAGE_KEY = 'deep_research_conversations';
const ACTIVE_CONVERSATION_KEY = 'deep_research_active_conversation';

export function useConversations() {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null);

  const updateMessageInConversation = useCallback(
    (conversationId: string, messageId: string, dataUpdate: Record<string, any>) => {
      setConversations((prev) =>
        prev.map((conv) => {
          if (conv.id !== conversationId) return conv;
          return {
            ...conv,
            messages: conv.messages.map((msg) => {
              if (msg.id !== messageId) return msg;
              return {
                ...msg,
                data: { ...msg.data, ...dataUpdate },
              };
            }),
            updatedAt: new Date(),
          };
        })
      );
    },
    []
  );

  // Load from localStorage on mount
  useEffect(() => {
    try {
      const stored = localStorage.getItem(STORAGE_KEY);
      if (stored) {
        const parsed = JSON.parse(stored).map((c: any) => ({
          ...c,
          createdAt: new Date(c.createdAt),
          updatedAt: new Date(c.updatedAt),
          messages: c.messages.map((m: any) => ({
            ...m,
            timestamp: new Date(m.timestamp),
          })),
        }));
        setConversations(parsed);
      }

      const activeId = localStorage.getItem(ACTIVE_CONVERSATION_KEY);
      if (activeId) {
        setActiveConversationId(activeId);
      }
    } catch (e) {
      console.error('Failed to load conversations from storage:', e);
    }
  }, []);

  // Persist to localStorage whenever conversations change
  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(conversations));
    } catch (e) {
      console.error('Failed to save conversations:', e);
    }
  }, [conversations]);

  // Persist active conversation id
  useEffect(() => {
    if (activeConversationId) {
      localStorage.setItem(ACTIVE_CONVERSATION_KEY, activeConversationId);
    } else {
      localStorage.removeItem(ACTIVE_CONVERSATION_KEY);
    }
  }, [activeConversationId]);

  const createConversation = useCallback((title?: string): Conversation => {
    const newConversation: Conversation = {
      id: Math.random().toString(36).substr(2, 9) + Date.now().toString(36),
      title: title || `Research ${new Date().toLocaleDateString()} ${new Date().toLocaleTimeString()}`,
      createdAt: new Date(),
      updatedAt: new Date(),
      messages: [],
    };
    setConversations((prev) => [newConversation, ...prev]);
    setActiveConversationId(newConversation.id);
    return newConversation;
  }, []);

  const deleteConversation = useCallback((id: string) => {
    setConversations((prev) => {
      const updated = prev.filter((c) => c.id !== id);
      // Persist immediately to localStorage
      localStorage.setItem(STORAGE_KEY, JSON.stringify(updated));
      return updated;
    });

    // If the deleted conversation is active, clear it
    setActiveConversationId((prevActive) => {
      if (prevActive === id) {
        localStorage.removeItem(ACTIVE_CONVERSATION_KEY);
        return null;
      }
      return prevActive;
    });
  }, []);

  const renameConversation = useCallback((id: string, newTitle: string) => {
    setConversations((prev) =>
      prev.map((c) =>
        c.id === id ? { ...c, title: newTitle, updatedAt: new Date() } : c
      )
    );
  }, []);

  const patchMessageInConversation = useCallback(
    (conversationId: string, messageId: string, patch: Partial<Message>) => {
      setConversations((prev) =>
        prev.map((conv) => {
          if (conv.id !== conversationId) return conv;
          return {
            ...conv,
            messages: conv.messages.map((msg) =>
              msg.id !== messageId ? msg : { ...msg, ...patch }
            ),
            updatedAt: new Date(),
          };
        })
      );
    },
    []
  );

  const addMessageToConversation = useCallback((conversationId: string, message: Message) => {
    setConversations((prev) =>
      prev.map((c) =>
        c.id === conversationId
          ? { ...c, messages: [...c.messages, message], updatedAt: new Date() }
          : c
      )
    );
  }, []);

  const getActiveConversation = useCallback((): Conversation | null => {
    return conversations.find((c) => c.id === activeConversationId) || null;
  }, [conversations, activeConversationId]);

  const setEventIndex = useCallback((conversationId: string, index: number) => {
    setConversations((prev) =>
      prev.map((c) =>
        c.id === conversationId
          ? { ...c, _eventIndex: index, updatedAt: new Date() }
          : c
      )
    );
  }, []);

  const getEventIndex = useCallback(
    (conversationId: string): number => {
      const conv = conversations.find((c) => c.id === conversationId);
      return conv?._eventIndex ?? 0;
    },
    [conversations]
  );

  return {
    conversations,
    activeConversationId,
    setActiveConversationId,
    createConversation,
    deleteConversation,
    renameConversation,
    addMessageToConversation,
    patchMessageInConversation,
    getActiveConversation,
    updateMessageInConversation,
    setEventIndex,
    getEventIndex,
  };
}