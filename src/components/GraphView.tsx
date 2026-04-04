import React, { useState, useMemo, useRef, useCallback, useEffect } from 'react';
import { GraphState, GraphNode, GraphBatch, NodeStatus, SubAgentRole, SubAgentStatus, PlanAction } from '../types/graph';
import { QUERY_NODE_ID, PLAN_NODE_ID, SYNTHESIS_NODE_ID } from '../hooks/useConversationGraphs';
import { useTheme } from '../hooks/useTheme';

// ─── Layout constants ────────────────────────────────────────────────────────

const CANVAS_WIDTH = 840;
const CENTER_X = CANVAS_WIDTH / 2;
const NODE_R = 28;
const H_SPACING = 170;
const ROW_HEIGHT = 150;
const PADDING_TOP = 72;
const LABEL_LINE1_Y = NODE_R + 16;
const LABEL_LINE2_Y = NODE_R + 29;

// ─── Colour helpers ──────────────────────────────────────────────────────────

function nodeFill(status: NodeStatus, isDark: boolean): string {
  switch (status) {
    case 'completed': return '#059669';
    case 'running': return '#2563EB';
    case 'failed': return '#DC2626';
    default: return isDark ? '#374151' : '#E5E7EB';
  }
}

function nodeStroke(status: NodeStatus, isDark: boolean): string {
  switch (status) {
    case 'completed': return '#10B981';
    case 'running': return '#60A5FA';
    case 'failed': return '#EF4444';
    default: return isDark ? '#4B5563' : '#9CA3AF';
  }
}

function edgeColor(fromStatus: NodeStatus, toStatus: NodeStatus, isDark: boolean): string {
  if (fromStatus === 'completed') return '#059669';
  if (fromStatus === 'running') return '#2563EB';
  return isDark ? '#374151' : '#9CA3AF';
}

const SUB_AGENT_DOT_RUNNING: Record<SubAgentRole, string> = {
  search: '#60A5FA',
  analyst: '#A78BFA',
  qa: '#FBBF24',
};

function subAgentDotColor(role: SubAgentRole, status: SubAgentStatus, isDark: boolean): string {
  switch (status) {
    case 'running': return SUB_AGENT_DOT_RUNNING[role];
    case 'completed': return '#34D399';
    case 'failed': return '#F87171';
    default: return isDark ? '#4B5563' : '#D1D5DB';
  }
}

/** Gold fill / stroke for the synthesis (ReportComposer) node. */
function synthesisFill(status: NodeStatus, isDark: boolean): string {
  switch (status) {
    case 'completed': return '#92400E';  // amber-800
    case 'running': return '#B45309';  // amber-700
    case 'failed': return '#DC2626';
    default: return isDark ? '#374151' : '#E5E7EB';
  }
}
function synthesisStroke(status: NodeStatus, isDark: boolean): string {
  switch (status) {
    case 'completed': return '#F59E0B';  // amber-400
    case 'running': return '#FCD34D';  // amber-300
    case 'failed': return '#EF4444';
    default: return isDark ? '#78716C' : '#9CA3AF';  // stone-500 / gray-400
  }
}

/** Indigo fill / stroke for the initial user query node. */
function queryFill(status: NodeStatus, isDark: boolean): string {
  switch (status) {
    case 'completed': return '#3730A3';  // indigo-800
    case 'running': return '#4F46E5';   // indigo-600
    case 'failed': return '#DC2626';
    default: return isDark ? '#374151' : '#E5E7EB';
  }
}
function queryStroke(status: NodeStatus, isDark: boolean): string {
  switch (status) {
    case 'completed': return '#818CF8';  // indigo-400
    case 'running': return '#A5B4FC';   // indigo-300
    case 'failed': return '#EF4444';
    default: return isDark ? '#4B5563' : '#9CA3AF';
  }
}

/** Fill / stroke for the plan node — colour driven by planAction. */
function planFill(action: PlanAction | undefined): string {
  switch (action) {
    case 'approved': return '#059669';  // emerald-600
    case 'denied': return '#DC2626';  // red-600
    case 'modified': return '#B45309';  // amber-700
    default: return '#1D4ED8';  // blue-700 (pending)
  }
}
function planStroke(action: PlanAction | undefined): string {
  switch (action) {
    case 'approved': return '#34D399';  // emerald-400
    case 'denied': return '#F87171';  // red-400
    case 'modified': return '#FCD34D';  // amber-300
    default: return '#93C5FD';  // blue-300 (pending)
  }
}

/** Teal fill / stroke for self-optimization phase nodes. */
function optimizeFill(status: NodeStatus, isDark: boolean): string {
  switch (status) {
    case 'completed': return '#0F766E';  // teal-700
    case 'running': return '#0D9488';  // teal-600
    case 'failed': return '#DC2626';
    default: return isDark ? '#374151' : '#E5E7EB';
  }
}
function optimizeStroke(status: NodeStatus, isDark: boolean): string {
  switch (status) {
    case 'completed': return '#2DD4BF';  // teal-400
    case 'running': return '#5EEAD4';  // teal-300
    case 'failed': return '#EF4444';
    default: return isDark ? '#4B5563' : '#9CA3AF';
  }
}

// ─── Layout computation ──────────────────────────────────────────────────────

interface NodePos { id: number; x: number; y: number }

