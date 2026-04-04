const API_BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:9999';

export const apiClient = {
  async startResearch(query: string) {
    const response = await fetch(`${API_BASE_URL}/research`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query }),
    });
    if (!response.ok) throw new Error('Failed to start research');
    return response.json();
  },

  async listMCPServers() {
    const response = await fetch(`${API_BASE_URL}/mcp/servers`);
    if (!response.ok) throw new Error('Failed to list MCP servers');
    return response.json();
  },

  async registerMCPServer(server: {
    name: string;
    transport: 'stdio' | 'sse' | 'streamable-http';
    url?: string;
    command?: string;
    args?: string[];
    env?: Record<string, string>;
    headers?: Record<string, string>;
  }) {
    const response = await fetch(`${API_BASE_URL}/mcp/servers`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(server),
    });
    if (!response.ok) throw new Error('Failed to register MCP server');
    return response.json();
  },

  async deleteMCPServer(name: string) {
    const response = await fetch(`${API_BASE_URL}/mcp/servers/${encodeURIComponent(name)}`, {
      method: 'DELETE',
    });
    if (!response.ok) throw new Error('Failed to delete MCP server');
    return response.json();
  },

  async getMCPServerDetail(name: string) {
    const response = await fetch(`${API_BASE_URL}/mcp/servers/${encodeURIComponent(name)}`);
    if (!response.ok) throw new Error('Failed to get MCP server detail');
    return response.json();
  },

  async uploadFile(file: File) {
    const formData = new FormData();
    formData.append('file', file);

    const response = await fetch('/files/upload', {
      method: 'POST',
      body: formData,
    });
    if (!response.ok) throw new Error('Failed to upload file');
    return response.json();
  },

  async listFiles() {
    const response = await fetch('/files');
    if (!response.ok) throw new Error('Failed to list files');
    return response.json();
  },

  async deleteFile(fileName: string) {
    const response = await fetch(`/files/${encodeURIComponent(fileName)}`, {
      method: 'DELETE',
    });
    if (!response.ok) throw new Error('Failed to delete file');
    return response.json();
  },

  async getResearchMethods(): Promise<string> {
    const response = await fetch(`${API_BASE_URL}/agent/research-methods`);
    if (!response.ok) throw new Error('Failed to fetch research methods');
    const data = await response.json();
    return data.content as string;
  },

  async listDocs(): Promise<string[]> {
    const response = await fetch(`${API_BASE_URL}/project-docs`);
    if (!response.ok) throw new Error('Failed to list docs');
    const data = await response.json();
    return data.docs as string[];
  },

  async getDoc(filename: string): Promise<string> {
    const response = await fetch(`${API_BASE_URL}/project-docs/${encodeURIComponent(filename)}`);
    if (!response.ok) throw new Error(`Failed to fetch doc: ${filename}`);
    const data = await response.json();
    return data.content as string;
  },

  async streamChat(
    content: string,
    onChunk: (chunk: string) => void,
    onDone: () => void,
    onError: (err: string) => void
  ): Promise<void> {
    let response: Response;
    try {
      response = await fetch(`${API_BASE_URL}/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ role: 'user', content }),
      });
    } catch (e: any) {
      onError(e.message || 'Network error');
      return;
    }

    if (!response.ok) {
      onError(`Request failed: ${response.status}`);
      return;
    }

    const reader = response.body?.getReader();
    if (!reader) {
      onError('No response body');
      return;
    }

    const decoder = new TextDecoder();
    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        onChunk(decoder.decode(value, { stream: true }));
      }
      onDone();
    } catch (e: any) {
      onError(e.message || 'Stream read error');
    } finally {
      reader.releaseLock();
    }
  },

  async getModelConfig(): Promise<Record<string, unknown>> {
    const response = await fetch(`${API_BASE_URL}/config`);
    if (!response.ok) throw new Error('Failed to fetch config');
    const data = await response.json();
    return data.config as Record<string, unknown>;
  },

  async updateModelConfig(update: Record<string, unknown>): Promise<Record<string, unknown>> {
    const response = await fetch(`${API_BASE_URL}/config`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(update),
    });
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      throw new Error((body as any).detail || `Config update failed: ${response.status}`);
    }
    const data = await response.json();
    return data.config as Record<string, unknown>;
  },
};