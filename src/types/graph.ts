export type NodeStatus = 'pending' | 'running' | 'completed' | 'failed';
export type SubAgentRole = 'search' | 'analyst' | 'qa';
export type SubAgentStatus = 'pending' | 'running' | 'completed' | 'failed';
export type PlanAction = 'pending' | 'approved' | 'modified' | 'denied';

/** Phase identifiers emitted by the backend self-optimize workflow. */
export type OptimizePhase =
  | 'read_methods'
  | 'retrieve_memories'
  | 'analyze'
  | 'develop'
  | 'update_methods';

export interface SubAgentInfo {
  role: SubAgentRole;
  status: SubAgentStatus;
}

export interface GraphNode {
  id: number;
  name: string;
  description: string;
  status: NodeStatus;
  parallelGroup: string | null;
  /**
   * 'query'    — initial user query (first node)
   * 'plan'     — generated plan awaiting approval
   * 'research' — an individual research step
   * 'synthesis' — final ReportComposer node
   * 'optimize' — a self-optimization workflow phase node
   */
  nodeType?: 'research' | 'synthesis' | 'query' | 'plan' | 'optimize';
  /** Only set for nodeType === 'plan'. Reflects the user's decision. */
  planAction?: PlanAction;
  /** Only set for nodeType === 'optimize'. Which phase this node represents. */
  optimizePhase?: OptimizePhase;
  result?: string;
  error?: string;
  toolsUsed: string[];
  subAgents: SubAgentInfo[];
  qaRetries: number;
  contradictions: any[];
}

export interface GraphBatch {
  parallel: boolean;
  stepIds: number[];
}

export interface GraphState {
  nodes: GraphNode[];
  batches: GraphBatch[];
  isReady: boolean;
  /** ISO string of when the research execution started (plan approved). */
  startTime?: string;
  /** ISO string of when the research finished (report received or stopped). */
  endTime?: string;
}