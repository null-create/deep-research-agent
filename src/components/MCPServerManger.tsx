import React, { useState, useEffect, useCallback } from 'react';
import { Plus, Server, CheckCircle, XCircle, Trash2, Lock, ChevronLeft, RefreshCw, Wrench, FileText, MessageSquare, Code } from 'lucide-react';
import { MCPServer } from '../types';
import { apiClient } from '../api/client';

type Transport = 'streamable-http' | 'sse' | 'stdio';

interface NewServerForm {
  name: string;
  transport: Transport;
  // HTTP transports
  url: string;
  headers: string; // JSON string — only for sse / streamable-http
  // stdio
  command: string;
  args: string;
  env: string;
}

interface MCPTool {
  name: string;
  description?: string;
  parameters?: Record<string, any>;
}

interface MCPResource {
  uri: string;
  name?: string;
  description?: string;
  mimeType?: string;
}

interface MCPResourceTemplate {
  uriTemplate: string;
  name?: string;
  description?: string;
  mimeType?: string;
}

interface MCPPrompt {
  name: string;
  description?: string;
  arguments?: Array<{ name: string; description?: string; required?: boolean }>;
}

interface ServerDetail {
  server: MCPServer;
  tools: MCPTool[];
  resources: MCPResource[];
  resource_templates: MCPResourceTemplate[];
  prompts: MCPPrompt[];
}

const EMPTY_FORM: NewServerForm = {
  name: '',
  transport: 'streamable-http',
  url: '',
  headers: '',
  command: '',
  args: '',
  env: '',
};

/** Return true if a header key looks like it carries a secret. */
function isSensitiveHeader(key: string): boolean {
  const lower = key.toLowerCase();
  return (
    lower.includes('auth') ||
    lower.includes('token') ||
    lower.includes('key') ||
    lower.includes('secret') ||
    lower.includes('password') ||
    lower.includes('credential')
  );
}

function maskValue(value: string): string {
  if (value.length <= 8) return '••••••••';
  return value.slice(0, 4) + '••••••••' + value.slice(-4);
}

const TRANSPORT_LABELS: Record<Transport, string> = {
  'streamable-http': 'Streamable HTTP',
  sse: 'SSE',
  stdio: 'stdio',
};

function ServerStatus({ status }: { status: MCPServer['status'] }) {
  if (status === 'connected')
    return <span title="Connected"><CheckCircle className="w-4 h-4 text-green-500 flex-shrink-0" /></span>;
  if (status === 'error')
    return <span title="Error"><XCircle className="w-4 h-4 text-red-500 flex-shrink-0" /></span>;
  return <span title="Disconnected"><XCircle className="w-4 h-4 text-gray-400 flex-shrink-0" /></span>;
}

type DetailTab = 'tools' | 'resources' | 'prompts';

