import React, { useState } from 'react';
import { Sparkles, Check, Edit3, X, RefreshCw, StopCircle } from 'lucide-react';
import { PlanData, PlanAction } from '../types/conversation';

interface PlanMessageProps {
  plan: PlanData;
  planAction: PlanAction;
  onApprove: (planId: string) => void;
  onModify: (planId: string, feedback: string) => void;
  onDeny: (planId: string) => void;
  onRegeneratePlan: (planId: string) => void;
  onEndResearch: (planId: string) => void;
  isLoading: boolean;
}

export const PlanMessage: React.FC<PlanMessageProps> = ({
  plan,
  planAction,
  onApprove,
  onModify,
  onDeny,
  onRegeneratePlan,
  onEndResearch,
  isLoading,
}) => {
  const [showModifyInput, setShowModifyInput] = useState(false);
  const [modifyFeedback, setModifyFeedback] = useState('');
  const [showDenyConfirm, setShowDenyConfirm] = useState(false);

  const isPending = planAction === 'pending';
  const isApproved = planAction === 'approved';
  const isModified = planAction === 'modified';
  const isDenied = planAction === 'denied';
  const isEnded = planAction === 'ended';

  const handleModifySubmit = () => {
    if (modifyFeedback.trim()) {
      onModify(plan.id, modifyFeedback.trim());
      setModifyFeedback('');
      setShowModifyInput(false);
    }
  };

  return (
    <div className="flex justify-start mb-4">
      <div className="max-w-[80%] rounded-lg p-4 bg-white dark:bg-gray-800 border border-blue-200 dark:border-blue-700 shadow-md">
        {/* Plan Header */}
        <div className="flex items-center gap-2 mb-3">
          <Sparkles className="w-5 h-5 text-purple-500 dark:text-yellow-400" />
          <span className="font-semibold text-lg text-gray-900 dark:text-white">
            Research Plan
          </span>
          {isApproved && (
            <span className="ml-2 text-xs bg-green-100 dark:bg-green-900 text-green-700 dark:text-green-300 px-2 py-0.5 rounded-full">
              ✅ Approved
            </span>
          )}
          {isModified && (
            <span className="ml-2 text-xs bg-yellow-100 dark:bg-yellow-900 text-yellow-700 dark:text-yellow-300 px-2 py-0.5 rounded-full">
              ✏️ Modification Sent
            </span>
          )}
          {isDenied && (
            <span className="ml-2 text-xs bg-red-100 dark:bg-red-900 text-red-700 dark:text-red-300 px-2 py-0.5 rounded-full">
              ❌ Denied
            </span>
          )}
          {isEnded && (
            <span className="ml-2 text-xs bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-400 px-2 py-0.5 rounded-full">
              🛑 Research Ended
            </span>
          )}
        </div>

        {/* Plan Summary */}
        <p className="text-gray-700 dark:text-gray-300 mb-3">Summary: {plan.goal}</p>

        {/* Steps */}
        <div className="mb-4">
          <h4 className="text-sm font-medium text-gray-600 dark:text-gray-400 mb-2">Steps:</h4>
          <ol className="list-decimal list-inside space-y-1.5">
            {plan.steps.map((step, index) => (
              <li key={index} className="text-gray-700 dark:text-gray-300 text-sm pl-2">
                {step.description}
              </li>
            ))}
          </ol>
        </div>

        {/* Action Buttons - Only show when pending */}
        {isPending && (
          <>
            {/* Modify Textarea */}
            {showModifyInput && (
              <div className="mb-4">
                <textarea
                  className="w-full p-3 border border-gray-300 dark:border-gray-600 rounded-lg
                    bg-white dark:bg-gray-700 text-gray-900 dark:text-white
                    placeholder-gray-400 focus:ring-2 focus:ring-blue-500 focus:border-transparent
                    text-sm"
                  placeholder="Describe what you'd like changed about this plan..."
                  rows={3}
                  value={modifyFeedback}
                  onChange={(e) => setModifyFeedback(e.target.value)}
                />
                <div className="flex gap-2 mt-2">
                  <button
                    onClick={handleModifySubmit}
                    disabled={isLoading || !modifyFeedback.trim()}
                    className="px-3 py-1.5 bg-yellow-500 hover:bg-yellow-600 disabled:bg-yellow-300
                      text-white text-sm font-medium rounded-lg transition-colors"
                  >
                    Submit Modification
                  </button>
                  <button
                    onClick={() => { setShowModifyInput(false); setModifyFeedback(''); }}
                    className="px-3 py-1.5 bg-gray-300 dark:bg-gray-600 hover:bg-gray-400
                      dark:hover:bg-gray-500 text-gray-800 dark:text-white text-sm font-medium
                      rounded-lg transition-colors"
                  >
                    Cancel
                  </button>
                </div>
              </div>
            )}

            {/* Deny Confirmation */}
            {showDenyConfirm && (
              <div className="mb-4 p-3 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg">
                <p className="text-sm text-gray-700 dark:text-gray-300 mb-3">
                  Would you like the agent to try generating a different plan, or end this research inquiry?
                </p>
                <div className="flex gap-2">
                  <button
                    onClick={() => {
                      onRegeneratePlan(plan.id);
                      setShowDenyConfirm(false);
                    }}
                    disabled={isLoading}
                    className="flex-1 px-3 py-2 bg-blue-500 hover:bg-blue-600 disabled:bg-blue-300
                      text-white text-sm font-medium rounded-lg transition-colors flex items-center justify-center gap-1.5"
                  >
                    <RefreshCw className="w-4 h-4" />
                    Generate New Plan
                  </button>
                  <button
                    onClick={() => {
                      onEndResearch(plan.id);
                      setShowDenyConfirm(false);
                    }}
                    disabled={isLoading}
                    className="flex-1 px-3 py-2 bg-gray-500 hover:bg-gray-600 disabled:bg-gray-300
                      text-white text-sm font-medium rounded-lg transition-colors flex items-center justify-center gap-1.5"
                  >
                    <StopCircle className="w-4 h-4" />
                    End Research
                  </button>
                  <button
                    onClick={() => setShowDenyConfirm(false)}
                    className="px-3 py-2 bg-gray-200 dark:bg-gray-700 hover:bg-gray-300
                      dark:hover:bg-gray-600 text-gray-700 dark:text-gray-300 text-sm
                      rounded-lg transition-colors"
                  >
                    Back
                  </button>
                </div>
              </div>
            )}

            {/* Main Action Buttons */}
            {!showModifyInput && !showDenyConfirm && (
              <div className="flex gap-2">
                <button
                  onClick={() => onApprove(plan.id)}
                  disabled={isLoading}
                  className="flex-1 px-4 py-2 bg-green-600 hover:bg-green-700 disabled:bg-green-400
                    text-white font-medium rounded-lg transition-colors flex items-center justify-center gap-2 text-sm"
                >
                  <Check className="w-4 h-4" />
                  Approve
                </button>
                <button
                  onClick={() => setShowModifyInput(true)}
                  disabled={isLoading}
                  className="flex-1 px-4 py-2 bg-yellow-500 hover:bg-yellow-600 disabled:bg-yellow-300
                    text-white font-medium rounded-lg transition-colors flex items-center justify-center gap-2 text-sm"
                >
                  <Edit3 className="w-4 h-4" />
                  Modify
                </button>
                <button
                  onClick={() => setShowDenyConfirm(true)}
                  disabled={isLoading}
                  className="flex-1 px-4 py-2 bg-red-600 hover:bg-red-700 disabled:bg-red-400
                    text-white font-medium rounded-lg transition-colors flex items-center justify-center gap-2 text-sm"
                >
                  <X className="w-4 h-4" />
                  Deny
                </button>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
};