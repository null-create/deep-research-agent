import React, { useState, useCallback, useEffect, useRef } from 'react';
import { pdf } from '@react-pdf/renderer';
import { Document, Page, pdfjs } from 'react-pdf';
import { Download, X, ChevronLeft, ChevronRight, FileText, ZoomIn, ZoomOut } from 'lucide-react';
import { ResearchReportDocument } from './ResearchReportDocument';
import 'react-pdf/dist/Page/AnnotationLayer.css';
import 'react-pdf/dist/Page/TextLayer.css';
import pdfjsWorkerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url';

// Required worker config for react-pdf
pdfjs.GlobalWorkerOptions.workerSrc = pdfjsWorkerUrl;

const BASE_WIDTH = 750;
const ZOOM_STEP = 0.25;
const ZOOM_MIN = 0.5;
const ZOOM_MAX = 2.0;

interface ResearchReportViewerProps {
  title: string;
  synthesis: string;
  generatedAt?: string;
}

export const ResearchReportViewer: React.FC<ResearchReportViewerProps> = ({
  title,
  synthesis,
  generatedAt,
}) => {
  const [isOpen, setIsOpen] = useState(false);
  const [pdfUrl, setPdfUrl] = useState<string | null>(null);
  const [numPages, setNumPages] = useState<number>(0);
  const [currentPage, setCurrentPage] = useState(1);
  const [isGenerating, setIsGenerating] = useState(false);
  const [scale, setScale] = useState(1.0);
  const [pdfError, setPdfError] = useState<string | null>(null);
  const pdfContainerRef = useRef<HTMLDivElement>(null);

  // Revoke the previous blob URL whenever we create a new one or unmount.
  const prevPdfUrlRef = useRef<string | null>(null);
  useEffect(() => {
    return () => {
      if (prevPdfUrlRef.current) URL.revokeObjectURL(prevPdfUrlRef.current);
    };
  }, []);

  // Non-passive wheel listener for pinch-to-zoom on the PDF container.
  useEffect(() => {
    const el = pdfContainerRef.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      if (!e.ctrlKey && !e.metaKey) return;
      e.preventDefault();
      setScale((s) => Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, Math.round((s + (e.deltaY < 0 ? ZOOM_STEP : -ZOOM_STEP)) * 100) / 100)));
    };
    el.addEventListener('wheel', onWheel, { passive: false });
    return () => el.removeEventListener('wheel', onWheel);
  }, [isOpen]);

  const adjustZoom = useCallback((delta: number) => {
    setScale((s) => Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, Math.round((s + delta) * 100) / 100)));
  }, []);

  const generatePdf = async () => {
    setIsGenerating(true);
    setPdfError(null);
    try {
      const blob = await pdf(
        <ResearchReportDocument
          title={title}
          synthesis={synthesis}
          generatedAt={generatedAt}
        />
      ).toBlob();
      // Revoke the previous URL before creating a new one.
      if (prevPdfUrlRef.current) URL.revokeObjectURL(prevPdfUrlRef.current);
      const url = URL.createObjectURL(blob);
      prevPdfUrlRef.current = url;
      setPdfUrl(url);
      setIsOpen(true);
    } catch (err) {
      console.error('Failed to generate PDF:', err);
      setPdfError('Failed to generate report PDF. Please try again.');
    } finally {
      setIsGenerating(false);
    }
  };

  const handleClose = () => {
    setIsOpen(false);
    setCurrentPage(1);
    setScale(1.0);
    // Revoke blob URL on close to free memory.
    if (prevPdfUrlRef.current) {
      URL.revokeObjectURL(prevPdfUrlRef.current);
      prevPdfUrlRef.current = null;
    }
    setPdfUrl(null);
  };

  const handleDownload = () => {
    if (!pdfUrl) return;
    const a = document.createElement('a');
    a.href = pdfUrl;
    a.download = `${title.replace(/\s+/g, '_')}_report.pdf`;
    a.click();
  };

  return (
    <>
      {/* Trigger Button */}
      <button
        onClick={generatePdf}
        disabled={isGenerating}
        className="flex items-center gap-2 px-5 py-2.5 text-sm font-semibold text-white bg-blue-600 hover:bg-blue-700 dark:bg-emerald-600 dark:hover:bg-emerald-700 disabled:opacity-60 disabled:cursor-not-allowed rounded-xl shadow-sm transition-colors"
      >
        <FileText className="w-4 h-4" />
        {isGenerating ? 'Generating...' : 'View Report'}
      </button>

      {/* Error feedback */}
      {pdfError && (
        <p className="mt-2 text-xs text-red-500 text-center">{pdfError}</p>
      )}

      {/* Modal */}
      {isOpen && pdfUrl && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
          role="dialog"
          aria-modal="true"
          aria-label="Research Report PDF Viewer"
          onKeyDown={(e) => { if (e.key === 'Escape') handleClose(); }}
        >          <div className="relative flex flex-col w-full max-w-4xl max-h-[92vh] mx-4 bg-white dark:bg-gray-900 rounded-2xl shadow-2xl overflow-hidden">

            {/* Toolbar */}
            <div className="flex items-center justify-between px-5 py-3 bg-gray-50 dark:bg-gray-800 border-b border-gray-200 dark:border-gray-700 flex-shrink-0">
              <h2 className="text-sm font-semibold text-gray-800 dark:text-white truncate max-w-sm">
                {title}
              </h2>

              <div className="flex items-center gap-2">
                {/* Page Navigation */}
                {numPages > 1 && (
                  <div className="flex items-center gap-1 text-sm text-gray-600 dark:text-gray-300">
                    <button
                      onClick={() => setCurrentPage((p) => Math.max(1, p - 1))}
                      disabled={currentPage <= 1}
                      className="p-1.5 rounded-lg hover:bg-gray-200 dark:hover:bg-gray-700 disabled:opacity-40 transition-colors"
                    >
                      <ChevronLeft className="w-4 h-4" />
                    </button>
                    <span className="min-w-[64px] text-center">
                      {currentPage} / {numPages}
                    </span>
                    <button
                      onClick={() =>
                        setCurrentPage((p) => Math.min(numPages, p + 1))
                      }
                      disabled={currentPage >= numPages}
                      className="p-1.5 rounded-lg hover:bg-gray-200 dark:hover:bg-gray-700 disabled:opacity-40 transition-colors"
                    >
                      <ChevronRight className="w-4 h-4" />
                    </button>
                  </div>
                )}

                {/* Zoom Controls */}
                <div className="flex items-center gap-1">
                  <button
                    onClick={() => adjustZoom(-ZOOM_STEP)}
                    disabled={scale <= ZOOM_MIN}
                    className="p-1.5 rounded-lg hover:bg-gray-200 dark:hover:bg-gray-700 disabled:opacity-40 transition-colors text-gray-600 dark:text-gray-300"
                    title="Zoom out"
                    aria-label="Zoom out"
                  >
                    <ZoomOut className="w-4 h-4" />
                  </button>
                  <button
                    onClick={() => setScale(1.0)}
                    className="min-w-[52px] text-center text-xs font-mono font-medium text-gray-600 dark:text-gray-300 hover:bg-gray-200 dark:hover:bg-gray-700 rounded-lg px-1.5 py-1 transition-colors"
                    title="Reset to 100%"
                  >
                    {Math.round(scale * 100)}%
                  </button>
                  <button
                    onClick={() => adjustZoom(ZOOM_STEP)}
                    disabled={scale >= ZOOM_MAX}
                    className="p-1.5 rounded-lg hover:bg-gray-200 dark:hover:bg-gray-700 disabled:opacity-40 transition-colors text-gray-600 dark:text-gray-300"
                    title="Zoom in"
                    aria-label="Zoom in"
                  >
                    <ZoomIn className="w-4 h-4" />
                  </button>
                </div>

                {/* Download */}
                <button
                  onClick={handleDownload}
                  className="flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium text-blue-600 dark:text-emerald-400 bg-blue-50 dark:bg-emerald-900/20 hover:bg-blue-100 dark:hover:bg-emerald-900/40 rounded-lg transition-colors"
                >
                  <Download className="w-4 h-4" />
                  Download
                </button>

                {/* Close */}
                <button
                  onClick={handleClose}
                  className="p-1.5 text-gray-500 dark:text-gray-400 hover:text-gray-800 dark:hover:text-white hover:bg-gray-200 dark:hover:bg-gray-700 rounded-lg transition-colors"
                  aria-label="Close PDF viewer"
                >
                  <X className="w-5 h-5" />
                </button>
              </div>
            </div>

            {/* PDF Viewer — ref attached here for non-passive wheel listener */}
            <div
              ref={pdfContainerRef}
              className="flex-1 overflow-auto flex justify-center bg-gray-100 dark:bg-gray-950 px-4 py-6"
            >
              <Document
                file={pdfUrl}
                onLoadSuccess={({ numPages }) => setNumPages(numPages)}
                className="flex flex-col items-center gap-4"
              >
                <Page
                  pageNumber={currentPage}
                  width={Math.min(Math.round(BASE_WIDTH * scale), window.innerWidth - 48)}
                  className="shadow-lg rounded-lg overflow-hidden"
                />
              </Document>
            </div>
          </div>
        </div>
      )}
    </>
  );
};