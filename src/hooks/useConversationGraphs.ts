import { useState, useCallback, useEffect } from 'react';
import {
  GraphState,
  GraphNode,
  GraphBatch,
  NodeStatus,
  SubAgentRole,
  SubAgentStatus,
  PlanAction,
  OptimizePhase,
} from '../types/graph';

const STORAGE_KEY = 'deep_research_graphs';

export const EMPTY_GRAPH_STATE: GraphState = { nodes: [], batches: [], isReady: false };

/** Reserved node IDs (negative so they never clash with plan step IDs). */
export const QUERY_NODE_ID = -2;
export const PLAN_NODE_ID = -1;
export const SYNTHESIS_NODE_ID = 0;

/** Self-optimization workflow node IDs. */
export const OPTIMIZE_READ_ID = -10;
export const OPTIMIZE_RETRIEVE_ID = -11;
export const OPTIMIZE_ANALYZE_ID = -12;
export const OPTIMIZE_DEVELOP_ID = -13;
export const OPTIMIZE_UPDATE_ID = -14;

const OPTIMIZE_PHASE_MAP: Record<OptimizePhase, number> = {
  read_methods: OPTIMIZE_READ_ID,
  retrieve_memories: OPTIMIZE_RETRIEVE_ID,
  analyze: OPTIMIZE_ANALYZE_ID,
  develop: OPTIMIZE_DEVELOP_ID,
  update_methods: OPTIMIZE_UPDATE_ID,
};

function inferBatches(nodes: GraphNode[]): GraphBatch[] {
  const batches: GraphBatch[] = [];
  for (const node of nodes) {
    if (!node.parallelGroup) {
      batches.push({ parallel: false, stepIds: [node.id] });
    } else {
      const last = batches[batches.length - 1];
      const lastGroup = last
        ? nodes.find((n) => n.id === last.stepIds[0])?.parallelGroup
        : null;
      if (last && last.parallel && lastGroup === node.parallelGroup) {
        last.stepIds.push(node.id);
      } else {
        batches.push({ parallel: true, stepIds: [node.id] });
      }
    }
  }
  return batches.map((b) => ({ ...b, parallel: b.stepIds.length > 1 }));
}

/**
 * Manages per-conversation execution graph states.
 * Each conversation gets its own GraphState stored under its ID.
 * State is persisted to localStorage so graphs survive page reloads.
 */
