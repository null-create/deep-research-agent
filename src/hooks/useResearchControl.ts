import { useState, useCallback } from 'react';
import { ResearchControlAction } from '../types/conversation';

export type ResearchStatus = 'idle' | 'running' | 'paused' | 'stopped';

interface UseResearchControlProps {
  sendMessage: (message: any) => void;
  activeConversationId: string | null;
  setIsResearching: (value: boolean) => void;
}

export function useResearchControl({
  sendMessage,
  activeConversationId,
  setIsResearching,
}: UseResearchControlProps) {
  const [researchStatus, setResearchStatus] = useState<ResearchStatus>('idle');

  const sendControlMessage = useCallback(
    (action: ResearchControlAction) => {
      sendMessage({
        type: 'research_control',
        action,
        conversationId: activeConversationId,
      });
    },
    [sendMessage, activeConversationId]
  );

  const startResearch = useCallback(() => {
    setResearchStatus('running');
    setIsResearching(true);
  }, [setIsResearching]);

  const pauseResearch = useCallback(() => {
    sendControlMessage('pause');
    setResearchStatus('paused');
  }, [sendControlMessage]);

  const resumeResearch = useCallback(() => {
    sendControlMessage('resume');
    setResearchStatus('running');
  }, [sendControlMessage]);

  const stopResearch = useCallback(() => {
    sendControlMessage('stop');
    setResearchStatus('stopped');
    setIsResearching(false);
  }, [sendControlMessage, setIsResearching]);

  const resetResearch = useCallback(() => {
    setResearchStatus('idle');
  }, []);

  return {
    researchStatus,
    startResearch,
    pauseResearch,
    resumeResearch,
    stopResearch,
    resetResearch,
  };
}