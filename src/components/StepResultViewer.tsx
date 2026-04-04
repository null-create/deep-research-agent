import React, { useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { ChevronDown, ChevronUp, FileText, AlertCircle } from 'lucide-react';

interface AnalystClaim {
  claim: string;
  sources: string[];
  corroborated: boolean;
}

interface AnalystTension {
  topic: string;
  source_a: string;
  position_a: string;
  source_b: string;
  position_b: string;
  severity?: string;
  gap_analysis?: string;
  gap_explanation?: string;
}

interface AnalystResult {
  claims?: AnalystClaim[];
  tensions?: AnalystTension[];
  analyst_notes?: string | Record<string, unknown>;
}

function formatAnalystResult(data: AnalystResult): string {
  const parts: string[] = [];

  if (data.claims?.length) {
    parts.push('## Claims\n');
    data.claims.forEach((c, i) => {
      const icon = c.corroborated ? '✓' : '✗';
      parts.push(`**${i + 1}. ${c.claim}** — ${icon}\n`);
      c.sources.forEach((s) => parts.push(`- ${s}`));
      parts.push('');
    });
  }

  if (data.tensions?.length) {
    parts.push('## Tensions\n');
    data.tensions.forEach((t) => {
      parts.push(`### ${t.topic}\n`);
      parts.push(`**Source A:** ${t.source_a}  \n> ${t.position_a}\n`);
      parts.push(`**Source B:** ${t.source_b}  \n> ${t.position_b}\n`);
      if (t.severity) parts.push(`**Severity:** \`${t.severity}\`\n`);
      const explanation = t.gap_analysis ?? t.gap_explanation;
      if (explanation) parts.push(`${explanation}\n`);
    });
  }

  if (data.analyst_notes) {
    parts.push('## Analyst Notes\n');
    parts.push(typeof data.analyst_notes === 'string'
      ? data.analyst_notes
      : JSON.stringify(data.analyst_notes, null, 2));
  }

  return parts.join('\n');
}

function resultToMarkdown(result: string): string {
  try {
    const parsed = JSON.parse(result);
    if (
      parsed &&
      typeof parsed === 'object' &&
      (parsed.claims || parsed.tensions || parsed.analyst_notes)
    ) {
      return formatAnalystResult(parsed as AnalystResult);
    }
    return '```json\n' + JSON.stringify(parsed, null, 2) + '\n```';
  } catch {
    return result;
  }
}

interface StepResultViewerProps {
  stepName: string;
  result?: string;
  error?: string;
}

export const StepResultViewer: React.FC<StepResultViewerProps> = ({
  stepName,
  result,
  error,
}) => {
  const [isOpen, setIsOpen] = useState(false);

  // Don't render the button at all if there's nothing to show
  if (!result && !error) return null;

  const hasError = Boolean(error);

  return (
    <div className="mt-2">
      <button
        onClick={() => setIsOpen((prev) => !prev)}
        className={`flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-lg
                    border transition-colors w-full justify-between
                    ${hasError
            ? 'bg-red-50 dark:bg-red-900/20 border-red-200 dark:border-red-700 text-red-700 dark:text-red-400 hover:bg-red-100 dark:hover:bg-red-900/30'
            : 'bg-gray-50 dark:bg-gray-700/50 border-gray-200 dark:border-gray-600 text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-700'
          }`}
        aria-expanded={isOpen}
      >
        <span className="flex items-center gap-1.5">
          {hasError ? (
            <AlertCircle className="w-3.5 h-3.5" />
          ) : (
            <FileText className="w-3.5 h-3.5" />
          )}
          {hasError ? `View error for: ${stepName}` : `View results for: ${stepName}`}
        </span>
        {isOpen ? (
          <ChevronUp className="w-3.5 h-3.5 shrink-0" />
        ) : (
          <ChevronDown className="w-3.5 h-3.5 shrink-0" />
        )}
      </button>

      {isOpen && (
        <div
          className={`mt-2 p-3 rounded-lg border text-xs max-h-[40rem] overflow-y-auto
                      ${hasError
              ? 'bg-red-50 dark:bg-red-900/10 border-red-200 dark:border-red-800 text-red-800 dark:text-red-300'
              : 'bg-gray-50 dark:bg-gray-900/50 border-gray-200 dark:border-gray-700 text-gray-700 dark:text-gray-300'
            }`}
        >
          {hasError ? (
            <pre className="whitespace-pre-wrap font-mono leading-relaxed">{error}</pre>
          ) : result ? (
            <div className="prose prose-sm dark:prose-invert max-w-none
                            prose-headings:text-gray-800 dark:prose-headings:text-gray-200
                            prose-p:text-gray-700 dark:prose-p:text-gray-300
                            prose-li:text-gray-700 dark:prose-li:text-gray-300
                            prose-blockquote:text-gray-600 dark:prose-blockquote:text-gray-400
                            prose-strong:text-gray-800 dark:prose-strong:text-gray-200
                            prose-code:text-xs">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>
                {resultToMarkdown(result)}
              </ReactMarkdown>
            </div>
          ) : null}
        </div>
      )}
    </div>
  );
};