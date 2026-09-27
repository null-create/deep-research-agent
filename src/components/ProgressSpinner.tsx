import React, { useEffect, useState } from 'react';

interface ProgressSpinnerProps {
  status: string;
}

const STATUS_CONFIG: Record<string, { icon: string; color: string }> = {
  Researching: {
    icon: '🔍',
    color: 'text-blue-500',
  },
  'Checking Internal Knowledge': {
    icon: '🧠',
    color: 'text-purple-500',
  },
  Planning: {
    icon: '🗂️',
    color: 'text-yellow-500',
  },
  'Plan Denied': {
    icon: '🚫',
    color: 'text-red-500',
  },
  Status: {
    icon: '📈',
    color: 'text-gray-500',
  },
  'Starting Step': {
    icon: '🚀',
    color: 'text-pink-500',
  },
  'Step Failed': {
    icon: '❌',
    color: 'text-red-500',
  },
  'Step Completed': {
    icon: '✅',
    color: 'text-green-500',
  },
  Synthesizing: {
    icon: '🔗',
    color: 'text-green-500',
  },
  Analyzing: {
    icon: '📊',
    color: 'text-orange-500',
  },
  'Verifying Sources': {
    icon: '🕵🏽',
    color: 'text-gray-500',
  },
  'Generating Report': {
    icon: '📝',
    color: 'text-indigo-500',
  },
};

export const ProgressSpinner: React.FC<ProgressSpinnerProps> = ({ status }) => {
  const [dots, setDots] = useState('');

  useEffect(() => {
    const interval = setInterval(() => {
      setDots((prev) => (prev.length >= 3 ? '' : prev + '.'));
    }, 500);
    return () => clearInterval(interval);
  }, []);

  const config = STATUS_CONFIG[status] || STATUS_CONFIG['Researching'];

  return (
    <div
      role="status"
      aria-live="polite"
      aria-label={status}
      className="flex items-center gap-3 p-4 mx-4 my-2 bg-gray-50 dark:bg-gray-800/50 rounded-lg border border-gray-200 dark:border-gray-700"
    >
      {/* Animated spinner ring */}
      <div className="relative" aria-hidden="true">
        <div className={`w-10 h-10 rounded-full border-4 border-gray-200 dark:border-gray-600 border-t-blue-500 animate-spin`} />
        <span className="absolute inset-0 flex items-center justify-center text-lg">
          {config.icon}
        </span>
      </div>

      <div className="flex flex-col">
        <span className={`text-sm font-semibold ${config.color}`}>
          {status}{dots}
        </span>
        <span className="text-xs text-gray-400 dark:text-gray-500">
          This may take a moment
        </span>
      </div>
    </div>
  );
};