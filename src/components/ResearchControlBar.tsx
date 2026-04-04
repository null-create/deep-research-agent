import React, { useState } from 'react';
import { Pause, Play, Square, Loader } from 'lucide-react';
import { ResearchStatus } from '../hooks/useResearchControl';

interface ResearchControlBarProps {
  researchStatus: ResearchStatus;
  onPause: () => void;
  onResume: () => void;
  onStop: () => void;
}

export const ResearchControlBar: React.FC<ResearchControlBarProps> = ({
  researchStatus,
  onPause,
  onResume,
  onStop,
}) => {
  const [showStopConfirm, setShowStopConfirm] = useState(false);

  if (researchStatus === 'idle' || researchStatus === 'stopped') return null;

  const isPaused = researchStatus === 'paused';
  const isRunning = researchStatus === 'running';

  const handleStopClick = () => setShowStopConfirm(true);
  const handleConfirmStop = () => {
    setShowStopConfirm(false);
    onStop();
  };
  const handleCancelStop = () => setShowStopConfirm(false);

  return (
    <div className="flex items-center gap-3 px-4 py-2 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-xl shadow-sm">
      {/* Status Indicator */}
      <div className="flex items-center gap-2 text-sm text-gray-600 dark:text-gray-300">
        {isRunning && (
          <Loader className="w-4 h-4 animate-spin text-blue-500 dark:text-emerald-400" />
        )}
        {isPaused && (
          <span className="w-2 h-2 rounded-full bg-yellow-400 animate-pulse" />
        )}
        <span className="font-medium">
          {isRunning ? 'Researching...' : 'Paused'}
        </span>
      </div>

      <div className="flex-1" />

      {/* Pause / Resume Button */}
      {isRunning ? (
        <button
          onClick={onPause}
          className="flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium text-yellow-700 bg-yellow-100 hover:bg-yellow-200 dark:text-yellow-300 dark:bg-yellow-900/30 dark:hover:bg-yellow-900/50 rounded-lg transition-colors"
        >
          <Pause className="w-4 h-4" />
          Pause
        </button>
      ) : (
        <button
          onClick={onResume}
          className="flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium text-green-700 bg-green-100 hover:bg-green-200 dark:text-green-300 dark:bg-green-900/30 dark:hover:bg-green-900/50 rounded-lg transition-colors"
        >
          <Play className="w-4 h-4" />
          Resume
        </button>
      )}

      {/* Stop Button / Confirm Dialog */}
      {showStopConfirm ? (
        <div className="flex items-center gap-2 pl-3 border-l border-gray-200 dark:border-gray-600">
          <span className="text-sm text-gray-600 dark:text-gray-300">
            Stop research?
          </span>
          <button
            onClick={handleConfirmStop}
            className="px-3 py-1.5 text-sm font-medium text-white bg-red-500 hover:bg-red-600 rounded-lg transition-colors"
          >
            Confirm
          </button>
          <button
            onClick={handleCancelStop}
            className="px-3 py-1.5 text-sm font-medium text-gray-600 dark:text-gray-300 bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600 rounded-lg transition-colors"
          >
            Cancel
          </button>
        </div>
      ) : (
        <button
          onClick={handleStopClick}
          className="flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium text-red-600 bg-red-50 hover:bg-red-100 dark:text-red-400 dark:bg-red-900/20 dark:hover:bg-red-900/40 rounded-lg transition-colors"
        >
          <Square className="w-4 h-4" />
          Stop
        </button>
      )}
    </div>
  );
};