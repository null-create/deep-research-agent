import React, { useState, useRef, useCallback, useEffect } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {
  FileText,
  Server,
  Settings,
  Moon,
  Sun,
  ChevronLeft,
  ChevronRight,
  MessageSquare,
  Plus,
  Pencil,
  Trash2,
  BookOpen,
  ArrowLeft,
  Cpu,
} from 'lucide-react';
import { FileUploader } from './FileUploader';
import { MCPServerManager } from './MCPServerManager';
import { ModelSettings } from './ModelSettings';
import { Conversation } from '../types/conversation';
import { useTheme } from '../hooks/useTheme';
import { apiClient } from '../api/client';

export type ResearchDepth = 'shallow' | 'moderate' | 'deep';

interface SidebarProps {
  conversations: Conversation[];
  activeConversationId: string | null;
  onSelectConversation: (id: string) => void;
  onNewChat: () => void;
  onRenameConversation: (id: string, newTitle: string) => void;
  onDeleteConversation: (id: string) => void;
  researchDepth: ResearchDepth;
  onResearchDepthChange: (depth: ResearchDepth) => void;
  isResearching: boolean;
  /** Called when the user closes the sidebar on mobile (via backdrop or close button). */
  onMobileClose?: () => void;
}

type SidebarTab = 'conversations' | 'files' | 'servers' | 'settings';