function computeLayout(batches: GraphBatch[]): NodePos[] {
  const positions: NodePos[] = [];
  batches.forEach((batch, bIdx) => {
    const n = batch.stepIds.length;
    const rowY = PADDING_TOP + bIdx * ROW_HEIGHT;
    batch.stepIds.forEach((id, k) => {
      positions.push({
        id,
        x: CENTER_X - ((n - 1) * H_SPACING) / 2 + k * H_SPACING,
        y: rowY,
      });
    });
  });
  return positions;
}

// ─── Node detail panel ───────────────────────────────────────────────────────

const STATUS_BADGE: Record<NodeStatus, string> = {
  pending: 'bg-gray-200 text-gray-700 dark:bg-gray-700 dark:text-gray-300',
  running: 'bg-blue-100 text-blue-700 dark:bg-blue-700 dark:text-blue-100',
  completed: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-700 dark:text-emerald-100',
  failed: 'bg-red-100 text-red-700 dark:bg-red-700 dark:text-red-100',
};

const AGENT_META: Record<SubAgentRole, { label: string; desc: string }> = {
  search: { label: 'Search Agent', desc: 'Web search & source discovery' },
  analyst: { label: 'Analyst Agent', desc: 'Data extraction & cross-referencing' },
  qa: { label: 'QA Agent', desc: 'Contradiction detection & quality review' },
};

const AGENT_STATUS_STYLE: Record<SubAgentStatus, string> = {
  pending: 'border-gray-300 bg-gray-50 dark:border-gray-700 dark:bg-gray-800/40',
  running: 'border-blue-400 bg-blue-50 dark:border-blue-500 dark:bg-blue-900/25',
  completed: 'border-emerald-400 bg-emerald-50 dark:border-emerald-500 dark:bg-emerald-900/25',
  failed: 'border-red-400 bg-red-50 dark:border-red-500 dark:bg-red-900/25',
};

const AGENT_DOT_STYLE: Record<SubAgentStatus, string> = {
  pending: 'bg-gray-400 dark:bg-gray-600',
  running: 'bg-blue-400 animate-pulse',
  completed: 'bg-emerald-400',
  failed: 'bg-red-400',
};

interface DetailPanelProps {
  node: GraphNode;
  onClose: () => void;
}

