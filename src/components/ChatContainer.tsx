import React, { useRef, useEffect } from 'react';
import { ProgressSpinner } from './ProgressSpinner';
import { Message, } from '../types/conversation';
import { ChatMessage } from './ChatMessage';

interface ChatContainerProps {
  messages: Message[];
  isLoading?: boolean;
  currentStatus?: string;
  onApprovePlan?: (planId: string) => void;
  onModifyPlan?: (planId: string, feedback: string) => void;
  onDenyPlan?: (planId: string) => void;
  onRegeneratePlan?: (planId: string) => void;
  onEndResearch?: (planId: string) => void;
}

export const ChatContainer: React.FC<ChatContainerProps> = ({
  messages,
  isLoading = false,
  currentStatus = 'Researching',
  onApprovePlan,
  onModifyPlan,
  onDenyPlan,
  onRegeneratePlan,
  onEndResearch,
}) => {
  const bottomRef = useRef<HTMLDivElement>(null);

  // Auto-scroll to bottom whenever messages change or loading state changes
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, isLoading]);

  return (
    <div className="flex-1 overflow-y-auto px-4 py-6">
      {/* Empty state */}
      {messages.length === 0 && !isLoading && (
        <div className="flex flex-col items-center justify-center h-full text-center">
          <div className="max-w-md space-y-4">
            <div className="w-16 h-16 mx-auto mb-4 rounded-full bg-blue-100 dark:bg-blue-900/30 flex items-center justify-center">
              <svg
                className="w-8 h-8 text-blue-500"
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"
                />
              </svg>
            </div>
            <h2 className="text-2xl font-bold text-gray-900 dark:text-gray-100">
              Deep Research Agent
            </h2>
            <p className="text-gray-600 dark:text-gray-400">
              Ask me to research any topic. I'll create a research plan, search multiple
              sources, cross-reference information, and provide comprehensive analysis
              with verified facts and insights.
            </p>
            <div className="grid grid-cols-2 gap-2 mt-6">
              {[
                'Research the latest AI trends',
                'Analyze market opportunities in EV',
                'Compare cloud providers for Agentic applications',
                'Summarize recent climate studies',
              ].map((suggestion, i) => (
                <button
                  key={i}
                  className="text-left text-xs p-3 rounded-lg border border-gray-200 dark:border-gray-700
                             hover:bg-gray-50 dark:hover:bg-gray-800 text-gray-600 dark:text-gray-400
                             transition-colors duration-150"
                >
                  {suggestion}
                </button>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* Messages */}
      {messages.map((message) => (
        <div key={message.id}>
          <ChatMessage
            message={message}
            onApprovePlan={onApprovePlan}
            onModifyPlan={onModifyPlan}
            onDenyPlan={onDenyPlan}
            onRegeneratePlan={onRegeneratePlan}
            onEndResearch={onEndResearch}
            isLoading={isLoading}
          />
        </div>
      ))}

      {/* Progress Spinner with dynamic status */}
      {isLoading && <ProgressSpinner status={currentStatus} />}

      {/* Scroll anchor */}
      <div ref={bottomRef} />
    </div>
  );
};