export const Sidebar: React.FC<SidebarProps> = ({
  conversations,
  activeConversationId,
  onSelectConversation,
  onNewChat,
  onRenameConversation,
  onDeleteConversation,
  researchDepth,
  onResearchDepthChange,
  isResearching,
  onMobileClose,
}) => {
  const [activeTab, setActiveTab] = useState<SidebarTab>('conversations');
  const [isCollapsed, setIsCollapsed] = useState(() => typeof window !== 'undefined' && window.innerWidth < 768);
  const [isMobile, setIsMobile] = useState(() => typeof window !== 'undefined' && window.innerWidth < 768);
  const [width, setWidth] = useState(288); // default w-72 equivalent
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editTitle, setEditTitle] = useState('');
  const [showResearchMethods, setShowResearchMethods] = useState(false);
  const [showModelSettings, setShowModelSettings] = useState(false);
  const [researchMethodsContent, setResearchMethodsContent] = useState<string | null>(null);
  const [researchMethodsLoading, setResearchMethodsLoading] = useState(false);
  const [researchMethodsError, setResearchMethodsError] = useState<string | null>(null);
  const [pendingDepth, setPendingDepth] = useState<ResearchDepth | null>(null);
  const isResizing = useRef(false);
  const startX = useRef(0);
  const startWidth = useRef(0);
  const { theme, toggleTheme } = useTheme();

  const MIN_WIDTH = 220;
  const MAX_WIDTH = 600;

  const onMouseDown = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    isResizing.current = true;
    startX.current = e.clientX;
    startWidth.current = width;
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
  }, [width]);

  const onTouchStart = useCallback((e: React.TouchEvent) => {
    e.preventDefault();
    isResizing.current = true;
    startX.current = e.touches[0].clientX;
    startWidth.current = width;
    document.body.style.userSelect = 'none';
  }, [width]);

  // Only react when the mobile/desktop breakpoint is crossed, not on every
  // resize pixel — prevents overriding the user's manual collapse/expand.
  useEffect(() => {
    let wasMobile = window.innerWidth < 768;
    const handleResize = () => {
      const nowMobile = window.innerWidth < 768;
      if (nowMobile !== wasMobile) {
        wasMobile = nowMobile;
        setIsMobile(nowMobile);
        setIsCollapsed(nowMobile);
      }
    };
    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, []);

  useEffect(() => {
    const onMouseMove = (e: MouseEvent) => {
      if (!isResizing.current) return;
      const delta = e.clientX - startX.current;
      const next = Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, startWidth.current + delta));
      setWidth(next);
    };
    const onTouchMove = (e: TouchEvent) => {
      if (!isResizing.current) return;
      const delta = e.touches[0].clientX - startX.current;
      const next = Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, startWidth.current + delta));
      setWidth(next);
    };
    const onMouseUp = () => {
      if (!isResizing.current) return;
      isResizing.current = false;
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
    };
    const onTouchEnd = () => {
      if (!isResizing.current) return;
      isResizing.current = false;
      document.body.style.userSelect = '';
    };
    window.addEventListener('mousemove', onMouseMove);
    window.addEventListener('touchmove', onTouchMove, { passive: false });
    window.addEventListener('mouseup', onMouseUp);
    window.addEventListener('touchend', onTouchEnd);
    return () => {
      window.removeEventListener('mousemove', onMouseMove);
      window.removeEventListener('touchmove', onTouchMove);
      window.removeEventListener('mouseup', onMouseUp);
      window.removeEventListener('touchend', onTouchEnd);
    };
  }, []);

  const handleStartRename = (conv: Conversation) => {
    setEditingId(conv.id);
    setEditTitle(conv.title);
  };

  const handleSaveRename = (id: string) => {
    if (editTitle.trim()) {
      onRenameConversation(id, editTitle.trim());
    }
    setEditingId(null);
    setEditTitle('');
  };

  const handleKeyDown = (e: React.KeyboardEvent, id: string) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      // Blur will fire after this; prevent the double-save by clearing
      // editingId first so the onBlur handler no-ops.
      setEditingId(null);
      if (editTitle.trim()) {
        onRenameConversation(id, editTitle.trim());
      }
      setEditTitle('');
    }
    if (e.key === 'Escape') {
      setEditingId(null);
      setEditTitle('');
    }
  };

  const handleOpenResearchMethods = async () => {
    setShowResearchMethods(true);
    if (researchMethodsContent !== null) return;
    setResearchMethodsLoading(true);
    setResearchMethodsError(null);
    try {
      const content = await apiClient.getResearchMethods();
      setResearchMethodsContent(content);
    } catch {
      setResearchMethodsError('Failed to load research methods document.');
    } finally {
      setResearchMethodsLoading(false);
    }
  };

  // Collapsed sidebar view (desktop rail — not shown on mobile)
  if (isCollapsed && !isMobile) {
    return (
      <div className="w-16 bg-gray-50 dark:bg-gray-900 border-r border-gray-200 dark:border-gray-700
                      flex flex-col items-center py-4 gap-4">
        <button
          onClick={() => setIsCollapsed(false)}
          className="p-2 rounded-lg hover:bg-gray-200 dark:hover:bg-gray-700 transition-colors"
          title="Expand sidebar"
        >
          <ChevronRight className="w-5 h-5 text-gray-600 dark:text-gray-300" />
        </button>

        <div className="w-8 h-px bg-gray-200 dark:bg-gray-700" />

        <button
          onClick={() => { setIsCollapsed(false); setActiveTab('conversations'); }}
          className="p-2 rounded-lg hover:bg-gray-200 dark:hover:bg-gray-700 transition-colors"
          title="Conversations"
        >
          <MessageSquare className="w-5 h-5 text-gray-600 dark:text-gray-300" />
        </button>

        <button
          onClick={() => { setIsCollapsed(false); setActiveTab('files'); }}
          className="p-2 rounded-lg hover:bg-gray-200 dark:hover:bg-gray-700 transition-colors"
          title="Files"
        >
          <FileText className="w-5 h-5 text-gray-600 dark:text-gray-300" />
        </button>

        <button
          onClick={() => { setIsCollapsed(false); setActiveTab('servers'); }}
          className="p-2 rounded-lg hover:bg-gray-200 dark:hover:bg-gray-700 transition-colors"
          title="MCP Servers"
        >
          <Server className="w-5 h-5 text-gray-600 dark:text-gray-300" />
        </button>

        <button
          onClick={() => { setIsCollapsed(false); setActiveTab('settings'); }}
          className="p-2 rounded-lg hover:bg-gray-200 dark:hover:bg-gray-700 transition-colors"
          title="Settings"
        >
          <Settings className="w-5 h-5 text-gray-600 dark:text-gray-300" />
        </button>

        <div className="flex-1" />

        <button
          onClick={toggleTheme}
          className="p-2 rounded-lg hover:bg-gray-200 dark:hover:bg-gray-700 transition-colors"
          title={`Switch to ${theme === 'dark' ? 'light' : 'dark'} mode`}
        >
          {theme === 'dark' ? (
            <Sun className="w-5 h-5 text-yellow-400" />
          ) : (
            <Moon className="w-5 h-5 text-gray-600" />
          )}
        </button>
      </div>
    );
  }

  // Expanded sidebar panel (shared by desktop inline and mobile overlay)
  const sidebarPanel = (
    <div
      style={isMobile ? undefined : { width }}
      className={`relative bg-gray-50 dark:bg-gray-900 border-r border-gray-200 dark:border-gray-700
                 flex flex-col h-full flex-shrink-0 ${isMobile ? 'w-80 max-w-[85vw]' : ''}`}
    >
      {/* Drag-to-resize handle on the right edge — desktop only */}
      {!isMobile && (
        <div
          onMouseDown={onMouseDown}
          className="absolute right-0 top-0 h-full w-1.5 cursor-col-resize z-10
                     hover:bg-blue-400 dark:hover:bg-emerald-500 transition-colors opacity-0 hover:opacity-60
                     active:opacity-100"
          title="Drag to resize"
          role="separator"
          aria-orientation="vertical"
        />
      )}
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-gray-200 dark:border-gray-700">
        <h2 className="text-sm font-semibold text-gray-700 dark:text-gray-200">
          Research Resources
        </h2>
        <button
          onClick={() => isMobile ? onMobileClose?.() : setIsCollapsed(true)}
          className="p-1.5 rounded-lg hover:bg-gray-200 dark:hover:bg-gray-700 transition-colors"
          title="Collapse sidebar"
          aria-label="Close sidebar"
        >
          <ChevronLeft className="w-4 h-4 text-gray-600 dark:text-gray-300" />
        </button>
      </div>

      {/* Tab Navigation */}
      <div className="flex border-b border-gray-200 dark:border-gray-700">
        {[
          { key: 'conversations' as SidebarTab, icon: MessageSquare, label: 'Chats' },
          { key: 'files' as SidebarTab, icon: FileText, label: 'Files' },
          { key: 'servers' as SidebarTab, icon: Server, label: 'Servers' },
          { key: 'settings' as SidebarTab, icon: Settings, label: 'Settings' },
        ].map(({ key, icon: Icon, label }) => (
          <button
            key={key}
            onClick={() => { setActiveTab(key); if (key !== 'settings') { setShowResearchMethods(false); setShowModelSettings(false); } }}
            className={`flex-1 flex flex-col items-center py-2 px-1 text-xs transition-colors
              ${activeTab === key
                ? 'text-blue-600 dark:text-blue-400 border-b-2 border-blue-600 dark:border-blue-400'
                : 'text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-300'
              }`}
            title={label}
          >
            <Icon className="w-4 h-4 mb-0.5" />
            <span>{label}</span>
          </button>
        ))}
      </div>

      {/* Tab Content */}
      <div className="flex-1 overflow-y-auto">
        {/* ===== Conversations Tab ===== */}
        {activeTab === 'conversations' && (
          <div className="flex flex-col h-full">
            {/* New Chat Button */}
            <div className="p-3">
              <button
                onClick={onNewChat}
                className="w-full px-4 py-2.5 bg-blue-600 hover:bg-blue-700 text-white
                           font-medium rounded-lg transition-colors duration-200
                           flex items-center justify-center gap-2 text-sm"
              >
                <Plus className="w-4 h-4" />
                New Research
              </button>
            </div>

            {/* Conversation List */}
            <div className="flex-1 overflow-y-auto px-2">
              {conversations.length === 0 ? (
                <p className="px-3 py-4 text-sm text-gray-400 dark:text-gray-500 text-center">
                  No conversations yet. Start a new research!
                </p>
              ) : (
                <div className="space-y-1">
                  {conversations.map((conv) => (
                    <div
                      key={conv.id}
                      className={`group flex items-center rounded-lg cursor-pointer transition-colors duration-150
                        ${activeConversationId === conv.id
                          ? 'bg-blue-100 dark:bg-blue-900/30 border border-blue-200 dark:border-blue-800'
                          : 'hover:bg-gray-100 dark:hover:bg-gray-800 border border-transparent'
                        }`}
                    >
                      {editingId === conv.id ? (
                        <div className="flex-1 p-2">
                          <input
                            type="text"
                            value={editTitle}
                            onChange={(e) => setEditTitle(e.target.value)}
                            onKeyDown={(e) => handleKeyDown(e, conv.id)}
                            onBlur={() => handleSaveRename(conv.id)}
                            className="w-full px-2 py-1 text-sm bg-white dark:bg-gray-700
                                       border border-blue-400 rounded focus:outline-none focus:ring-1
                                       focus:ring-blue-500 text-gray-900 dark:text-white"
                            autoFocus
                          />
                        </div>
                      ) : (
                        <>
                          <div
                            className="flex-1 p-2 min-w-0"
                            onClick={() => onSelectConversation(conv.id)}
                          >
                            <p className="text-sm font-medium text-gray-800 dark:text-gray-200 truncate">
                              {conv.title}
                            </p>
                            <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">
                              {conv.messages?.length || 0} messages · {conv.updatedAt?.toLocaleDateString() || ''}
                            </p>
                          </div>

                          {/* Action buttons visible on hover */}
                          <div className="flex items-center gap-1 pr-2 opacity-0 group-hover:opacity-100 transition-opacity">
                            <button
                              onClick={(e) => {
                                e.stopPropagation();
                                handleStartRename(conv);
                              }}
                              className="p-1 rounded hover:bg-gray-200 dark:hover:bg-gray-600"
                              title="Rename"
                            >
                              <Pencil className="w-3.5 h-3.5 text-gray-500 dark:text-gray-400" />
                            </button>
                            <button
                              onClick={(e) => {
                                e.stopPropagation();
                                if (window.confirm('Delete this conversation?')) {
                                  onDeleteConversation(conv.id);
                                }
                              }}
                              className="p-1 rounded hover:bg-red-100 dark:hover:bg-red-900/30 
             text-gray-400 hover:text-red-500 opacity-0 group-hover:opacity-100 
             transition-opacity"
                              title="Delete conversation"
                            >
                              <Trash2 className="w-4 h-4" />
                            </button>
                          </div>
                        </>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        )}

        {/* ===== Files Tab ===== */}
        {activeTab === 'files' && (
          <div className="p-3">
            <FileUploader />
          </div>
        )}

        {/* ===== MCP Servers Tab ===== */}
        {activeTab === 'servers' && (
          <div className="p-3">
            <MCPServerManager />
          </div>
        )}

        {/* ===== Settings Tab ===== */}
        {activeTab === 'settings' && !showResearchMethods && !showModelSettings && (
          <div className="p-3 space-y-4">
            <h3 className="text-sm font-semibold text-gray-700 dark:text-gray-200">
              Appearance
            </h3>
            <div className="flex items-center justify-between p-3 bg-white dark:bg-gray-800
                            rounded-lg border border-gray-200 dark:border-gray-700">
              <div className="flex items-center gap-2">
                {theme === 'dark' ? (
                  <Moon className="w-4 h-4 text-gray-400" />
                ) : (
                  <Sun className="w-4 h-4 text-yellow-500" />
                )}
                <span className="text-sm text-gray-700 dark:text-gray-300">
                  {theme === 'dark' ? 'Dark Mode' : 'Light Mode'}
                </span>
              </div>
              <button
                onClick={toggleTheme}
                className={`relative w-11 h-6 rounded-full transition-colors duration-200 ${theme === 'dark' ? 'bg-blue-600' : 'bg-gray-300'
                  }`}
              >
                <div
                  className={`absolute top-0.5 left-0.5 w-5 h-5 bg-white rounded-full shadow
                              transition-transform duration-200 ${theme === 'dark' ? 'translate-x-5' : 'translate-x-0'
                    }`}
                />
              </button>
            </div>

            <h3 className="text-sm font-semibold text-gray-700 dark:text-gray-200 pt-2">
              Research Settings
            </h3>
            <div className="p-3 bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700">
              <label className="text-sm text-gray-700 dark:text-gray-300 block mb-2">
                Research Depth
              </label>
              <div className={`flex rounded-lg overflow-hidden border ${isResearching
                ? 'border-gray-200 dark:border-gray-700 opacity-50 cursor-not-allowed'
                : 'border-gray-200 dark:border-gray-600'
                }`}>
                {([
                  { value: 'shallow', label: 'Shallow', title: 'Quick: Search → Analyze, no QA' },
                  { value: 'moderate', label: 'Moderate', title: 'Standard: Search → Analyze → QA (2 retries)' },
                  { value: 'deep', label: 'Deep', title: 'Thorough: in-depth search, rigorous analysis, QA (3 retries)' },
                ] as { value: ResearchDepth; label: string; title: string }[]).map(({ value, label, title }) => {
                  const displayDepth = pendingDepth ?? researchDepth;
                  const isSelected = displayDepth === value;
                  const isPending = pendingDepth !== null && pendingDepth === value;
                  return (
                    <button
                      key={value}
                      disabled={isResearching}
                      onClick={() => {
                        if (value !== researchDepth) {
                          setPendingDepth(value);
                        } else {
                          setPendingDepth(null);
                        }
                      }}
                      title={isResearching ? 'Cannot change depth during an active research session' : title}
                      className={`flex-1 py-1.5 text-xs font-medium transition-colors ${isResearching
                        ? 'cursor-not-allowed'
                        : 'cursor-pointer'
                        } ${isSelected
                          ? isPending
                            ? 'bg-amber-500 dark:bg-amber-500 text-white'
                            : 'bg-blue-600 dark:bg-blue-500 text-white'
                          : 'text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-700'
                        }`}
                    >
                      {label}
                    </button>
                  );
                })}
              </div>
              <p className="mt-2 text-xs text-gray-500 dark:text-gray-400">
                {isResearching
                  ? 'Research depth cannot be changed during an active session.'
                  : (() => {
                    const d = pendingDepth ?? researchDepth;
                    if (d === 'shallow') return 'Quick lookup: search → analyze, no QA pass.';
                    if (d === 'moderate') return 'Balanced: search → analyze → QA with up to 2 retries.';
                    return 'Thorough: in-depth queries, rigorous analysis, QA with up to 3 retries.';
                  })()
                }
              </p>
              {pendingDepth !== null && !isResearching && (
                <div className="mt-3 flex gap-2">
                  <button
                    onClick={() => {
                      onResearchDepthChange(pendingDepth);
                      setPendingDepth(null);
                    }}
                    className="flex-1 py-1.5 text-xs font-semibold rounded-md bg-blue-600 hover:bg-blue-700
                               text-white transition-colors"
                  >
                    Save
                  </button>
                  <button
                    onClick={() => setPendingDepth(null)}
                    className="flex-1 py-1.5 text-xs font-medium rounded-md
                               bg-gray-100 hover:bg-gray-200 dark:bg-gray-700 dark:hover:bg-gray-600
                               text-gray-600 dark:text-gray-300 transition-colors"
                  >
                    Cancel
                  </button>
                </div>
              )}
            </div>

            <h3 className="text-sm font-semibold text-gray-700 dark:text-gray-200 pt-2">
              Agent Documents
            </h3>
            <button
              onClick={handleOpenResearchMethods}
              className="w-full flex items-center gap-3 p-3 bg-white dark:bg-gray-800
                         rounded-lg border border-gray-200 dark:border-gray-700
                         hover:bg-gray-50 dark:hover:bg-gray-750 transition-colors text-left"
            >
              <BookOpen className="w-4 h-4 text-blue-500 dark:text-blue-400 flex-shrink-0" />
              <div className="min-w-0">
                <p className="text-sm font-medium text-gray-700 dark:text-gray-300">
                  Research Methods
                </p>
                <p className="text-xs text-gray-500 dark:text-gray-400 truncate">
                  View the agent's research methodology guide
                </p>
              </div>
            </button>

            <h3 className="text-sm font-semibold text-gray-700 dark:text-gray-200 pt-2">
              Model Configuration
            </h3>
            <button
              onClick={() => setShowModelSettings(true)}
              className="w-full flex items-center gap-3 p-3 bg-white dark:bg-gray-800
                         rounded-lg border border-gray-200 dark:border-gray-700
                         hover:bg-gray-50 dark:hover:bg-gray-750 transition-colors text-left"
            >
              <Cpu className="w-4 h-4 text-purple-500 dark:text-purple-400 flex-shrink-0" />
              <div className="min-w-0">
                <p className="text-sm font-medium text-gray-700 dark:text-gray-300">
                  Model Settings
                </p>
                <p className="text-xs text-gray-500 dark:text-gray-400 truncate">
                  Backend, API keys, models &amp; sampling params
                </p>
              </div>
            </button>
          </div>
        )}

        {/* ===== Model Settings Panel ===== */}
        {activeTab === 'settings' && showModelSettings && (
          <ModelSettings onClose={() => setShowModelSettings(false)} />
        )}

        {/* ===== Research Methods Viewer ===== */}
        {activeTab === 'settings' && showResearchMethods && (
          <div className="flex flex-col h-full">
            <div className="flex items-center gap-2 px-3 py-2 border-b border-gray-200 dark:border-gray-700 flex-shrink-0">
              <button
                onClick={() => setShowResearchMethods(false)}
                className="p-1 rounded hover:bg-gray-200 dark:hover:bg-gray-700 transition-colors"
                title="Back to Settings"
              >
                <ArrowLeft className="w-4 h-4 text-gray-600 dark:text-gray-300" />
              </button>
              <span className="text-sm font-semibold text-gray-700 dark:text-gray-200">
                Research Methods
              </span>
            </div>
            <div className="flex-1 overflow-y-auto px-3 py-3">
              {researchMethodsLoading && (
                <p className="text-sm text-gray-500 dark:text-gray-400 text-center py-8">
                  Loading…
                </p>
              )}
              {researchMethodsError && (
                <p className="text-sm text-red-500 dark:text-red-400 text-center py-8">
                  {researchMethodsError}
                </p>
              )}
              {researchMethodsContent && (
                <div className="prose prose-sm dark:prose-invert max-w-none
                                prose-headings:font-semibold prose-headings:text-gray-800 dark:prose-headings:text-gray-100
                                prose-p:text-gray-700 dark:prose-p:text-gray-300
                                prose-li:text-gray-700 dark:prose-li:text-gray-300
                                prose-code:bg-gray-100 dark:prose-code:bg-gray-800
                                prose-code:px-1 prose-code:rounded prose-code:text-xs
                                prose-strong:text-gray-800 dark:prose-strong:text-gray-100">
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>
                    {researchMethodsContent}
                  </ReactMarkdown>
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );

  // On mobile: render as a fixed overlay drawer with a backdrop
  if (isMobile) {
    return (
      <>
        {/* Backdrop */}
        <div
          className="fixed inset-0 z-30 bg-black/40 backdrop-blur-sm"
          onClick={onMobileClose}
          aria-hidden="true"
        />
        {/* Drawer */}
        <div className="fixed inset-y-0 left-0 z-40 h-full shadow-2xl">
          {sidebarPanel}
        </div>
      </>
    );
  }

  return sidebarPanel;
};