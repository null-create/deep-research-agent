import React, { useEffect, useState, useCallback } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { X, BookOpen, ChevronRight } from 'lucide-react';
import { apiClient } from '../api/client';

interface DocsViewerProps {
  onClose: () => void;
}

/** Convert a filename like "API_SERVER.md" to a readable label "API Server". */
function toReadableTitle(filename: string): string {
  return filename
    .replace(/\.md$/, '')
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

export const DocsViewer: React.FC<DocsViewerProps> = ({ onClose }) => {
  const [docList, setDocList] = useState<string[]>([]);
  const [selectedDoc, setSelectedDoc] = useState<string | null>(null);
  const [content, setContent] = useState<string | null>(null);
  const [loadingList, setLoadingList] = useState(true);
  const [loadingContent, setLoadingContent] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Load doc list on mount
  useEffect(() => {
    setLoadingList(true);
    setError(null);
    apiClient
      .listDocs()
      .then((docs) => {
        setDocList(docs);
        if (docs.length > 0) setSelectedDoc(docs[0]);
      })
      .catch(() => setError('Failed to load documentation index.'))
      .finally(() => setLoadingList(false));
  }, []);

  // Load doc content when selection changes — use an ignore flag to prevent
  // a stale earlier response from overwriting a newer selection.
  useEffect(() => {
    if (!selectedDoc) return;
    let ignore = false;
    setLoadingContent(true);
    setContent(null);
    setError(null);
    apiClient
      .getDoc(selectedDoc)
      .then((result) => { if (!ignore) setContent(result); })
      .catch(() => { if (!ignore) setError(`Failed to load "${toReadableTitle(selectedDoc)}".`); })
      .finally(() => { if (!ignore) setLoadingContent(false); });
    return () => { ignore = true; };
  }, [selectedDoc]);

  // Close on Escape key
  const handleKeyDown = useCallback(
    (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    },
    [onClose]
  );
  useEffect(() => {
    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [handleKeyDown]);

  return (
    /* Backdrop */
    <div
      className="fixed inset-0 z-50 flex items-stretch bg-black/50 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
      aria-label="Documentation"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      {/* Modal panel — stacks vertically on small screens */}
      <div className="relative flex flex-col sm:flex-row w-full max-w-6xl mx-auto my-6 bg-white dark:bg-gray-900 rounded-xl shadow-2xl overflow-hidden">

        {/* ── Left sidebar: doc list ── */}
        <aside className="w-full sm:w-56 flex-shrink-0 flex flex-col border-b sm:border-b-0 sm:border-r border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800 max-h-48 sm:max-h-none">
          <div className="flex items-center gap-2 px-4 py-3 border-b border-gray-200 dark:border-gray-700">
            <BookOpen className="w-4 h-4 text-indigo-500 flex-shrink-0" />
            <span className="text-sm font-semibold text-gray-700 dark:text-gray-200 truncate">
              Documentation
            </span>
          </div>
          <nav className="flex-1 overflow-y-auto py-2" aria-label="Documentation pages">
            {loadingList && (
              <p className="px-4 py-3 text-xs text-gray-400">Loading…</p>
            )}
            {!loadingList && docList.map((filename) => (
              <button
                key={filename}
                onClick={() => setSelectedDoc(filename)}
                className={`w-full flex items-center gap-2 px-4 py-2.5 text-left text-sm transition-colors ${selectedDoc === filename
                  ? 'bg-indigo-50 dark:bg-indigo-900/40 text-indigo-700 dark:text-indigo-300 font-medium'
                  : 'text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-700/60 hover:text-gray-900 dark:hover:text-gray-100'
                  }`}
                aria-current={selectedDoc === filename ? 'page' : undefined}
              >
                {selectedDoc === filename && (
                  <ChevronRight className="w-3.5 h-3.5 flex-shrink-0 text-indigo-500" aria-hidden="true" />
                )}
                <span className={selectedDoc === filename ? '' : 'ml-5'}>
                  {toReadableTitle(filename)}
                </span>
              </button>
            ))}
          </nav>
        </aside>

        {/* ── Right panel: content ── */}
        <div className="flex flex-col flex-1 min-w-0">
          {/* Header */}
          <div className="flex items-center justify-between px-6 py-3 border-b border-gray-200 dark:border-gray-700 flex-shrink-0">
            <h2 className="text-base font-semibold text-gray-900 dark:text-white truncate">
              {selectedDoc ? toReadableTitle(selectedDoc) : 'Documentation'}
            </h2>
            <button
              onClick={onClose}
              className="p-1.5 rounded-md hover:bg-gray-200 dark:hover:bg-gray-700 transition-colors"
              aria-label="Close documentation"
            >
              <X className="w-5 h-5 text-gray-500 dark:text-gray-400" />
            </button>
          </div>

          {/* Content area */}
          <div className="flex-1 overflow-y-auto px-8 py-6">
            {loadingContent && (
              <p className="text-sm text-gray-400 text-center py-16">Loading…</p>
            )}
            {error && !loadingContent && (
              <p className="text-sm text-red-500 dark:text-red-400 text-center py-16">{error}</p>
            )}
            {!loadingContent && !error && content && (
              <div
                className="
                  prose prose-sm dark:prose-invert max-w-none
                  prose-headings:font-semibold
                  prose-headings:text-gray-900 dark:prose-headings:text-gray-100
                  prose-h1:text-2xl prose-h1:mb-4 prose-h1:pb-2 prose-h1:border-b prose-h1:border-gray-200 dark:prose-h1:border-gray-700
                  prose-h2:text-xl prose-h2:mt-8 prose-h2:mb-3
                  prose-h3:text-base prose-h3:mt-6
                  prose-p:text-gray-700 dark:prose-p:text-gray-300 prose-p:leading-relaxed
                  prose-li:text-gray-700 dark:prose-li:text-gray-300
                  prose-a:text-indigo-600 dark:prose-a:text-indigo-400 prose-a:no-underline hover:prose-a:underline
                  prose-code:bg-gray-100 dark:prose-code:bg-gray-800 prose-code:px-1.5 prose-code:py-0.5 prose-code:rounded prose-code:text-xs prose-code:font-mono prose-code:text-gray-800 dark:prose-code:text-gray-200
                  prose-pre:bg-gray-950 dark:prose-pre:bg-gray-950 prose-pre:text-gray-100 prose-pre:rounded-lg prose-pre:p-4 prose-pre:overflow-x-auto
                  prose-blockquote:border-indigo-400 prose-blockquote:text-gray-600 dark:prose-blockquote:text-gray-400
                  prose-table:text-sm
                  prose-th:bg-gray-100 dark:prose-th:bg-gray-800 prose-th:text-gray-700 dark:prose-th:text-gray-300
                  prose-td:border-gray-200 dark:prose-td:border-gray-700
                  prose-strong:text-gray-900 dark:prose-strong:text-gray-100
                  prose-hr:border-gray-200 dark:prose-hr:border-gray-700
                "
              >
                <ReactMarkdown remarkPlugins={[remarkGfm]}>
                  {content}
                </ReactMarkdown>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};
