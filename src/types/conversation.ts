export interface Conversation {
  id: string;
  title: string;
  createdAt: Date;
  updatedAt: Date;
  messages: Message[];
  /** Monotonically increasing counter of WebSocket events received for the
   *  backend session associated with this conversation.  Used on reconnect to
   *  determine which replay events are already in localStorage and which are
   *  new (i.e. were emitted while the browser was closed). */
  _eventIndex?: number;
}

export type PlanAction = 'pending' | 'approved' | 'modified' | 'denied' | 'ended';

// Sent over WebSocket to control an in-progress research session
export type ResearchControlAction = 'pause' | 'resume' | 'stop';

export interface SynthesisData {
  title?: string;
  summary: string;
  key_insights?: string[];
  key_findings?: string;
  patterns?: string[];
  recommendations?: string[];
  creative_applications?: string[];
  knowledge_gaps?: string[];
  sources?: Array<{ title: string; url: string }>;
  content?: string;
  generatedAt?: string;
}

export interface PlanData {
  id: string;
  steps: {
    id: number;
    name: string;
    description: string;
    status: string;
    result?: string;
    error?: string;
    /** Shared label used by the Orchestrator to group steps that run in parallel. */
    parallel_group?: string | null;
  }[];
  goal: string;
  estimatedTime?: string;
}

export interface Message {
  id: string;
  role: 'user' | 'assistant' | 'system';
  type:
  | 'user'
  | 'system'
  | 'plan'
  | 'plan_approval'
  | 'step_start'
  | 'step_complete'
  | 'step_failed'
  | 'step_update'
  | 'research_step'
  | 'synthesis'
  | 'research_complete'
  | 'report'
  | 'research_paused'
  | 'research_resumed'
  | 'research_stopped'
  | 'session_resumed'
  | 'chat_response'
  | 'error';
  content: string;
  timestamp: Date;
  data?: {
    plan?: PlanData;
    planAction?: PlanAction;
    step?: {
      name?: string;
      description?: string;
      result?: string;
      error?: string;
    };
    synthesis?: SynthesisData;
    [key: string]: any;
  };
}