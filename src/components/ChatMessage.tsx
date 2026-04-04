import React, { useEffect } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Message } from '../types/conversation';
import { CheckCircle, Loader, XCircle, MessageCircle } from 'lucide-react';
import { PlanMessage } from './PlanMessage';
import { StepResultViewer } from './StepResultViewer';
import { SynthesisMessage } from './SynthesisMessage';
import { useTypingEffect } from '../hooks/useTypingEffect';

// Isolated component so the hook is always called unconditionally
const TypingText: React.FC<{ text: string; messageId: string }> = ({ text, messageId }) => {
  const storageKey = `typed:${messageId}`;
  const alreadyTyped = !!localStorage.getItem(storageKey);
  const displayed = useTypingEffect(text, alreadyTyped);

  // Mark this message as fully typed once we've caught up to the full text
  useEffect(() => {
    if (displayed.length > 0 && displayed === text) {
      localStorage.setItem(storageKey, '1');
    }
  }, [displayed, text, storageKey]);

  return (
    <div className="prose prose-sm dark:prose-invert max-w-none">
      {displayed ? (
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{displayed}</ReactMarkdown>
      ) : (
        <span className="flex items-center gap-2 text-gray-400 dark:text-gray-500">
          <Loader className="w-4 h-4 animate-spin" />
          Thinking...
        </span>
      )}
    </div>
  );
};

interface ChatMessageProps {
  message: Message;
  onApprovePlan?: (planId: string) => void;
  onModifyPlan?: (planId: string, feedback: string) => void;
  onDenyPlan?: (planId: string) => void;
  onRegeneratePlan?: (planId: string) => void;
  onEndResearch?: (planId: string) => void;
  isLoading?: boolean;
}

export const ChatMessage: React.FC<ChatMessageProps> = ({
  message,
  onApprovePlan,
  onModifyPlan,
  onDenyPlan,
  onRegeneratePlan,
  onEndResearch,
  isLoading = false,
}) => {
  const isUser = message.role === 'user';

  const renderContent = () => {
    // ── Research plan ───────────────────────────────────────────────────────
    if (message.type === 'plan_approval' && message.data?.plan) {
      return (
        <PlanMessage
          plan={message.data.plan}
          planAction={message.data.planAction || 'pending'}
          onApprove={onApprovePlan || (() => { })}
          onModify={onModifyPlan || (() => { })}
          onDeny={onDenyPlan || (() => { })}
          onRegeneratePlan={onRegeneratePlan || (() => { })}
          onEndResearch={onEndResearch || (() => { })}
          isLoading={isLoading}
        />
      );
    }

    // ── Step in progress ────────────────────────────────────────────────────
    if (message.type === 'step_start' && message.data?.step) {
      return (
        <div className="flex items-center gap-2">
          <Loader className="w-5 h-5 animate-spin text-blue-500 dark:text-emerald-500 shrink-0" />
          <span className="font-semibold">
            Step {message.data.step.name || 'Unknown'}:{' '}
            {message.data.step.description || 'No description'}
          </span>
        </div>
      );
    }

    // ── Step complete ────────────────────────────────────────────────────────
    if (message.type === 'step_complete' && message.data?.step) {
      const { name, description, result, error } = message.data.step;
      return (
        <div className="space-y-1">
          <div className="flex items-center gap-2">
            <CheckCircle className="w-5 h-5 text-green-500 shrink-0" />
            <span className="font-semibold">
              Step {name || 'Unknown'} Complete
            </span>
          </div>
          {description && (
            <p className="text-sm text-gray-600 dark:text-gray-400 pl-7">
              {description}
            </p>
          )}
          {/* ── "View results" collapsible ── */}
          <div className="pl-7">
            <StepResultViewer
              stepName={name || 'Unknown Step'}
              result={result}
              error={error}
            />
          </div>
        </div>
      );
    }

    // ── Step failed ──────────────────────────────────────────────────────────
    if (message.type === 'step_failed' && message.data?.step) {
      const { name, description, error } = message.data.step;
      return (
        <div className="space-y-2">
          <div className="flex items-center gap-2">
            <XCircle className="w-5 h-5 text-red-500 shrink-0" />
            <span className="font-semibold text-red-600 dark:text-red-400">
              Step {name || 'Unknown'} Failed
            </span>
          </div>
          {description && (
            <p className="text-sm text-gray-600 dark:text-gray-400 pl-7">
              {description}
            </p>
          )}
          <div className="pl-7">
            <StepResultViewer
              stepName={name || 'Unknown Step'}
              error={error}
            />
          </div>
        </div>
      );
    }

    // ── Step update ─────────────────────────────────────────────────────────
    if (message.type === 'step_update' && message.data?.step) {
      const { name, description, result, error } = message.data.step;
      return (
        <div className="space-y-1">
          <div className="flex items-center gap-2">
            <Loader className="w-5 h-5 animate-spin text-blue-500 dark:text-emerald-500 shrink-0" />
            <span className="font-semibold">
              Step {name || 'Unknown'} Update
            </span>
          </div>
          {description && (
            <p className="text-sm text-gray-600 dark:text-gray-400 pl-7">
              {description}
            </p>
          )}
        </div>
      );
    }

    // ── Synthesis / final report ─────────────────────────────────────────────
    if (
      (message.type === 'synthesis' || message.type === 'research_complete' || message.type === 'report') &&
      message.data?.synthesis
    ) {
      return <SynthesisMessage synthesis={message.data.synthesis} />;
    }

    // ── Chat response (streaming from /chat endpoint) ─────────────────────────
    if (message.type === 'chat_response') {
      return <TypingText text={message.content} messageId={message.id} />;
    }

    // ── Default plain text ───────────────────────────────────────────────────
    return (
      <div className="prose prose-sm dark:prose-invert max-w-none whitespace-pre-wrap">
        {message.content}
      </div>
    );
  };

  return (
    <div className={`flex ${isUser ? 'justify-end' : 'justify-start'} mb-4`}>
      <div
        className={`max-w-[80%] rounded-lg p-4 ${isUser
          ? 'bg-blue-500 dark:bg-emerald-600 text-white'
          : 'bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 border border-gray-200 dark:border-gray-700'
          }`}
      >
        {renderContent()}
      </div>
    </div>
  );
};