import React, { useCallback, useEffect, useState } from 'react';
import { Upload, File, X, Loader, RefreshCw, AlertCircle } from 'lucide-react';
import { apiClient } from '../api/client';

interface ServerFile {
  name: string;
  size_bytes: number;
}

interface LocalFile {
  /** Stable unique key for React rendering. */
  id: string;
  name: string;
  size: number;
  uploading?: boolean;
  error?: boolean;
}

export const FileUploader: React.FC = () => {
  const [serverFiles, setServerFiles] = useState<LocalFile[]>([]);
  const [isDragging, setIsDragging] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);

  const loadFiles = useCallback(async (showRefreshing = false) => {
    if (showRefreshing) setIsRefreshing(true);
    setLoadError(null);
    try {
      const data: ServerFile[] = await apiClient.listFiles();
      setServerFiles(data.map((f) => ({ id: f.name, name: f.name, size: f.size_bytes })));
    } catch {
      setLoadError('Could not load files from the file handler server.');
    } finally {
      setIsLoading(false);
      setIsRefreshing(false);
    }
  }, []);

  useEffect(() => {
    loadFiles();
  }, [loadFiles]);

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(true);
  }, []);

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
  }, []);

  const handleDrop = useCallback(async (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
    await uploadFiles(Array.from(e.dataTransfer.files));
  }, []);

  const handleFileInput = useCallback(async (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files) {
      await uploadFiles(Array.from(e.target.files));
      // Reset input so the same file can be re-selected if needed
      e.target.value = '';
    }
  }, []);

  const uploadFiles = async (newFiles: File[]) => {
    if (newFiles.length === 0) return;

    // Optimistically add uploading placeholders with a stable unique id.
    const placeholders: LocalFile[] = newFiles.map((f) => ({
      id: `upload-${Date.now()}-${Math.random().toString(36).slice(2)}-${f.name}`,
      name: f.name,
      size: f.size,
      uploading: true,
    }));
    setServerFiles(prev => [...prev, ...placeholders]);

    for (let i = 0; i < newFiles.length; i++) {
      const file = newFiles[i];
      const placeholder = placeholders[i];
      try {
        await apiClient.uploadFile(file);
        setServerFiles(prev =>
          prev.map(f => f.id === placeholder.id ? { id: f.name, name: f.name, size: f.size } : f)
        );
      } catch {
        setServerFiles(prev =>
          prev.map(f => f.id === placeholder.id ? { ...f, uploading: false, error: true } : f)
        );
      }
    }
  };

  const handleDelete = async (name: string) => {
    const confirmed = window.confirm(`Delete "${name}" from the file handler server? This cannot be undone.`);
    if (!confirmed) return;

    setServerFiles(prev => prev.filter(f => f.name !== name));
    try {
      await apiClient.deleteFile(name);
    } catch {
      // Re-fetch on failure so state stays accurate
      loadFiles();
    }
  };

  const formatSize = (bytes: number) => {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  };

  return (
    <div className="space-y-3">
      {/* Upload drop zone */}
      <div
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
        className={`border-2 border-dashed rounded-lg p-6 text-center transition-colors ${isDragging
          ? 'border-blue-500 dark:border-emerald-500 bg-blue-50 dark:bg-emerald-900/20'
          : 'border-gray-300 dark:border-gray-600 hover:border-gray-400 dark:hover:border-gray-500'
          }`}
      >
        <Upload className="w-8 h-8 mx-auto mb-2 text-gray-400" />
        <p className="text-sm text-gray-600 dark:text-gray-300 mb-2">
          Drop files here or click to select
        </p>
        <input
          type="file"
          multiple
          onChange={handleFileInput}
          className="hidden"
          id="file-upload"
        />
        <label
          htmlFor="file-upload"
          className="inline-block px-3 py-1.5 text-xs font-medium bg-blue-500 dark:bg-emerald-600
                     text-white rounded-lg hover:bg-blue-600 dark:hover:bg-emerald-700 cursor-pointer transition-colors"
        >
          Select Files
        </label>
      </div>

      {/* File list header */}
      <div className="flex items-center justify-between">
        <span className="text-xs font-semibold text-gray-600 dark:text-gray-400 uppercase tracking-wide">
          Agent Files {!isLoading && `(${serverFiles.length})`}
        </span>
        <button
          onClick={() => loadFiles(true)}
          disabled={isRefreshing}
          className="p-1 rounded hover:bg-gray-200 dark:hover:bg-gray-700 transition-colors"
          title="Refresh file list"
        >
          <RefreshCw className={`w-3.5 h-3.5 text-gray-500 dark:text-gray-400 ${isRefreshing ? 'animate-spin' : ''}`} />
        </button>
      </div>

      {/* States */}
      {isLoading && (
        <div className="flex items-center justify-center py-6 gap-2 text-sm text-gray-500 dark:text-gray-400">
          <Loader className="w-4 h-4 animate-spin" />
          Loading files…
        </div>
      )}

      {!isLoading && loadError && (
        <div className="flex items-start gap-2 p-3 bg-red-50 dark:bg-red-900/20 rounded-lg
                        text-xs text-red-600 dark:text-red-400">
          <AlertCircle className="w-4 h-4 flex-shrink-0 mt-0.5" />
          {loadError}
        </div>
      )}

      {!isLoading && !loadError && serverFiles.length === 0 && (
        <p className="text-xs text-gray-400 dark:text-gray-500 text-center py-4">
          No files yet. Upload files to give the agent context.
        </p>
      )}

      {/* File list */}
      {serverFiles.length > 0 && (
        <div className="space-y-1.5">
          {serverFiles.map((file) => (
            <div
              key={file.id}
              className="flex items-center gap-2 p-2.5 bg-white dark:bg-gray-800
                         rounded-lg border border-gray-200 dark:border-gray-700"
            >
              {file.uploading ? (
                <Loader className="w-4 h-4 flex-shrink-0 animate-spin text-blue-500 dark:text-emerald-500" />
              ) : file.error ? (
                <AlertCircle className="w-4 h-4 flex-shrink-0 text-red-500" />
              ) : (
                <File className="w-4 h-4 flex-shrink-0 text-gray-400" />
              )}
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium text-gray-900 dark:text-gray-100 truncate" title={file.name}>
                  {file.name}
                </p>
                <p className="text-xs text-gray-500 dark:text-gray-400">
                  {file.uploading ? 'Uploading…' : file.error ? 'Upload failed' : formatSize(file.size)}
                </p>
              </div>
              {!file.uploading && (
                <button
                  onClick={() => handleDelete(file.name)}
                  className="p-1 rounded hover:bg-red-100 dark:hover:bg-red-900/30
                             text-gray-400 hover:text-red-500 transition-colors flex-shrink-0"
                  title={`Delete ${file.name}`}
                >
                  <X className="w-3.5 h-3.5" />
                </button>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
};