import React, { useState } from 'react';
import { BookOpen } from 'lucide-react';
import { SynthesisData } from '../types/conversation';
import { ReportViewer } from './ReportViewer';
import { ResearchReportViewer } from './ResearchReportViewer';

interface SynthesisMessageProps {
  synthesis: SynthesisData;
}

export const SynthesisMessage: React.FC<SynthesisMessageProps> = ({ synthesis }) => {
  const [showReport, setShowReport] = useState(false);

  return (
    <>
      {/* ── Prompt card ── */}
      <div className="flex flex-col gap-3 p-4 rounded-lg
        bg-gradient-to-br from-blue-50 to-purple-50
        dark:from-blue-900/20 dark:to-purple-900/20
        border border-blue-200 dark:border-blue-700"
      >
        <div className="flex items-center gap-2">
          <BookOpen className="w-5 h-5 text-purple-500 dark:text-yellow-400 shrink-0" />
          <span className="font-semibold text-gray-900 dark:text-white">
            Research Complete
          </span>
        </div>

        <p className="text-sm text-gray-600 dark:text-gray-300">
          Your research report <strong>"{synthesis.title ?? 'Research Report'}"</strong> is ready.
        </p>

        {/* ── View Report button ── */}
        <div className="flex justify-end">
          <ResearchReportViewer
            title={synthesis.title ?? 'Research Report'}
            synthesis={synthesis.summary}
          />
        </div>
      </div>

      {/* ── ReportViewer already imported, render it conditionally ── */}
      {showReport && (
        <ReportViewer
          synthesis={synthesis}
          onClose={() => setShowReport(false)}
        />
      )}
    </>
  );
};