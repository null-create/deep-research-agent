export interface ResearchPlan {
  id: string;
  goal: string;
  summary: string;
  estimatedTime?: string;
  steps: ResearchStep[];
}

export interface ResearchStep {
  id: number;
  description: string;
  status: 'pending' | 'in_progress' | 'completed' | 'failed';
  result?: string;
  error?: string;
}

export interface MCPServer {
  name: string;
  transport: 'stdio' | 'sse' | 'streamable-http';
  // HTTP-based transports (sse, streamable-http)
  url?: string;
  headers?: Record<string, string>;
  // stdio transport
  command?: string;
  args?: string[];
  env?: Record<string, string>;
  // Runtime fields returned by the API
  status: 'connected' | 'disconnected' | 'error';
  tools_count?: number;
  builtin?: boolean;
}

export interface UploadedFile {
  id: string;
  name: string;
  size: number;
  type: string;
  uploadedAt: Date;
  status: 'uploading' | 'ready' | 'error';
}

export interface WebSocketMessage {
  type: string;
  data?: any;
  message?: string;
}