import React, { useState } from 'react';
import { Send, FlaskConical, MessageCircle, Sparkles } from 'lucide-react';

interface ChatInputProps {
  onSendMessage: (message: string) => void;
  disabled?: boolean;
  inputMode: 'research' | 'chat';
  onToggleMode: () => void;
  onSelfOptimize?: () => void;
  isOptimizing?: boolean;
}

export const ChatInput: React.FC<ChatInputProps> = ({
  onSendMessage,
  disabled,
  inputMode,
  onToggleMode,
  onSelfOptimize,
  isOptimizing = false,
}) => {
  const [input, setInput] = useState('');

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (input.trim() && !disabled) {
      onSendMessage(input.trim());
      setInput('');
    }
  };

  return (
    <form onSubmit={handleSubmit} className="border-t border-gray-200 dark:border-gray-700 p-4">
      <div className="flex flex-col sm:flex-row gap-2">
        {/* Mode toggle */}
        <button
          type="button"
          onClick={onToggleMode}
          title={inputMode === 'research' ? 'Switch to Chat mode' : 'Switch to Research mode'}
          className={`flex items-center gap-1.5 px-3 py-2 rounded-lg text-sm font-medium transition-colors whitespace-nowrap shrink-0 w-full sm:w-auto justify-center sm:justify-start
            ${inputMode === 'research'
              ? 'bg-blue-100 dark:bg-blue-900/40 text-blue-700 dark:text-blue-300 hover:bg-blue-200 dark:hover:bg-blue-900/60'
              : 'bg-purple-100 dark:bg-purple-900/40 text-purple-700 dark:text-purple-300 hover:bg-purple-200 dark:hover:bg-purple-900/60'
            }`}
        >
          {inputMode === 'research' ? (
            <>
              <FlaskConical className="w-4 h-4" />
              <span>Research</span>
            </>
          ) : (
            <>
              <MessageCircle className="w-4 h-4" />
              <span>Chat</span>
            </>
          )}
        </button>
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder={inputMode === 'research' ? 'Ask me to research something...' : 'Chat with the agent...'}
          disabled={disabled}
          className="flex-1 px-4 py-3 rounded-lg border border-gray-300 dark:border-gray-600 
                   bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100
                   focus:outline-none focus:ring-2 focus:ring-blue-500 dark:focus:ring-emerald-500
                   disabled:opacity-50 disabled:cursor-not-allowed min-w-0"
        />
        {/* Self-optimize button */}
        {onSelfOptimize && (
          <button
            type="button"
            onClick={onSelfOptimize}
            disabled={disabled || isOptimizing}
            title="Run self-optimization: analyse past sessions and update research methods"
            className={`flex items-center gap-1.5 px-3 py-2 rounded-lg text-sm font-medium transition-colors whitespace-nowrap shrink-0 w-full sm:w-auto justify-center sm:justify-start
              ${isOptimizing
                ? 'bg-teal-100 dark:bg-teal-900/40 text-teal-500 dark:text-teal-400 cursor-not-allowed opacity-70'
                : 'bg-teal-100 dark:bg-teal-900/40 text-teal-700 dark:text-teal-300 hover:bg-teal-200 dark:hover:bg-teal-900/60'
              }
              disabled:opacity-50 disabled:cursor-not-allowed`}
          >
            <Sparkles className={`w-4 h-4 ${isOptimizing ? 'animate-pulse' : ''}`} />
            <span>{isOptimizing ? 'Optimizing…' : 'Self-Optimize'}</span>
          </button>
        )}
        <button
          type="submit"
          disabled={disabled || !input.trim()}
          className="px-6 py-3 bg-blue-500 dark:bg-emerald-600 text-white rounded-lg
                   hover:bg-blue-600 dark:hover:bg-emerald-700 disabled:opacity-50 
                   disabled:cursor-not-allowed transition-colors flex items-center gap-2 w-full sm:w-auto justify-center"
        >
          <Send className="w-5 h-5" />
          Send
        </button>
      </div>
    </form>
  );
};