const NodeDetailPanel: React.FC<DetailPanelProps> = ({ node, onClose }) => {
  const [contradictionsExpanded, setContradictionsExpanded] = useState(false);
  const [notesExpanded, setNotesExpanded] = useState(false);
  let parsedResult: any = null;
  if (node.result) {
    try { parsedResult = JSON.parse(node.result); } catch { /* raw string */ }
  }

  return (
    <div className="p-4 space-y-5">
      {/* Header */}
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <h3 className="text-gray-900 dark:text-white font-semibold text-sm truncate">{node.name}</h3>
          <span className={`inline-block mt-1 px-2 py-0.5 rounded text-xs font-medium ${STATUS_BADGE[node.status]}`}>
            {node.status.charAt(0).toUpperCase() + node.status.slice(1)}
          </span>
        </div>
        <button
          onClick={onClose}
          className="flex-shrink-0 p-1 rounded text-gray-400 hover:text-gray-700 hover:bg-gray-100 dark:text-gray-500 dark:hover:text-gray-300 dark:hover:bg-gray-800 transition-colors"
          aria-label="Close detail panel"
        >
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>
      </div>

      {/* Description */}
      <div>
        <p className="text-xs text-gray-500 uppercase tracking-wider font-medium mb-1">Description</p>
        <p className="text-sm text-gray-700 dark:text-gray-300 leading-relaxed">{node.description}</p>
      </div>

      {/* Agent pipeline — only for research (non-synthesis) nodes */}
      {node.nodeType !== 'synthesis' && node.id !== SYNTHESIS_NODE_ID && node.subAgents.length > 0 && (
        <div>
          <p className="text-xs text-gray-500 uppercase tracking-wider font-medium mb-2">Agent Pipeline</p>
          <div className="space-y-1.5">
            {node.subAgents.map((agent) => {
              const meta = AGENT_META[agent.role];
              return (
                <div
                  key={agent.role}
                  className={`flex items-center gap-2 px-2 py-1.5 rounded border ${AGENT_STATUS_STYLE[agent.status]}`}
                >
                  <div className={`w-2 h-2 rounded-full flex-shrink-0 ${AGENT_DOT_STYLE[agent.status]}`} />
                  <div className="flex-1 min-w-0">
                    <p className="text-xs font-medium text-gray-800 dark:text-gray-200">{meta.label}</p>
                    <p className="text-xs text-gray-500 truncate">{meta.desc}</p>
                  </div>
                  <span className="text-xs text-gray-600 dark:text-gray-400 capitalize flex-shrink-0">{agent.status}</span>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Synthesis node info badge */}
      {node.nodeType === 'synthesis' && (
        <div className="px-3 py-2.5 bg-amber-50 border border-amber-200 dark:bg-amber-900/20 dark:border-amber-700/40 rounded-lg space-y-1">
          <p className="text-xs font-semibold text-amber-700 dark:text-amber-400">Report Composer</p>
          <p className="text-xs text-gray-600 dark:text-gray-400 leading-relaxed">
            Synthesizes all vetted findings from every search step into a
            PDF-formatted research report with numbered in-text citations and a
            full References section.
          </p>
        </div>
      )}

      {/* Optimize node info badge */}
      {node.nodeType === 'optimize' && (
        <div className="px-3 py-2.5 bg-teal-50 border border-teal-200 dark:bg-teal-900/20 dark:border-teal-700/40 rounded-lg space-y-1">
          <p className="text-xs font-semibold text-teal-700 dark:text-teal-400">Self-Optimization Phase</p>
          <p className="text-xs text-gray-600 dark:text-gray-400 leading-relaxed">{node.description}</p>
        </div>
      )}

      {/* Query node — show the full query text */}
      {node.nodeType === 'query' && (
        <div className="px-3 py-2.5 bg-indigo-50 border border-indigo-200 dark:bg-indigo-900/20 dark:border-indigo-700/40 rounded-lg space-y-1">
          <p className="text-xs font-semibold text-indigo-700 dark:text-indigo-400">User Query</p>
          <p className="text-xs text-gray-700 dark:text-gray-300 leading-relaxed break-words">{node.description}</p>
        </div>
      )}

      {/* Plan node — show approval state */}
      {node.nodeType === 'plan' && (
        <div className={`px-3 py-2.5 rounded-lg border space-y-1 ${node.planAction === 'approved' ? 'bg-emerald-50 border-emerald-200 dark:bg-emerald-900/20 dark:border-emerald-700/40' :
          node.planAction === 'denied' ? 'bg-red-50 border-red-200 dark:bg-red-900/20 dark:border-red-700/40' :
            node.planAction === 'modified' ? 'bg-amber-50 border-amber-200 dark:bg-amber-900/20 dark:border-amber-700/40' :
              'bg-blue-50 border-blue-200 dark:bg-blue-900/20 dark:border-blue-700/40'
          }`}>
          <p className={`text-xs font-semibold ${node.planAction === 'approved' ? 'text-emerald-700 dark:text-emerald-400' :
            node.planAction === 'denied' ? 'text-red-600 dark:text-red-400' :
              node.planAction === 'modified' ? 'text-amber-700 dark:text-amber-400' :
                'text-blue-700 dark:text-blue-400'
            }`}>
            {node.planAction === 'approved' ? '✓ Plan Approved' :
              node.planAction === 'denied' ? '✗ Plan Denied' :
                node.planAction === 'modified' ? '✎ Modification Requested' :
                  '◉ Awaiting Approval'}
          </p>
          <p className="text-xs text-gray-600 dark:text-gray-400 leading-relaxed">
            {node.planAction === 'approved' ? 'Research execution has begun.' :
              node.planAction === 'denied' ? 'The plan was rejected. A new query can be submitted.' :
                node.planAction === 'modified' ? 'Feedback sent — a revised plan is being generated.' :
                  'Waiting for the user to approve, modify, or deny this plan.'}
          </p>
        </div>
      )}

      {/* Tools used */}
      {node.toolsUsed.length > 0 && (
        <div>
          <p className="text-xs text-gray-500 uppercase tracking-wider font-medium mb-2">Tools Invoked</p>
          <div className="flex flex-wrap gap-1">
            {node.toolsUsed.map((tool) => (
              <span
                key={tool}
                className="px-2 py-0.5 bg-gray-100 border border-gray-200 dark:bg-gray-800 dark:border-gray-700 rounded text-xs text-gray-700 dark:text-gray-300 font-mono"
              >
                {tool}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* QA retries */}
      {node.qaRetries > 0 && (
        <div className="px-3 py-2 bg-orange-50 border border-orange-200 dark:bg-orange-900/20 dark:border-orange-700/40 rounded-lg">
          <p className="text-xs font-semibold text-orange-600 dark:text-orange-400">
            ↺ QA Re-investigation ×{node.qaRetries}
          </p>
          <p className="text-xs text-gray-600 dark:text-gray-400 mt-0.5 leading-relaxed">
            The QA agent detected contradictions and triggered additional search & analysis cycles.
          </p>
        </div>
      )}

      {/* Contradictions */}
      {node.contradictions.length > 0 && (
        <div>
          <p className="text-xs text-gray-500 uppercase tracking-wider font-medium mb-2">
            Flagged Contradictions ({node.contradictions.length})
          </p>
          <div className="space-y-2">
            {(contradictionsExpanded ? node.contradictions : node.contradictions.slice(0, 3)).map((c: any, i: number) => (
              <div key={i} className="text-xs bg-gray-50 border border-gray-200 dark:bg-gray-800 dark:border-gray-700 rounded p-2 space-y-1">
                <p className="text-yellow-700 dark:text-yellow-400 font-medium">Topic: {c.context || 'Unspecified'}</p>
                <p className="text-gray-600 dark:text-gray-400"><span className="text-gray-800 dark:text-gray-300">Source A:</span> {c.claim_a}</p>
                <p className="text-gray-600 dark:text-gray-400"><span className="text-gray-800 dark:text-gray-300">Source B:</span> {c.claim_b}</p>
              </div>
            ))}
            {node.contradictions.length > 3 && (
              <button
                onClick={() => setContradictionsExpanded((v) => !v)}
                className="text-xs text-blue-600 dark:text-blue-400 hover:underline mt-1"
              >
                {contradictionsExpanded
                  ? 'Show fewer'
                  : `+${node.contradictions.length - 3} more contradiction(s) — click to expand`}
              </button>
            )}
          </div>
        </div>
      )}

      {/* Step error */}
      {node.error && node.status === 'failed' && (
        <div>
          <p className="text-xs text-gray-500 uppercase tracking-wider font-medium mb-1">Error</p>
          <p className="text-xs text-red-600 bg-red-50 dark:text-red-400 dark:bg-gray-800 rounded p-2 break-words">{node.error}</p>
        </div>
      )}

      {/* Results */}
      {node.result && (
        <div>
          <p className="text-xs text-gray-500 uppercase tracking-wider font-medium mb-2">Results</p>
          {parsedResult ? (
            <div className="space-y-2">
              {parsedResult.claims?.length > 0 && (
                <div>
                  <p className="text-xs text-gray-500 mb-1">{parsedResult.claims.length} claim(s) extracted</p>
                  {parsedResult.claims.slice(0, 4).map((c: any, i: number) => (
                    <div key={i} className="text-xs bg-gray-100 dark:bg-gray-800 rounded p-2 mb-1">
                      <p className="text-gray-700 dark:text-gray-300">{c.claim}</p>
                      {c.corroborated === false && (
                        <span className="text-yellow-600 dark:text-yellow-500 text-xs">⚠ Unverified</span>
                      )}
                      {c.sources?.length > 0 && (
                        <p className="text-gray-500 dark:text-gray-600 mt-0.5 truncate text-xs">
                          {c.sources.slice(0, 2).join(' · ')}
                        </p>
                      )}
                    </div>
                  ))}
                  {parsedResult.claims.length > 4 && (
                    <p className="text-xs text-gray-600">+{parsedResult.claims.length - 4} more claims</p>
                  )}
                </div>
              )}
              {parsedResult.analyst_notes && (() => {
                const notes = typeof parsedResult.analyst_notes === 'string'
                  ? parsedResult.analyst_notes
                  : JSON.stringify(parsedResult.analyst_notes, null, 2);
                const truncated = !notesExpanded && notes.length > 280;
                return (
                  <div>
                    <p className="text-xs text-gray-500 mb-1">Analyst Notes</p>
                    <p className="text-xs text-gray-600 dark:text-gray-400 bg-gray-100 dark:bg-gray-800 rounded p-2 leading-relaxed whitespace-pre-wrap">
                      {truncated ? notes.slice(0, 280) + '…' : notes}
                    </p>
                    {notes.length > 280 && (
                      <button
                        onClick={() => setNotesExpanded((v) => !v)}
                        className="text-xs text-blue-600 dark:text-blue-400 hover:underline mt-1"
                      >
                        {notesExpanded ? 'Show less' : 'Show more'}
                      </button>
                    )}
                  </div>
                );
              })()}
            </div>
          ) : (
            <pre className="text-xs text-gray-600 bg-gray-100 dark:text-gray-400 dark:bg-gray-800 rounded p-2 overflow-x-auto whitespace-pre-wrap break-words">
              {node.result.slice(0, 600)}{node.result.length > 600 ? '\n…' : ''}
            </pre>
          )}
        </div>
      )}

      {/* Running placeholder */}
      {!node.result && node.status === 'running' && (
        <p className="text-xs text-gray-400 dark:text-gray-600 italic">Step in progress…</p>
      )}
    </div>
  );
};

// ─── Main GraphView ──────────────────────────────────────────────────────────

/** Format a duration in seconds as H:MM:SS or M:SS */
function formatElapsed(totalSeconds: number): string {
  const h = Math.floor(totalSeconds / 3600);
  const m = Math.floor((totalSeconds % 3600) / 60);
  const s = totalSeconds % 60;
  if (h > 0) {
    return `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
  }
  return `${m}:${String(s).padStart(2, '0')}`;
}

/** Running (or stopped) timer that reads startTime / endTime from graphState. */
const ResearchTimer: React.FC<{ startTime?: string; endTime?: string }> = ({
  startTime,
  endTime,
}) => {
  const [elapsed, setElapsed] = React.useState(0);

  React.useEffect(() => {
    if (!startTime) {
      setElapsed(0);
      return;
    }
    const start = new Date(startTime).getTime();

    const compute = () => {
      const end = endTime ? new Date(endTime).getTime() : Date.now();
      setElapsed(Math.max(0, Math.floor((end - start) / 1000)));
    };

    compute(); // immediate update

    if (endTime) return; // already finished — no interval needed

    const id = setInterval(compute, 1000);
    return () => clearInterval(id);
  }, [startTime, endTime]);

  if (!startTime) return null;

  const isRunning = !endTime;
  const startLabel = new Date(startTime).toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });

  return (
    <div className="flex items-center gap-2 ml-2 pl-2 border-l border-gray-300 dark:border-gray-700">
      <div className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${isRunning ? 'bg-emerald-400 animate-pulse' : 'bg-gray-500'}`} />
      <div className="flex flex-col leading-none">
        <span className="text-xs text-gray-500 leading-tight">
          Started {startLabel}
        </span>
        <span className={`text-xs font-mono font-semibold tabular-nums leading-tight ${isRunning ? 'text-emerald-400' : 'text-gray-400'}`}>
          {formatElapsed(elapsed)}
          {isRunning && <span className="text-gray-600"> elapsed</span>}
        </span>
      </div>
    </div>
  );
};

interface GraphViewProps {
  graphState: GraphState;
}

export const GraphView: React.FC<GraphViewProps> = ({ graphState }) => {
  const { nodes, batches, isReady, startTime, endTime } = graphState;
  const { theme } = useTheme();
  const isDark = theme === 'dark';
  const [selectedId, setSelectedId] = useState<number | null>(null);

  // ── Detail panel resize ─────────────────────────────────────────────────
  const [panelWidth, setPanelWidth] = useState(320);
  const PANEL_MIN_WIDTH = 256;
  const PANEL_MAX_WIDTH = 640;
  const panelIsResizing = useRef(false);
  const panelResizeStartX = useRef(0);
  const panelResizeStartWidth = useRef(0);

  const onPanelResizeStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    panelIsResizing.current = true;
    panelResizeStartX.current = e.clientX;
    panelResizeStartWidth.current = panelWidth;
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
  }, [panelWidth]);

  useEffect(() => {
    const onMove = (e: MouseEvent) => {
      if (!panelIsResizing.current) return;
      // Panel is on the right: dragging left makes it wider
      const delta = panelResizeStartX.current - e.clientX;
      const next = Math.min(PANEL_MAX_WIDTH, Math.max(PANEL_MIN_WIDTH, panelResizeStartWidth.current + delta));
      setPanelWidth(next);
    };
    const onUp = () => {
      if (!panelIsResizing.current) return;
      panelIsResizing.current = false;
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
    };
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
    return () => {
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
    };
  }, []);

  // Position map: stepId → {x, y}
  const posMap = useMemo(() => {
    const layout = computeLayout(batches);
    return new Map(layout.map((p) => [p.id, p]));
  }, [batches]);

  // Node lookup
  const nodeMap = useMemo(() => new Map(nodes.map((n) => [n.id, n])), [nodes]);

  // Edge pairs
  const edges = useMemo(() => {
    const list: { from: number; to: number }[] = [];
    for (let i = 0; i < batches.length - 1; i++) {
      for (const fromId of batches[i].stepIds) {
        for (const toId of batches[i + 1].stepIds) {
          list.push({ from: fromId, to: toId });
        }
      }
    }
    return list;
  }, [batches]);

  const svgHeight = PADDING_TOP + Math.max(batches.length, 1) * ROW_HEIGHT + NODE_R + 60;

  const selectedNode = selectedId !== null ? nodeMap.get(selectedId) ?? null : null;

  // ── Pan / zoom ───────────────────────────────────────────────────────────
  const containerRef = useRef<HTMLDivElement>(null);
  const [transform, setTransform] = useState({ scale: 1, x: 0, y: 0 });
  const [isDragging, setIsDragging] = useState(false);
  // Mirror transform in a ref so imperative handlers always see current values
  const xformRef = useRef(transform);
  useEffect(() => { xformRef.current = transform; }, [transform]);
  const isPanning = useRef(false);
  const panStart = useRef({ mouseX: 0, mouseY: 0, tx: 0, ty: 0 });
  const didDrag = useRef(false);

  /** Zoom centered on cursor position. */
  const handleWheel = useCallback((e: WheelEvent) => {
    e.preventDefault();
    const container = containerRef.current;
    if (!container) return;
    const rect = container.getBoundingClientRect();
    const cx = e.clientX - rect.left;
    const cy = e.clientY - rect.top;
    setTransform((prev) => {
      const factor = e.deltaY < 0 ? 1.12 : 1 / 1.12;
      const newScale = Math.min(4, Math.max(0.1, prev.scale * factor));
      const ratio = newScale / prev.scale;
      return { scale: newScale, x: cx - (cx - prev.x) * ratio, y: cy - (cy - prev.y) * ratio };
    });
  }, []);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    el.addEventListener('wheel', handleWheel, { passive: false });
    return () => el.removeEventListener('wheel', handleWheel);
  }, [handleWheel, isReady]);

  const handleMouseDown = useCallback((e: React.MouseEvent) => {
    if (e.button !== 0) return;
    isPanning.current = true;
    didDrag.current = false;
    setIsDragging(true);
    const { x, y } = xformRef.current;
    panStart.current = { mouseX: e.clientX, mouseY: e.clientY, tx: x, ty: y };
  }, []);

  const handleMouseMove = useCallback((e: React.MouseEvent) => {
    if (!isPanning.current) return;
    const dx = e.clientX - panStart.current.mouseX;
    const dy = e.clientY - panStart.current.mouseY;
    if (Math.abs(dx) > 4 || Math.abs(dy) > 4) didDrag.current = true;
    setTransform((prev) => ({ ...prev, x: panStart.current.tx + dx, y: panStart.current.ty + dy }));
  }, []);

  const handleMouseUp = useCallback(() => {
    isPanning.current = false;
    setIsDragging(false);
  }, []);

  /** Scale + translate so the full graph fits the visible container. */
  const fitView = useCallback(() => {
    const container = containerRef.current;
    if (!container) return;
    const cw = container.clientWidth;
    const ch = container.clientHeight;
    if (cw === 0 || ch === 0) return;
    const padding = 40;
    const scale = Math.min(1, Math.min((cw - padding * 2) / CANVAS_WIDTH, (ch - padding * 2) / svgHeight));
    setTransform({
      scale,
      x: (cw - CANVAS_WIDTH * scale) / 2,
      y: padding,
    });
  }, [svgHeight]);

  // Auto-fit whenever the graph becomes ready or the layout changes
  useEffect(() => {
    if (isReady) {
      // Small delay lets the container finish layout before measuring
      const id = setTimeout(fitView, 60);
      return () => clearTimeout(id);
    }
  }, [isReady, fitView]);

  // ── Empty / loading state ────────────────────────────────────────────────

  if (!isReady) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center bg-gray-50 dark:bg-gray-950 text-gray-600 dark:text-gray-400 px-4">
        <div className="text-center space-y-4 max-w-sm">
          <div className="w-16 h-16 mx-auto rounded-full bg-gray-200 dark:bg-gray-800 flex items-center justify-center">
            <svg className="w-8 h-8 text-gray-400 dark:text-gray-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
                d="M13 10V3L4 14h7v7l9-11h-7z" />
            </svg>
          </div>
          <p className="text-lg font-semibold text-gray-700 dark:text-gray-200">Execution Graph</p>
          <p className="text-sm text-gray-500 leading-relaxed">
            Submit a research query and approve the plan to visualise the multi-agent
            execution pipeline here in real time.
          </p>
        </div>
      </div>
    );
  }

  // ── Graph canvas ─────────────────────────────────────────────────────────

  return (
    <div className="flex-1 flex overflow-hidden bg-gray-50 dark:bg-gray-950">
      {/* SVG graph area */}
      <div className="flex-1 flex flex-col overflow-hidden">
        {/* Legend bar */}
        <div className="flex items-center gap-4 px-4 py-2 border-b border-gray-200 dark:border-gray-800 flex-shrink-0">
          <span className="text-xs text-gray-500 font-semibold uppercase tracking-widest">Legend</span>
          {(
            [
              { color: isDark ? '#374151' : '#D1D5DB', label: 'Pending' },
              { color: '#2563EB', label: 'Running' },
              { color: '#059669', label: 'Complete' },
              { color: '#DC2626', label: 'Failed' },
            ] as const
          ).map(({ color, label }) => (
            <div key={label} className="flex items-center gap-1.5">
              <div className="w-3 h-3 rounded-full" style={{ backgroundColor: color }} />
              <span className="text-xs text-gray-600 dark:text-gray-400">{label}</span>
            </div>
          ))}
          <div className="w-px h-4 bg-gray-300 dark:bg-gray-700 mx-1" />
          <div className="flex items-center gap-1.5">
            <div className="w-3 h-3 rounded-full" style={{ backgroundColor: '#4F46E5' }} />
            <span className="text-xs text-gray-600 dark:text-gray-400">Query</span>
          </div>
          <div className="flex items-center gap-1.5">
            <div className="w-3 h-3 rounded-full" style={{ backgroundColor: '#1D4ED8' }} />
            <span className="text-xs text-gray-600 dark:text-gray-400">Plan</span>
          </div>
          <div className="flex items-center gap-1.5">
            <div className="w-3 h-3 rounded-full" style={{ backgroundColor: '#0D9488' }} />
            <span className="text-xs text-gray-600 dark:text-gray-400">Optimize</span>
          </div>
          <div className="ml-auto flex items-center gap-3 text-xs text-gray-600">
            <span className="flex items-center gap-1">
              <span className="inline-block w-2 h-2 rounded-full bg-blue-400" />
              <span className="text-gray-500">Search</span>
            </span>
            <span className="flex items-center gap-1">
              <span className="inline-block w-2 h-2 rounded-full bg-purple-400" />
              <span className="text-gray-500">Analyst</span>
            </span>
            <span className="flex items-center gap-1">
              <span className="inline-block w-2 h-2 rounded-full bg-yellow-400" />
              <span className="text-gray-500">QA</span>
            </span>
            <span className="text-gray-600">· Drag to pan · Scroll to zoom</span>
          </div>
          <div className="flex items-center gap-1 ml-2">
            <span className="text-xs text-gray-500 dark:text-gray-600 tabular-nums">{Math.round(transform.scale * 100)}%</span>
            <button
              onClick={fitView}
              className="ml-1 px-2 py-0.5 rounded text-xs border border-gray-300 text-gray-600 hover:text-gray-900 hover:border-gray-500 dark:border-gray-700 dark:text-gray-400 dark:hover:text-gray-100 dark:hover:border-gray-500 transition-colors"
              title="Fit graph to view"
            >
              Fit
            </button>
            <button
              onClick={() => setTransform({ scale: 1, x: 0, y: 0 })}
              className="px-2 py-0.5 rounded text-xs border border-gray-300 text-gray-600 hover:text-gray-900 hover:border-gray-500 dark:border-gray-700 dark:text-gray-400 dark:hover:text-gray-100 dark:hover:border-gray-500 transition-colors"
              title="Reset to 100%"
            >
              100%
            </button>
          </div>
          <ResearchTimer startTime={startTime} endTime={endTime} />
        </div>

        {/* Zoomable / pannable SVG canvas */}
        <div
          ref={containerRef}
          className="flex-1 overflow-hidden relative select-none"
          style={{ cursor: isDragging ? 'grabbing' : 'grab' }}
          onMouseDown={handleMouseDown}
          onMouseMove={handleMouseMove}
          onMouseUp={handleMouseUp}
          onMouseLeave={handleMouseUp}
        >
          <svg
            width="100%"
            height="100%"
            style={{ display: 'block' }}
          >
            {/* ── Embedded animations ── */}
            <style>{`
              @keyframes rippleOut {
                0%   { r: ${NODE_R + 1}px; opacity: 0.65; }
                100% { r: ${NODE_R + 16}px; opacity: 0; }
              }
              .node-ripple { animation: rippleOut 1.8s ease-out infinite; }

              @keyframes retryGlow {
                0%, 100% { opacity: 1; }
                50%       { opacity: 0.35; }
              }
              .retry-badge { animation: retryGlow 1.2s ease-in-out infinite; }
            `}</style>

            {/* ── Arrow marker defs ── */}
            <defs>
              {(
                [
                  { id: 'arrow-default', color: isDark ? '#4B5563' : '#9CA3AF' },
                  { id: 'arrow-running', color: '#2563EB' },
                  { id: 'arrow-complete', color: '#059669' },
                ] as const
              ).map(({ id, color }) => (
                <marker key={id} id={id} markerWidth="8" markerHeight="6"
                  refX="7" refY="3" orient="auto">
                  <polygon points="0 0, 8 3, 0 6" fill={color} />
                </marker>
              ))}
            </defs>

            {/* ── All graph content inside the pan/zoom transform group ── */}
            <g transform={`translate(${transform.x}, ${transform.y}) scale(${transform.scale})`}>

              {/* ── Edges ── */}
              {edges.map(({ from, to }) => {
                const fp = posMap.get(from);
                const tp = posMap.get(to);
                if (!fp || !tp) return null;
                const x1 = fp.x, y1 = fp.y + NODE_R + 1;
                const x2 = tp.x, y2 = tp.y - NODE_R - 1;
                const midY = (y1 + y2) / 2;
                const fNode = nodeMap.get(from);
                const tNode = nodeMap.get(to);
                const fStatus = fNode?.status ?? 'pending';
                const tStatus = tNode?.status ?? 'pending';
                const color = edgeColor(fStatus, tStatus, isDark);
                const markerId =
                  fStatus === 'completed' ? 'arrow-complete'
                    : fStatus === 'running' ? 'arrow-running'
                      : 'arrow-default';
                return (
                  <path
                    key={`${from}-${to}`}
                    d={`M ${x1} ${y1} C ${x1} ${midY}, ${x2} ${midY}, ${x2} ${y2}`}
                    fill="none"
                    stroke={color}
                    strokeWidth={fStatus !== 'pending' ? 2 : 1.5}
                    markerEnd={`url(#${markerId})`}
                    style={{ transition: 'stroke 0.5s ease, stroke-width 0.3s ease' }}
                  />
                );
              })}

              {/* ── Nodes ── */}
              {batches.map((batch, bIdx) => {
                const n = batch.stepIds.length;
                return batch.stepIds.map((id, k) => {
                  const pos = posMap.get(id);
                  if (!pos) return null;
                  const { x, y } = pos;
                  const node = nodeMap.get(id);
                  if (!node) return null;
                  const isSelected = id === selectedId;
                  const isRunning = node.status === 'running';
                  const isSynthesis = node.nodeType === 'synthesis' || node.id === SYNTHESIS_NODE_ID;
                  const isQuery = node.nodeType === 'query';
                  const isPlan = node.nodeType === 'plan';
                  const isOptimize = node.nodeType === 'optimize';
                  const fill =
                    isSynthesis ? synthesisFill(node.status, isDark) :
                      isQuery ? queryFill(node.status, isDark) :
                        isPlan ? planFill(node.planAction) :
                          isOptimize ? optimizeFill(node.status, isDark) :
                            nodeFill(node.status, isDark);
                  const stroke =
                    isSynthesis ? synthesisStroke(node.status, isDark) :
                      isQuery ? queryStroke(node.status, isDark) :
                        isPlan ? planStroke(node.planAction) :
                          isOptimize ? optimizeStroke(node.status, isDark) :
                            nodeStroke(node.status, isDark);
                  const labelText = node.description.length > 30
                    ? node.description.slice(0, 27) + '…'
                    : node.description;

                  return (
                    <g
                      key={id}
                      onClick={() => { if (!didDrag.current) setSelectedId(id === selectedId ? null : id); }}
                      style={{ cursor: 'pointer' }}
                      role="button"
                      tabIndex={0}
                      onKeyDown={(e) => e.key === 'Enter' && setSelectedId(id === selectedId ? null : id)}
                      aria-label={`Step ${id}: ${node.description} (${node.status})`}
                    >
                      {/* Running ripple rings */}
                      {isRunning && [0, 1].map((i) => (
                        <circle
                          key={i}
                          cx={x} cy={y}
                          r={NODE_R + 1}
                          fill="none"
                          stroke={fill}
                          strokeWidth="2"
                          opacity="0"
                          className="node-ripple"
                          style={{ animationDelay: `${i * 0.7}s` }}
                        />
                      ))}

                      {/* Selection ring */}
                      {isSelected && (
                        <circle
                          cx={x} cy={y}
                          r={NODE_R + 8}
                          fill="none"
                          stroke={isDark ? '#F9FAFB' : '#111827'}
                          strokeWidth="1.5"
                          strokeDasharray="5 3"
                          opacity="0.6"
                        />
                      )}

                      {/* Parallel group tag — small ∥ pill above parallel nodes */}
                      {node.parallelGroup && n > 1 && (
                        <>
                          <rect
                            x={x - 16} y={y - NODE_R - 16}
                            width={32} height={13}
                            rx={6}
                            fill="#1E3A8A"
                            opacity="0.85"
                          />
                          <text
                            x={x} y={y - NODE_R - 6}
                            textAnchor="middle"
                            fill="#93C5FD"
                            fontSize="8"
                            fontWeight="700"
                            letterSpacing="1"
                          >
                            ∥ PAR
                          </text>
                        </>
                      )}

                      {/* Main node circle */}
                      <circle
                        cx={x} cy={y}
                        r={NODE_R}
                        fill={fill}
                        stroke={stroke}
                        strokeWidth={(isSynthesis || isPlan || isQuery || isOptimize) ? 2.5 : 2}
                        strokeDasharray={
                          isSynthesis ? '6 3' :
                            isPlan && node.planAction === 'pending' ? '4 2' :
                              undefined
                        }
                        style={{ transition: 'fill 0.4s ease, stroke 0.4s ease' }}
                      />

                      {/* Inner icon */}
                      {node.status === 'completed' && (
                        <path
                          d={`M ${x - 10} ${y + 1} l 7 7 l 11 -13`}
                          stroke="white" strokeWidth="2.5"
                          fill="none" strokeLinecap="round" strokeLinejoin="round"
                        />
                      )}
                      {node.status === 'failed' && (
                        <>
                          <line x1={x - 8} y1={y - 8} x2={x + 8} y2={y + 8}
                            stroke="white" strokeWidth="2.5" strokeLinecap="round" />
                          <line x1={x + 8} y1={y - 8} x2={x - 8} y2={y + 8}
                            stroke="white" strokeWidth="2.5" strokeLinecap="round" />
                        </>
                      )}
                      {(node.status === 'pending' || node.status === 'running') && (
                        <text
                          x={x} y={y + 5}
                          textAnchor="middle"
                          fill={node.status === 'running' ? 'white' : (isDark ? '#9CA3AF' : '#6B7280')}
                          fontSize={isSynthesis ? '16' : '14'}
                          fontWeight="700"
                        >
                          {isSynthesis ? '\u03a3' :
                            isQuery ? '?' :
                              isOptimize ? '\u2699' :
                                isPlan && node.planAction === 'modified' ? '\u270e' :
                                  isPlan ? '\u25ce' :
                                    id}
                        </text>
                      )}

                      {/* Sub-agent dots — only for research nodes */}
                      {!isSynthesis && !isQuery && !isPlan && !isOptimize && node.subAgents.map((agent, ai) => (
                        <circle
                          key={agent.role}
                          cx={x - 8 + ai * 8}
                          cy={y + NODE_R - 8}
                          r={3.5}
                          fill={subAgentDotColor(agent.role, agent.status, isDark)}
                          stroke="#111827"
                          strokeWidth="1"
                        />
                      ))}

                      {/* QA retry badge — top-right corner */}
                      {node.qaRetries > 0 && (
                        <g className="retry-badge">
                          <circle
                            cx={x + NODE_R - 4} cy={y - NODE_R + 4}
                            r={10}
                            fill="#EA580C"
                            stroke="#111827"
                            strokeWidth="1.5"
                          />
                          <text
                            x={x + NODE_R - 4} y={y - NODE_R + 4 + 4}
                            textAnchor="middle"
                            fill="white"
                            fontSize="8"
                            fontWeight="800"
                          >
                            ↺{node.qaRetries}
                          </text>
                        </g>
                      )}

                      {/* Step id / synthesis / query / plan / optimize label */}
                      <text
                        x={x} y={y + LABEL_LINE1_Y}
                        textAnchor="middle"
                        fill={
                          isSynthesis ? (isDark ? '#FCD34D' : '#B45309') :
                            isQuery ? (isDark ? '#A5B4FC' : '#4338CA') :
                              isPlan ? (isDark ? '#93C5FD' : '#1D4ED8') :
                                isOptimize ? (isDark ? '#5EEAD4' : '#0F766E') :
                                  (isDark ? '#D1D5DB' : '#374151')
                        }
                        fontSize="11"
                        fontWeight="600"
                      >
                        {isSynthesis ? 'Synthesis' :
                          isQuery ? 'Query' :
                            isPlan ? 'Plan' :
                              isOptimize ? node.name :
                                `Step ${id}`}
                      </text>
                      {/* Description label */}
                      <text
                        x={x} y={y + LABEL_LINE2_Y}
                        textAnchor="middle"
                        fill="#6B7280"
                        fontSize="10"
                      >
                        {labelText}
                      </text>
                    </g>
                  );
                });
              })}

            </g>{/* end pan/zoom transform group */}
          </svg>
        </div>
      </div>

      {/* ── Detail panel (resizable, slide-in from right) ── */}
      {selectedNode && (
        <div
          className="flex-shrink-0 border-l border-gray-200 bg-white dark:border-gray-800 dark:bg-gray-900 overflow-y-auto relative"
          style={{ width: panelWidth }}
        >
          {/* Drag handle on left edge */}
          <div
            className="absolute left-0 top-0 bottom-0 w-1 cursor-col-resize hover:bg-blue-400/40 dark:hover:bg-blue-500/40 transition-colors z-10"
            onMouseDown={onPanelResizeStart}
          />
          <NodeDetailPanel node={selectedNode} onClose={() => setSelectedId(null)} />
        </div>
      )}
    </div>
  );
};