function ServerDetailPanel({
  detail,
  onBack,
  onRefresh,
  loading,
}: {
  detail: ServerDetail;
  onBack: () => void;
  onRefresh: () => void;
  loading: boolean;
}) {
  const [tab, setTab] = useState<DetailTab>('tools');
  const { server, tools, resources, resource_templates, prompts } = detail;

  const allResources = [
    ...resources.map((r) => ({ ...r, isTemplate: false })),
    ...resource_templates.map((r) => ({ uri: r.uriTemplate, name: r.name, description: r.description, mimeType: r.mimeType, isTemplate: true })),
  ];

  const tabCount: Record<DetailTab, number> = {
    tools: tools.length,
    resources: allResources.length,
    prompts: prompts.length,
  };

  const tabClass = (t: DetailTab) =>
    `px-3 py-1.5 text-xs font-medium rounded transition-colors flex items-center gap-1 ${tab === t
      ? 'bg-blue-500 dark:bg-emerald-600 text-white'
      : 'text-gray-600 dark:text-gray-400 hover:bg-gray-200 dark:hover:bg-gray-700'
    }`;

  const itemBase =
    'p-3 rounded-lg bg-gray-50 dark:bg-gray-800 border border-gray-100 dark:border-gray-700';

  return (
    <div className="flex flex-col h-full min-h-0">
      {/* Header */}
      <div className="flex items-center gap-2 mb-3">
        <button
          onClick={onBack}
          className="p-1 rounded hover:bg-gray-200 dark:hover:bg-gray-700 transition-colors"
          title="Back to server list"
        >
          <ChevronLeft className="w-4 h-4 text-gray-600 dark:text-gray-300" />
        </button>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-1.5">
            <Server className="w-4 h-4 text-gray-400 flex-shrink-0" />
            <span className="text-sm font-semibold text-gray-900 dark:text-gray-100 truncate">
              {server.name}
            </span>
            {server.builtin && (
              <span title="Built-in"><Lock className="w-3 h-3 text-gray-400 flex-shrink-0" /></span>
            )}
            <span className="text-xs text-gray-400 dark:text-gray-500 bg-gray-100 dark:bg-gray-700 px-1.5 py-0.5 rounded flex-shrink-0">
              {TRANSPORT_LABELS[server.transport as Transport] ?? server.transport}
            </span>
          </div>
          <p className="text-xs text-gray-500 dark:text-gray-400 truncate mt-0.5">
            {server.transport === 'stdio'
              ? [server.command, ...(server.args || [])].filter(Boolean).join(' ')
              : server.url}
          </p>
          {server.headers && Object.keys(server.headers).length > 0 && (
            <div className="flex flex-wrap gap-1 mt-1">
              {Object.entries(server.headers).map(([k, v]) => (
                <span
                  key={k}
                  className="inline-flex items-center gap-1 text-xs font-mono bg-yellow-50 dark:bg-yellow-900/20 text-yellow-700 dark:text-yellow-300 px-1.5 py-0.5 rounded"
                  title={`${k}: ${v}`}
                >
                  <span>{k}:</span>
                  <span>{isSensitiveHeader(k) ? maskValue(v) : v}</span>
                </span>
              ))}
            </div>
          )}
        </div>
        <div className="flex items-center gap-1.5 flex-shrink-0">
          <ServerStatus status={server.status} />
          <button
            onClick={onRefresh}
            disabled={loading}
            className="p-1 rounded hover:bg-gray-200 dark:hover:bg-gray-700 transition-colors"
            title="Refresh"
          >
            <RefreshCw className={`w-3.5 h-3.5 text-gray-500 ${loading ? 'animate-spin' : ''}`} />
          </button>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 mb-3">
        <button className={tabClass('tools')} onClick={() => setTab('tools')}>
          <Wrench className="w-3 h-3" />
          Tools {tabCount.tools > 0 && <span className="opacity-75">({tabCount.tools})</span>}
        </button>
        <button className={tabClass('resources')} onClick={() => setTab('resources')}>
          <FileText className="w-3 h-3" />
          Resources {tabCount.resources > 0 && <span className="opacity-75">({tabCount.resources})</span>}
        </button>
        <button className={tabClass('prompts')} onClick={() => setTab('prompts')}>
          <MessageSquare className="w-3 h-3" />
          Prompts {tabCount.prompts > 0 && <span className="opacity-75">({tabCount.prompts})</span>}
        </button>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto space-y-2 min-h-0">
        {tab === 'tools' && (
          <>
            {tools.length === 0 ? (
              <p className="text-xs text-gray-500 dark:text-gray-400 text-center py-6">No tools available</p>
            ) : (
              tools.map((tool) => (
                <div key={tool.name} className={itemBase}>
                  <div className="flex items-start gap-2">
                    <Code className="w-3.5 h-3.5 text-blue-500 dark:text-emerald-400 flex-shrink-0 mt-0.5" />
                    <div className="min-w-0">
                      <p className="text-xs font-mono font-semibold text-gray-900 dark:text-gray-100">
                        {tool.name}
                      </p>
                      {tool.description && (
                        <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5 leading-relaxed">
                          {tool.description}
                        </p>
                      )}
                      {tool.parameters?.properties && Object.keys(tool.parameters.properties).length > 0 && (
                        <div className="mt-1.5 flex flex-wrap gap-1">
                          {Object.entries(tool.parameters.properties as Record<string, any>).map(([param, schema]) => {
                            const required = (tool.parameters?.required as string[] | undefined)?.includes(param);
                            return (
                              <span
                                key={param}
                                title={schema?.description || ''}
                                className={`text-xs px-1.5 py-0.5 rounded font-mono ${required
                                  ? 'bg-blue-50 dark:bg-blue-900/30 text-blue-700 dark:text-blue-300 border border-blue-200 dark:border-blue-700'
                                  : 'bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-400'
                                  }`}
                              >
                                {param}
                                {schema?.type ? `: ${schema.type}` : ''}
                                {!required && '?'}
                              </span>
                            );
                          })}
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              ))
            )}
          </>
        )}

        {tab === 'resources' && (
          <>
            {allResources.length === 0 ? (
              <p className="text-xs text-gray-500 dark:text-gray-400 text-center py-6">No resources available</p>
            ) : (
              allResources.map((res, i) => (
                <div key={`${res.uri}-${i}`} className={itemBase}>
                  <div className="flex items-start gap-2">
                    <FileText className="w-3.5 h-3.5 text-purple-500 dark:text-purple-400 flex-shrink-0 mt-0.5" />
                    <div className="min-w-0">
                      <div className="flex items-center gap-1.5 flex-wrap">
                        <p className="text-xs font-mono font-semibold text-gray-900 dark:text-gray-100 break-all">
                          {res.uri}
                        </p>
                        {res.isTemplate && (
                          <span className="text-xs bg-yellow-100 dark:bg-yellow-900/30 text-yellow-700 dark:text-yellow-300 px-1.5 py-0.5 rounded border border-yellow-200 dark:border-yellow-700">
                            template
                          </span>
                        )}
                        {res.mimeType && (
                          <span className="text-xs text-gray-400 dark:text-gray-500">{res.mimeType}</span>
                        )}
                      </div>
                      {res.name && res.name !== res.uri && (
                        <p className="text-xs font-medium text-gray-700 dark:text-gray-300 mt-0.5">{res.name}</p>
                      )}
                      {res.description && (
                        <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5 leading-relaxed">
                          {res.description}
                        </p>
                      )}
                    </div>
                  </div>
                </div>
              ))
            )}
          </>
        )}

        {tab === 'prompts' && (
          <>
            {prompts.length === 0 ? (
              <p className="text-xs text-gray-500 dark:text-gray-400 text-center py-6">No prompts available</p>
            ) : (
              prompts.map((prompt) => (
                <div key={prompt.name} className={itemBase}>
                  <div className="flex items-start gap-2">
                    <MessageSquare className="w-3.5 h-3.5 text-green-500 dark:text-green-400 flex-shrink-0 mt-0.5" />
                    <div className="min-w-0">
                      <p className="text-xs font-mono font-semibold text-gray-900 dark:text-gray-100">
                        {prompt.name}
                      </p>
                      {prompt.description && (
                        <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5 leading-relaxed">
                          {prompt.description}
                        </p>
                      )}
                      {prompt.arguments && prompt.arguments.length > 0 && (
                        <div className="mt-1.5 flex flex-wrap gap-1">
                          {prompt.arguments.map((arg) => (
                            <span
                              key={arg.name}
                              title={arg.description || ''}
                              className={`text-xs px-1.5 py-0.5 rounded font-mono ${arg.required
                                ? 'bg-green-50 dark:bg-green-900/30 text-green-700 dark:text-green-300 border border-green-200 dark:border-green-700'
                                : 'bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-400'
                                }`}
                            >
                              {arg.name}{!arg.required && '?'}
                            </span>
                          ))}
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              ))
            )}
          </>
        )}
      </div>
    </div>
  );
}

export const MCPServerManager: React.FC = () => {
  const [servers, setServers] = useState<MCPServer[]>([]);
  const [showAddForm, setShowAddForm] = useState(false);
  const [form, setForm] = useState<NewServerForm>(EMPTY_FORM);
  const [error, setError] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [selectedDetail, setSelectedDetail] = useState<ServerDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  useEffect(() => {
    loadServers();
  }, []);

  const loadServers = async () => {
    setLoadError(null);
    try {
      const data = await apiClient.listMCPServers();
      setServers(data.servers || []);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.error('Failed to load MCP servers:', err);
      setLoadError(`Failed to load servers: ${msg}`);
    }
  };

  const handleAddServer = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);

    try {
      const payload: Parameters<typeof apiClient.registerMCPServer>[0] = {
        name: form.name.trim(),
        transport: form.transport,
      };

      if (form.transport === 'stdio') {
        payload.command = form.command.trim();
        payload.args = form.args.split(',').map((s) => s.trim()).filter(Boolean);
        if (form.env.trim()) {
          try {
            payload.env = JSON.parse(form.env);
          } catch {
            setError('Environment variables must be valid JSON (e.g. {"KEY": "value"})');
            return;
          }
        }
      } else {
        payload.url = form.url.trim();
        if (form.headers.trim()) {
          try {
            payload.headers = JSON.parse(form.headers);
          } catch {
            setError('Headers must be valid JSON (e.g. {"Authorization": "Bearer token"})');
            return;
          }
        }
      }

      await apiClient.registerMCPServer(payload);
      setForm(EMPTY_FORM);
      setShowAddForm(false);
      await loadServers();
    } catch (err) {
      console.error('Failed to register server:', err);
      setError('Failed to register server. Check console for details.');
    }
  };

  const handleDelete = async (name: string) => {
    try {
      await apiClient.deleteMCPServer(name);
      await loadServers();
    } catch (err) {
      console.error('Failed to delete server:', err);
    }
  };

  const loadDetail = useCallback(async (name: string) => {
    setDetailLoading(true);
    try {
      const data = await apiClient.getMCPServerDetail(name);
      setSelectedDetail(data);
    } catch (err) {
      console.error('Failed to load server detail:', err);
    } finally {
      setDetailLoading(false);
    }
  }, []);

  const inputClass =
    'w-full px-3 py-2 rounded border border-gray-300 dark:border-gray-600 ' +
    'bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100 ' +
    'focus:outline-none focus:ring-2 focus:ring-blue-500 dark:focus:ring-emerald-500 text-sm';

  const serverSubtitle = (s: MCPServer) => {
    if (s.transport === 'stdio') {
      const cmd = [s.command, ...(s.args || [])].filter(Boolean).join(' ');
      return cmd || 'stdio';
    }
    return s.url || s.transport;
  };

  // ── Detail view ──────────────────────────────────────────────────────────────
  if (selectedDetail) {
    return (
      <ServerDetailPanel
        detail={selectedDetail}
        loading={detailLoading}
        onBack={() => setSelectedDetail(null)}
        onRefresh={() => loadDetail(selectedDetail.server.name)}
      />
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex justify-between items-center">
        <h3 className="font-semibold text-lg text-gray-900 dark:text-gray-100">MCP Servers</h3>
        <button
          onClick={() => { setShowAddForm(!showAddForm); setError(null); }}
          className="flex items-center gap-1.5 px-3 py-1.5 bg-blue-500 dark:bg-emerald-600
                     text-white rounded-lg hover:bg-blue-600 dark:hover:bg-emerald-700
                     transition-colors text-sm"
        >
          <Plus className="w-4 h-4" />
          Add Server
        </button>
      </div>

      {showAddForm && (
        <form
          onSubmit={handleAddServer}
          className="p-4 bg-gray-50 dark:bg-gray-800 rounded-lg space-y-3 border border-gray-200 dark:border-gray-700"
        >
          <input
            type="text"
            placeholder="Server name (e.g., github)"
            value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
            required
            className={inputClass}
          />

          <div>
            <label className="block text-xs font-medium text-gray-500 dark:text-gray-400 mb-1">
              Transport
            </label>
            <div className="flex gap-2">
              {(['streamable-http', 'sse', 'stdio'] as Transport[]).map((t) => (
                <button
                  key={t}
                  type="button"
                  onClick={() => setForm({ ...form, transport: t })}
                  className={`px-3 py-1.5 rounded text-sm font-medium transition-colors ${form.transport === t
                    ? 'bg-blue-500 dark:bg-emerald-600 text-white'
                    : 'bg-gray-200 dark:bg-gray-700 text-gray-700 dark:text-gray-300 hover:bg-gray-300 dark:hover:bg-gray-600'
                    }`}
                >
                  {TRANSPORT_LABELS[t]}
                </button>
              ))}
            </div>
          </div>

          {form.transport !== 'stdio' ? (
            <>
              <input
                type="url"
                placeholder={
                  form.transport === 'sse'
                    ? 'SSE URL (e.g., https://example.com/sse)'
                    : 'Server URL (e.g., http://localhost:9393/mcp)'
                }
                value={form.url}
                onChange={(e) => setForm({ ...form, url: e.target.value })}
                required
                className={inputClass}
              />
              <input
                type="text"
                placeholder='Auth headers (JSON, e.g., {"Authorization": "Bearer token"})'
                value={form.headers}
                onChange={(e) => setForm({ ...form, headers: e.target.value })}
                className={inputClass}
              />
            </>
          ) : (
            <>
              <input
                type="text"
                placeholder="Command (e.g., npx)"
                value={form.command}
                onChange={(e) => setForm({ ...form, command: e.target.value })}
                required
                className={inputClass}
              />
              <input
                type="text"
                placeholder="Args (comma-separated, e.g., -y, @modelcontextprotocol/server-github)"
                value={form.args}
                onChange={(e) => setForm({ ...form, args: e.target.value })}
                className={inputClass}
              />
              <input
                type="text"
                placeholder='Env vars (JSON, e.g., {"GITHUB_PERSONAL_ACCESS_TOKEN": "..."})'
                value={form.env}
                onChange={(e) => setForm({ ...form, env: e.target.value })}
                className={inputClass}
              />
            </>
          )}

          {error && (
            <p className="text-sm text-red-500 dark:text-red-400">{error}</p>
          )}

          <div className="flex gap-2">
            <button
              type="submit"
              className="px-4 py-2 bg-blue-500 dark:bg-emerald-600 text-white rounded-lg
                         hover:bg-blue-600 dark:hover:bg-emerald-700 transition-colors text-sm"
            >
              Register
            </button>
            <button
              type="button"
              onClick={() => { setShowAddForm(false); setError(null); setForm(EMPTY_FORM); }}
              className="px-4 py-2 bg-gray-300 dark:bg-gray-600 text-gray-700 dark:text-gray-200
                         rounded-lg hover:bg-gray-400 dark:hover:bg-gray-500 transition-colors text-sm"
            >
              Cancel
            </button>
          </div>
        </form>
      )}

      {loadError && (
        <div className="p-3 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg">
          <p className="text-sm text-red-600 dark:text-red-400">{loadError}</p>
          <button
            onClick={loadServers}
            className="mt-2 text-xs text-red-600 dark:text-red-400 underline hover:no-underline"
          >
            Retry
          </button>
        </div>
      )}

      <div className="space-y-2">
        {servers.map((server) => (
          <div
            key={server.name}
            onClick={() => loadDetail(server.name)}
            className="flex items-start gap-3 p-3 bg-gray-50 dark:bg-gray-800 rounded-lg
                       cursor-pointer hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors"
          >
            <Server className="w-4 h-4 text-gray-400 flex-shrink-0 mt-0.5" />
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-1.5">
                <p className="text-sm font-medium text-gray-900 dark:text-gray-100 truncate">
                  {server.name}
                </p>
                {server.builtin && (
                  <span title="Built-in server"><Lock className="w-3 h-3 text-gray-400 flex-shrink-0" /></span>
                )}
                <span className="text-xs text-gray-400 dark:text-gray-500 bg-gray-100 dark:bg-gray-700 px-1.5 py-0.5 rounded flex-shrink-0">
                  {TRANSPORT_LABELS[server.transport] ?? server.transport}
                </span>
              </div>
              <p className="text-xs text-gray-500 dark:text-gray-400 truncate mt-0.5">
                {serverSubtitle(server)}
              </p>
              {server.tools_count !== undefined && server.tools_count > 0 && (
                <p className="text-xs text-gray-400 dark:text-gray-500">
                  {server.tools_count} tool{server.tools_count !== 1 ? 's' : ''}
                </p>
              )}
            </div>
            <div className="flex items-center gap-2 flex-shrink-0">
              <ServerStatus status={server.status} />
              {!server.builtin && (
                <button
                  onClick={(e) => { e.stopPropagation(); handleDelete(server.name); }}
                  className="p-1 text-gray-400 hover:text-red-500 transition-colors rounded"
                  title="Remove server"
                >
                  <Trash2 className="w-4 h-4" />
                </button>
              )}
            </div>
          </div>
        ))}
        {servers.length === 0 && (
          <p className="text-sm text-gray-500 dark:text-gray-400 text-center py-4">
            No MCP servers registered
          </p>
        )}
      </div>
    </div>
  );
};