export function useConversationGraphs() {
  const [graphs, setGraphs] = useState<Record<string, GraphState>>(() => {
    try {
      const stored = localStorage.getItem(STORAGE_KEY);
      return stored ? JSON.parse(stored) : {};
    } catch {
      return {};
    }
  });

  // Persist whenever graphs change
  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(graphs));
    } catch { }
  }, [graphs]);

  const getGraph = useCallback(
    (convId: string): GraphState => graphs[convId] || EMPTY_GRAPH_STATE,
    [graphs]
  );

  const setConvState = useCallback(
    (convId: string, updater: (prev: GraphState) => GraphState) => {
      if (!convId) return;
      setGraphs((prev) => ({
        ...prev,
        [convId]: updater(prev[convId] || EMPTY_GRAPH_STATE),
      }));
    },
    []
  );

  /** Add a query node immediately when the user submits a research query. */
  const addQueryNode = useCallback(
    (convId: string, query: string) => {
      if (!convId) return;
      setConvState(convId, (prev) => {
        // Idempotent — don't duplicate if the graph already has one
        if (prev.nodes.some((n) => n.id === QUERY_NODE_ID)) return prev;
        const queryNode: GraphNode = {
          id: QUERY_NODE_ID,
          name: 'Research Query',
          description: query,
          status: 'running' as NodeStatus,
          parallelGroup: null,
          nodeType: 'query',
          toolsUsed: [],
          subAgents: [],
          qaRetries: 0,
          contradictions: [],
        };
        return {
          nodes: [queryNode],
          batches: [{ parallel: false, stepIds: [QUERY_NODE_ID] }],
          isReady: true,
        };
      });
    },
    [setConvState]
  );

  const initFromPlan = useCallback(
    (convId: string, planData: any) => {
      if (!convId || !planData?.steps?.length) return;
      setConvState(convId, (prev) => {
        // Preserve the query node (mark it completed — we got a plan back)
        const existingQuery = prev.nodes.find((n) => n.id === QUERY_NODE_ID);
        const queryNode = existingQuery
          ? { ...existingQuery, status: 'completed' as NodeStatus }
          : null;

        const planNode: GraphNode = {
          id: PLAN_NODE_ID,
          name: 'Research Plan',
          description:
            planData.goal
              ? planData.goal.length > 70
                ? planData.goal.slice(0, 67) + '…'
                : planData.goal
              : `${planData.steps.length} step${planData.steps.length !== 1 ? 's' : ''}`,
          status: 'pending' as NodeStatus,
          parallelGroup: null,
          nodeType: 'plan',
          planAction: 'pending' as PlanAction,
          toolsUsed: [],
          subAgents: [],
          qaRetries: 0,
          contradictions: [],
        };

        const stepNodes: GraphNode[] = (planData.steps as any[]).map((step: any) => ({
          id: step.id,
          name: step.name || `Step ${step.id}`,
          description: step.description || '',
          status: 'pending' as NodeStatus,
          parallelGroup: step.parallel_group ?? null,
          toolsUsed: [],
          subAgents: [
            { role: 'search' as SubAgentRole, status: 'pending' as SubAgentStatus },
            { role: 'analyst' as SubAgentRole, status: 'pending' as SubAgentStatus },
            { role: 'qa' as SubAgentRole, status: 'pending' as SubAgentStatus },
          ],
          qaRetries: 0,
          contradictions: [],
        }));

        const stepBatches = inferBatches(stepNodes);
        const allNodes = [
          ...(queryNode ? [queryNode] : []),
          planNode,
          ...stepNodes,
        ];
        const allBatches: GraphBatch[] = [
          ...(queryNode ? [{ parallel: false, stepIds: [QUERY_NODE_ID] }] : []),
          { parallel: false, stepIds: [PLAN_NODE_ID] },
          ...stepBatches,
        ];
        return { nodes: allNodes, batches: allBatches, isReady: true };
      });
    },
    [setConvState]
  );

  /** Update the plan node when the user approves, modifies, or denies the plan. */
  const updatePlanAction = useCallback(
    (convId: string, action: PlanAction) => {
      if (!convId) return;
      const newStatus: NodeStatus =
        action === 'approved' ? 'completed' :
          action === 'denied' ? 'failed' :
            action === 'modified' ? 'running' : 'pending';
      setConvState(convId, (prev) => ({
        ...prev,
        nodes: prev.nodes.map((n) =>
          n.id === PLAN_NODE_ID
            ? { ...n, planAction: action, status: newStatus }
            : n
        ),
      }));
    },
    [setConvState]
  );

  const overrideBatches = useCallback(
    (convId: string, rawBatches: { parallel: boolean; step_ids: number[] }[]) => {
      setConvState(convId, (prev) => {
        // Preserve preamble batches (query / plan nodes have negative IDs) so
        // they remain visible after the orchestrator sends its own batch layout.
        const preambleBatches = prev.batches.filter((b) =>
          b.stepIds.every((id) => id < 0)
        );
        return {
          ...prev,
          batches: [
            ...preambleBatches,
            ...rawBatches.map((b) => ({
              parallel: b.parallel,
              stepIds: b.step_ids,
            })),
          ],
        };
      });
    },
    [setConvState]
  );

  const setNodeRunning = useCallback(
    (convId: string, stepId: number) => {
      setConvState(convId, (prev) => ({
        ...prev,
        nodes: prev.nodes.map((n) =>
          n.id === stepId ? { ...n, status: 'running' as NodeStatus } : n
        ),
      }));
    },
    [setConvState]
  );

  const setNodeCompleted = useCallback(
    (convId: string, stepId: number, result?: string, toolsUsed?: string[]) => {
      setConvState(convId, (prev) => ({
        ...prev,
        nodes: prev.nodes.map((n) =>
          n.id === stepId
            ? {
              ...n,
              status: 'completed' as NodeStatus,
              result: result ?? n.result,
              toolsUsed: toolsUsed?.length ? toolsUsed : n.toolsUsed,
              subAgents: n.subAgents.map((a) => ({
                ...a,
                status: 'completed' as SubAgentStatus,
              })),
            }
            : n
        ),
      }));
    },
    [setConvState]
  );

  const setNodeFailed = useCallback(
    (convId: string, stepId: number, error?: string) => {
      setConvState(convId, (prev) => ({
        ...prev,
        nodes: prev.nodes.map((n) =>
          n.id === stepId
            ? { ...n, status: 'failed' as NodeStatus, error: error ?? n.error }
            : n
        ),
      }));
    },
    [setConvState]
  );

  const updateSubAgent = useCallback(
    (convId: string, stepId: number, role: SubAgentRole, status: SubAgentStatus) => {
      const roleOrder: SubAgentRole[] = ['search', 'analyst', 'qa'];
      const incomingIdx = roleOrder.indexOf(role);
      setConvState(convId, (prev) => ({
        ...prev,
        nodes: prev.nodes.map((n) => {
          if (n.id !== stepId) return n;
          const updatedAgents = n.subAgents.map((a) => {
            if (a.role === role) return { ...a, status };
            const agentIdx = roleOrder.indexOf(a.role);
            if (
              status === 'running' &&
              agentIdx < incomingIdx &&
              a.status === 'pending'
            ) {
              return { ...a, status: 'completed' as SubAgentStatus };
            }
            return a;
          });
          return { ...n, subAgents: updatedAgents };
        }),
      }));
    },
    [setConvState]
  );

  const incrementQaRetries = useCallback(
    (convId: string, stepId: number) => {
      setConvState(convId, (prev) => ({
        ...prev,
        nodes: prev.nodes.map((n) =>
          n.id === stepId ? { ...n, qaRetries: n.qaRetries + 1 } : n
        ),
      }));
    },
    [setConvState]
  );

  const addContradictions = useCallback(
    (convId: string, stepId: number, contradictions: any[]) => {
      if (!contradictions?.length) return;
      setConvState(convId, (prev) => ({
        ...prev,
        nodes: prev.nodes.map((n) =>
          n.id === stepId
            ? { ...n, contradictions: [...n.contradictions, ...contradictions] }
            : n
        ),
      }));
    },
    [setConvState]
  );

  /**
   * Append a synthesis node (id=SYNTHESIS_NODE_ID) as the final batch.
   * Safe to call multiple times — subsequent calls are no-ops.
   */
  const addSynthesisNode = useCallback(
    (convId: string) => {
      setConvState(convId, (prev) => {
        if (prev.nodes.some((n) => n.id === SYNTHESIS_NODE_ID)) return prev;
        const synthesisNode: GraphNode = {
          id: SYNTHESIS_NODE_ID,
          name: 'Final Synthesis',
          description: 'ReportComposer — synthesises all vetted findings into a PDF-formatted report with full citations.',
          status: 'pending' as NodeStatus,
          parallelGroup: null,
          nodeType: 'synthesis',
          toolsUsed: [],
          subAgents: [],
          qaRetries: 0,
          contradictions: [],
        };
        return {
          ...prev,
          nodes: [...prev.nodes, synthesisNode],
          batches: [...prev.batches, { parallel: false, stepIds: [SYNTHESIS_NODE_ID] }],
        };
      });
    },
    [setConvState]
  );

  const resetGraph = useCallback(
    (convId: string) => {
      if (!convId) return;
      setConvState(convId, () => EMPTY_GRAPH_STATE);
    },
    [setConvState]
  );

  /** Record the ISO timestamp when execution begins (plan approved). */
  const setResearchStartTime = useCallback(
    (convId: string, isoTime: string) => {
      setConvState(convId, (prev) => ({ ...prev, startTime: isoTime, endTime: undefined }));
    },
    [setConvState]
  );

  /** Record the ISO timestamp when the research finishes or is stopped. */
  const setResearchEndTime = useCallback(
    (convId: string, isoTime: string) => {
      setConvState(convId, (prev) => ({ ...prev, endTime: isoTime }));
    },
    [setConvState]
  );

  /** Remove the graph entry for a deleted conversation. */
  const deleteGraph = useCallback((convId: string) => {
    setGraphs((prev) => {
      const { [convId]: _, ...rest } = prev;
      return rest;
    });
  }, []);

  /**
   * Initialise the graph with the five self-optimization workflow nodes.
   * Called when the user triggers the self-optimize action.
   */
  const initOptimizeGraph = useCallback(
    (convId: string) => {
      if (!convId) return;
      const phases: Array<{ id: number; name: string; description: string; phase: OptimizePhase }> = [
        { id: OPTIMIZE_READ_ID, name: 'Read Methods', description: 'Load RESEARCH-METHODS.md to use as baseline context.', phase: 'read_methods' },
        { id: OPTIMIZE_RETRIEVE_ID, name: 'Retrieve Memories', description: 'Fetch all stored memories from prior research sessions.', phase: 'retrieve_memories' },
        { id: OPTIMIZE_ANALYZE_ID, name: 'Analyse Patterns', description: 'Identify recurring patterns, failures, and improvement opportunities.', phase: 'analyze' },
        { id: OPTIMIZE_DEVELOP_ID, name: 'Develop Methods', description: 'Generate concrete new research method recommendations.', phase: 'develop' },
        { id: OPTIMIZE_UPDATE_ID, name: 'Update Methods', description: 'Rewrite RESEARCH-METHODS.md with improved strategies.', phase: 'update_methods' },
      ];
      const nodes: GraphNode[] = phases.map((p) => ({
        id: p.id,
        name: p.name,
        description: p.description,
        status: 'pending' as NodeStatus,
        parallelGroup: null,
        nodeType: 'optimize',
        optimizePhase: p.phase,
        toolsUsed: [],
        subAgents: [],
        qaRetries: 0,
        contradictions: [],
      }));
      const batches: GraphBatch[] = phases.map((p) => ({
        parallel: false,
        stepIds: [p.id],
      }));
      setConvState(convId, () => ({ nodes, batches, isReady: true }));
    },
    [setConvState]
  );

  /**
   * Update the status of one optimization phase node when the backend reports
   * progress via an ``optimize_progress`` WebSocket event.
   */
  const updateOptimizePhase = useCallback(
    (convId: string, phase: OptimizePhase, status: NodeStatus) => {
      const nodeId = OPTIMIZE_PHASE_MAP[phase];
      if (nodeId === undefined) return;
      setConvState(convId, (prev) => ({
        ...prev,
        nodes: prev.nodes.map((n) =>
          n.id === nodeId ? { ...n, status } : n
        ),
      }));
    },
    [setConvState]
  );

  return {
    getGraph,
    addQueryNode,
    initFromPlan,
    updatePlanAction,
    overrideBatches,
    setNodeRunning,
    setNodeCompleted,
    setNodeFailed,
    updateSubAgent,
    incrementQaRetries,
    addContradictions,
    addSynthesisNode,
    resetGraph,
    deleteGraph,
    setResearchStartTime,
    setResearchEndTime,
    initOptimizeGraph,
    updateOptimizePhase,
  };
}
