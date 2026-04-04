import React, { useRef } from 'react';
import { X, Download, FileText } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { SynthesisData } from '../types/conversation';

interface ReportViewerProps {
  synthesis: SynthesisData;
  onClose: () => void;
}

export const ReportViewer: React.FC<ReportViewerProps> = ({ synthesis, onClose }) => {
  const reportRef = useRef<HTMLDivElement>(null);

  // ── Print-to-PDF ──────────────────────────────────────────────────────────
  const handleDownload = () => {
    const printContent = reportRef.current?.innerHTML;
    if (!printContent) return;

    const printWindow = window.open('', '_blank');
    if (!printWindow) return;

    printWindow.document.write(`
      <!DOCTYPE html>
      <html lang="en">
        <head>
          <meta charset="UTF-8" />
          <title>${synthesis.title || 'Research Report'}</title>
          <style>
            /* ── Reset ── */
            *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

            /* ── Page ── */
            @page { size: A4; margin: 2.5cm 2cm; }

            body {
              font-family: 'Georgia', 'Times New Roman', serif;
              font-size: 11pt;
              line-height: 1.75;
              color: #1a1a1a;
              background: #fff;
              padding: 0;
            }

            /* ── Typography ── */
            h1 { font-size: 22pt; margin-bottom: 0.4em; color: #111; }
            h2 { font-size: 16pt; margin-top: 1.6em; margin-bottom: 0.4em;
                 border-bottom: 1px solid #ddd; padding-bottom: 0.2em; color: #222; }
            h3 { font-size: 13pt; margin-top: 1.2em; margin-bottom: 0.3em; color: #333; }
            p  { margin-bottom: 0.9em; }
            ul, ol { padding-left: 1.5em; margin-bottom: 0.9em; }
            li { margin-bottom: 0.3em; }
            blockquote {
              border-left: 3px solid #aaa;
              padding-left: 1em;
              color: #555;
              margin: 1em 0;
            }
            code {
              font-family: 'Courier New', monospace;
              font-size: 9pt;
              background: #f4f4f4;
              padding: 0.1em 0.3em;
              border-radius: 2px;
            }
            pre {
              background: #f4f4f4;
              padding: 0.8em;
              border-radius: 4px;
              overflow-x: auto;
              margin-bottom: 0.9em;
            }
            pre code { background: none; padding: 0; }
            table {
              border-collapse: collapse;
              width: 100%;
              margin-bottom: 0.9em;
              font-size: 10pt;
            }
            th, td {
              border: 1px solid #ccc;
              padding: 0.4em 0.7em;
              text-align: left;
            }
            th { background: #f0f0f0; font-weight: 600; }
            a { color: #1a56db; }

            /* ── Report header ── */
            .report-header {
              border-bottom: 2px solid #1a56db;
              padding-bottom: 1em;
              margin-bottom: 2em;
            }
            .report-meta {
              font-size: 9pt;
              color: #666;
              margin-top: 0.4em;
            }

            /* ── No screen-only elements ── */
            @media print {
              .no-print { display: none !important; }
            }
          </style>
        </head>
        <body>
          <div class="report-header">
            <h1>${synthesis.title || 'Research Report'}</h1>
            <p class="report-meta">
              Generated: ${synthesis.generatedAt
        ? new Date(synthesis.generatedAt).toLocaleString()
        : new Date().toLocaleString()}
            </p>
          </div>
          ${printContent}
        </body>
      </html>
    `);

    printWindow.document.close();
    printWindow.focus();

    // Small delay lets images / fonts load before the print dialog opens
    setTimeout(() => {
      printWindow.print();
      printWindow.close();
    }, 400);
  };

  return (
    // ── Backdrop ──────────────────────────────────────────────────────────────
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4
                 bg-black/60 backdrop-blur-sm"
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      {/* ── Modal shell ── */}
      <div
        className="relative flex flex-col w-full max-w-4xl h-[90vh]
                   bg-white dark:bg-gray-900 rounded-xl shadow-2xl overflow-hidden"
      >
        {/* ── Toolbar ── */}
        <div
          className="flex items-center justify-between px-6 py-3 shrink-0
                     bg-white dark:bg-gray-800
                     border-b border-gray-200 dark:border-gray-700"
        >
          <div className="flex items-center gap-2 text-gray-800 dark:text-gray-100">
            <FileText className="w-5 h-5 text-blue-500 dark:text-emerald-500" />
            <span className="font-semibold text-sm truncate max-w-xs">
              {synthesis.title || 'Research Report'}
            </span>
          </div>

          <div className="flex items-center gap-2">
            <button
              onClick={handleDownload}
              className="flex items-center gap-1.5 px-4 py-1.5 text-sm font-medium
                         bg-blue-500 dark:bg-emerald-600
                         hover:bg-blue-600 dark:hover:bg-emerald-700
                         text-white rounded-lg transition-colors"
            >
              <Download className="w-4 h-4" />
              Download PDF
            </button>

            <button
              onClick={onClose}
              className="p-1.5 rounded-lg text-gray-500 dark:text-gray-400
                         hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors"
              aria-label="Close report"
            >
              <X className="w-5 h-5" />
            </button>
          </div>
        </div>

        {/* ── Scrollable report body ── */}
        <div className="flex-1 overflow-y-auto bg-gray-100 dark:bg-gray-950 p-6">
          {/* "Paper" card */}
          <div
            ref={reportRef}
            className="mx-auto max-w-3xl bg-white dark:bg-gray-800 rounded-lg shadow-md
                       px-10 py-10 text-gray-900 dark:text-gray-100
                       prose prose-sm dark:prose-invert max-w-none"
          >
            <ReactMarkdown remarkPlugins={[remarkGfm]}>
              {synthesis.content}
            </ReactMarkdown>
          </div>
        </div>
      </div>
    </div>
  );
};