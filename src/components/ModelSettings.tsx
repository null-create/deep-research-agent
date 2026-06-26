import React, { useCallback, useEffect, useState } from 'react';
import { ChevronDown, ChevronRight, Eye, EyeOff, Loader2, Save, X } from 'lucide-react';
import { apiClient } from '../api/client';
import { AgentParams, Backend, FormState } from '../types/models';


// ── Constants ─────────────────────────────────────────────────────────────────

const BACKENDS: { value: Backend; label: string }[] = [
  { value: 'ollama', label: 'Ollama (local)' },
  { value: 'openai', label: 'OpenAI' },
  { value: 'azure', label: 'Azure OpenAI' },
  { value: 'aws', label: 'AWS (OpenAI-compatible gateway)' },
  { value: 'bedrock', label: 'AWS Bedrock (boto3 native)' },
  { value: 'anthropic', label: 'Anthropic' },
  { value: 'gcp', label: 'GCP Vertex AI' },
  { value: 'huggingface', label: 'HuggingFace' },
];

const BACKEND_URL_PLACEHOLDERS: Record<Backend, string> = {
  ollama: 'http://localhost:11434',
  openai: 'https://api.openai.com/v1',
  azure: 'https://<resource>.openai.azure.com',
  aws: 'https://bedrock-runtime.<region>.amazonaws.com',
  bedrock: 'us-east-1',
  anthropic: 'https://api.anthropic.com (optional)',
  gcp: 'https://us-central1-aiplatform.googleapis.com/v1',
  huggingface: 'http://localhost:8080',
};

const BACKEND_URL_LABELS: Record<Backend, string> = {
  ollama: 'Base URL',
  openai: 'API Base URL',
  azure: 'Azure Endpoint',
  aws: 'Gateway Base URL (optional)',
  bedrock: 'AWS Region',
  anthropic: 'Base URL (optional)',
  gcp: 'Vertex AI Endpoint',
  huggingface: 'Base URL',
};

// bedrock uses the boto3 credential chain (env vars / ~/.aws / IAM role) — no key field needed
const BACKENDS_WITHOUT_KEY: Backend[] = ['ollama', 'bedrock'];

// ── Helpers ───────────────────────────────────────────────────────────────────

function extractFormState(cfg: Record<string, unknown>, backend: Backend): FormState {
  const baseModelKey = `${backend}_heavy_model` as string;
  const lightModelKey = `${backend}_light_model` as string;
  const urlKey =
    backend === 'azure' ? 'azure_endpoint' :
      backend === 'bedrock' ? 'aws_region' :
        `${backend}_base_url`;
  const keyKey =
    backend === 'openai' ? 'openai_api_key' :
      backend === 'azure' ? 'azure_api_key' :
        backend === 'aws' ? 'aws_api_key' :
          backend === 'gcp' ? 'gcp_api_key' :
            backend === 'huggingface' ? 'huggingface_api_key' :
              backend === 'anthropic' ? 'anthropic_api_key' :
                null; // bedrock: no key — uses boto3 credential chain

  const heavyModel = (cfg[baseModelKey] as string) || '';
  const lightModel = (cfg[lightModelKey] as string) || '';

  const get = (key: string, fallback: unknown) =>
    cfg[key] !== undefined && cfg[key] !== null ? cfg[key] : fallback;

  return {
    backend,
    api_key: keyKey ? ((cfg[keyKey] as string) || '') : '',
    api_base_url: (cfg[urlKey] as string) || '',
    root: {
      model: (cfg['root_model_override'] as string) || heavyModel,
      temperature: get('root_temperature', 0.7) as number,
      top_p: get('root_top_p', 0.9) as number,
      max_tokens: get('root_max_tokens', 4096) as number,
    },
    search: {
      model: (cfg['search_model_override'] as string) || lightModel,
      temperature: get('search_temperature', 0.7) as number,
      top_p: get('search_top_p', 0.9) as number,
      max_tokens: get('search_max_tokens', 4096) as number,
    },
    analyst: {
      model: (cfg['analyst_model_override'] as string) || lightModel,
      temperature: get('analyst_temperature', 0.7) as number,
      top_p: get('analyst_top_p', 0.9) as number,
      max_tokens: get('analyst_max_tokens', 4096) as number,
    },
    qa: {
      model: (cfg['qa_model_override'] as string) || heavyModel,
      temperature: get('qa_temperature', 0.7) as number,
      top_p: get('qa_top_p', 0.9) as number,
      max_tokens: get('qa_max_tokens', 4096) as number,
    },
  };
}

function buildPayload(form: FormState, original: FormState): Record<string, unknown> {
  const payload: Record<string, unknown> = {
    model_backend: form.backend,
  };
  if (form.api_key !== original.api_key) payload.api_key = form.api_key;
  if (form.api_base_url !== original.api_base_url) payload.api_base_url = form.api_base_url;

  // Send heavy/light model based on which defaults the UI showed
  const origHeavy = original.root.model;
  const origLight = original.search.model;

  if (form.root.model !== origHeavy) payload.heavy_model = form.root.model;
  if (form.search.model !== origLight) payload.light_model = form.search.model;

  // Per-agent overrides: always emit them so each agent's model is saved
  // independently of whether root and search happen to be equal.
  payload.root_model_override = form.root.model;
  payload.qa_model_override = form.qa.model;
  payload.search_model_override = form.search.model;
  payload.analyst_model_override = form.analyst.model;

  payload.root_temperature = form.root.temperature;
  payload.search_temperature = form.search.temperature;
  payload.analyst_temperature = form.analyst.temperature;
  payload.qa_temperature = form.qa.temperature;
  payload.root_top_p = form.root.top_p;
  payload.search_top_p = form.search.top_p;
  payload.analyst_top_p = form.analyst.top_p;
  payload.qa_top_p = form.qa.top_p;
  payload.root_max_tokens = form.root.max_tokens;
  payload.search_max_tokens = form.search.max_tokens;
  payload.analyst_max_tokens = form.analyst.max_tokens;
  payload.qa_max_tokens = form.qa.max_tokens;

  return payload;
}

// ── Sub-components ────────────────────────────────────────────────────────────

interface SectionProps {
  title: string;
  subtitle?: string;
  children: React.ReactNode;
  defaultOpen?: boolean;
}

const CollapsibleSection: React.FC<SectionProps> = ({ title, subtitle, children, defaultOpen = true }) => {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="rounded-lg border border-gray-200 dark:border-gray-700 overflow-hidden">
      <button
        type="button"
        onClick={() => setOpen(o => !o)}
        className="w-full flex items-center justify-between px-3 py-2.5 bg-gray-50 dark:bg-gray-800
                   hover:bg-gray-100 dark:hover:bg-gray-750 transition-colors text-left"
      >
        <div>
          <span className="text-xs font-semibold text-gray-700 dark:text-gray-200">{title}</span>
          {subtitle && (
            <span className="ml-2 text-xs text-gray-400 dark:text-gray-500">{subtitle}</span>
          )}
        </div>
        {open
          ? <ChevronDown className="w-3.5 h-3.5 text-gray-400 flex-shrink-0" />
          : <ChevronRight className="w-3.5 h-3.5 text-gray-400 flex-shrink-0" />}
      </button>
      {open && (
        <div className="px-3 py-3 bg-white dark:bg-gray-900 space-y-2.5">
          {children}
        </div>
      )}
    </div>
  );
};

interface FieldProps {
  label: string;
  hint?: string;
  children: React.ReactNode;
}

const Field: React.FC<FieldProps> = ({ label, hint, children }) => (
  <div>
    <label className="block text-xs text-gray-600 dark:text-gray-400 mb-1">
      {label}
      {hint && <span className="ml-1 text-gray-400 dark:text-gray-500">({hint})</span>}
    </label>
    {children}
  </div>
);

const inputCls =
  'w-full px-2 py-1.5 text-xs rounded-md border border-gray-200 dark:border-gray-600 ' +
  'bg-white dark:bg-gray-800 text-gray-800 dark:text-gray-100 ' +
  'focus:outline-none focus:ring-1 focus:ring-blue-500 dark:focus:ring-blue-400 ' +
  'placeholder-gray-400 dark:placeholder-gray-500';

interface NumberFieldProps {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  hint?: string;
  onChange: (v: number) => void;
}

const NumberField: React.FC<NumberFieldProps> = ({ label, value, min, max, step, hint, onChange }) => (
  <Field label={label} hint={hint}>
    <div className="flex items-center gap-2">
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={e => onChange(parseFloat(e.target.value))}
        className="flex-1 accent-blue-600"
      />
      <input
        type="number"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={e => {
          const v = parseFloat(e.target.value);
          if (!isNaN(v)) onChange(Math.min(max, Math.max(min, v)));
        }}
        className="w-20 px-2 py-1 text-xs rounded-md border border-gray-200 dark:border-gray-600
                   bg-white dark:bg-gray-800 text-gray-800 dark:text-gray-100
                   focus:outline-none focus:ring-1 focus:ring-blue-500"
      />
    </div>
  </Field>
);

interface AgentSectionProps {
  title: string;
  subtitle: string;
  params: AgentParams;
  onChange: (p: AgentParams) => void;
  defaultOpen?: boolean;
}

const AgentSection: React.FC<AgentSectionProps> = ({ title, subtitle, params, onChange, defaultOpen }) => {
  const set = (key: keyof AgentParams, value: AgentParams[keyof AgentParams]) =>
    onChange({ ...params, [key]: value });

  return (
    <CollapsibleSection title={title} subtitle={subtitle} defaultOpen={defaultOpen}>
      <Field label="Model ID" hint="leave blank to use backend default">
        <input
          type="text"
          value={params.model}
          onChange={e => set('model', e.target.value)}
          placeholder="e.g. gpt-4o-mini"
          className={inputCls}
        />
      </Field>
      <NumberField
        label="Temperature"
        value={params.temperature}
        min={0} max={2} step={0.05}
        onChange={v => set('temperature', v)}
      />
      <NumberField
        label="Top-P"
        hint="stored; support varies by backend"
        value={params.top_p}
        min={0} max={1} step={0.05}
        onChange={v => set('top_p', v)}
      />
      <NumberField
        label="Max Tokens"
        value={params.max_tokens}
        min={256} max={32768} step={256}
        onChange={v => set('max_tokens', v)}
      />
    </CollapsibleSection>
  );
};

// ── Main component ────────────────────────────────────────────────────────────

interface ModelSettingsProps {
  onClose: () => void;
}

export const ModelSettings: React.FC<ModelSettingsProps> = ({ onClose }) => {
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);
  const [showKey, setShowKey] = useState(false);
  const [rawConfig, setRawConfig] = useState<Record<string, unknown>>({});
  const [form, setForm] = useState<FormState | null>(null);
  const [originalForm, setOriginalForm] = useState<FormState | null>(null);

  useEffect(() => {
    apiClient.getModelConfig()
      .then(cfg => {
        setRawConfig(cfg);
        const backend = (cfg.model_backend as Backend) || 'ollama';
        const state = extractFormState(cfg, backend);
        setForm(state);
        setOriginalForm(state);
      })
      .catch(e => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  const handleBackendChange = useCallback((b: Backend) => {
    if (!rawConfig) return;
    const state = extractFormState(rawConfig, b);
    setForm(state);
    setSuccessMsg(null);
  }, [rawConfig]);

  const handleSave = useCallback(async () => {
    if (!form || !originalForm) return;
    setSaving(true);
    setError(null);
    setSuccessMsg(null);
    try {
      const payload = buildPayload(form, originalForm);
      const updated = await apiClient.updateModelConfig(payload);
      setRawConfig(updated);
      const newState = extractFormState(updated, form.backend);
      setForm(newState);
      setOriginalForm(newState);
      setSuccessMsg('Settings saved and applied.');
    } catch (e: any) {
      setError(e.message || 'Save failed');
    } finally {
      setSaving(false);
    }
  }, [form, originalForm]);

  if (loading) {
    return (
      <div className="flex items-center justify-center py-12">
        <Loader2 className="w-5 h-5 animate-spin text-gray-400" />
      </div>
    );
  }

  if (!form) {
    return (
      <div className="p-4 text-sm text-red-500">
        {error || 'Failed to load configuration.'}
      </div>
    );
  }

  const backendNeedsKey = !BACKENDS_WITHOUT_KEY.includes(form.backend);

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2 border-b border-gray-200 dark:border-gray-700 flex-shrink-0">
        <span className="text-sm font-semibold text-gray-700 dark:text-gray-200">Model Settings</span>
        <button
          onClick={onClose}
          className="p-1 rounded hover:bg-gray-200 dark:hover:bg-gray-700 transition-colors"
          title="Close"
        >
          <X className="w-4 h-4 text-gray-500 dark:text-gray-400" />
        </button>
      </div>

      {/* Scrollable body */}
      <div className="flex-1 overflow-y-auto px-3 py-3 space-y-3">

        {/* ── Provider ── */}
        <CollapsibleSection title="Provider" defaultOpen>
          <Field label="Backend">
            <select
              value={form.backend}
              onChange={e => handleBackendChange(e.target.value as Backend)}
              className={inputCls}
            >
              {BACKENDS.map(b => (
                <option key={b.value} value={b.value}>{b.label}</option>
              ))}
            </select>
          </Field>

          <Field label={BACKEND_URL_LABELS[form.backend]}>
            <input
              type="text"
              value={form.api_base_url}
              onChange={e => setForm(f => f ? { ...f, api_base_url: e.target.value } : f)}
              placeholder={BACKEND_URL_PLACEHOLDERS[form.backend]}
              className={inputCls}
            />
          </Field>

          {backendNeedsKey && (
            <Field label="API Key / Secret">
              <div className="relative">
                <input
                  type={showKey ? 'text' : 'password'}
                  value={form.api_key}
                  onChange={e => setForm(f => f ? { ...f, api_key: e.target.value } : f)}
                  placeholder={form.api_key ? '••••••••' : 'Not set'}
                  className={`${inputCls} pr-8`}
                  autoComplete="off"
                />
                <button
                  type="button"
                  onClick={() => setShowKey(v => !v)}
                  className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300"
                  tabIndex={-1}
                >
                  {showKey
                    ? <EyeOff className="w-3.5 h-3.5" />
                    : <Eye className="w-3.5 h-3.5" />}
                </button>
              </div>
            </Field>
          )}
        </CollapsibleSection>

        {/* ── Per-agent sections ── */}
        <AgentSection
          title="Root Agent"
          subtitle="planning, synthesis, reporting"
          params={form.root}
          onChange={root => setForm(f => f ? { ...f, root } : f)}
          defaultOpen
        />
        <AgentSection
          title="Search Agent"
          subtitle="web retrieval"
          params={form.search}
          onChange={search => setForm(f => f ? { ...f, search } : f)}
          defaultOpen={false}
        />
        <AgentSection
          title="Analyst Agent"
          subtitle="claim extraction"
          params={form.analyst}
          onChange={analyst => setForm(f => f ? { ...f, analyst } : f)}
          defaultOpen={false}
        />
        <AgentSection
          title="QA Agent"
          subtitle="contradiction detection"
          params={form.qa}
          onChange={qa => setForm(f => f ? { ...f, qa } : f)}
          defaultOpen={false}
        />

        {/* Messages */}
        {error && (
          <p className="text-xs text-red-500 dark:text-red-400 px-1">{error}</p>
        )}
        {successMsg && (
          <p className="text-xs text-green-600 dark:text-green-400 px-1">{successMsg}</p>
        )}

        <p className="text-xs text-gray-400 dark:text-gray-500 px-1">
          Changes take effect immediately for new research sessions.
          Top-P is stored but support varies by provider.
        </p>
      </div>

      {/* Footer — Save/Cancel */}
      <div className="flex gap-2 px-3 py-2 border-t border-gray-200 dark:border-gray-700 flex-shrink-0">
        <button
          onClick={handleSave}
          disabled={saving}
          className="flex-1 flex items-center justify-center gap-1.5 py-1.5 text-xs font-semibold
                     rounded-md bg-blue-600 hover:bg-blue-700 disabled:opacity-60
                     text-white transition-colors"
        >
          {saving
            ? <Loader2 className="w-3.5 h-3.5 animate-spin" />
            : <Save className="w-3.5 h-3.5" />}
          {saving ? 'Saving…' : 'Save'}
        </button>
        <button
          onClick={onClose}
          className="flex-1 py-1.5 text-xs font-medium rounded-md
                     bg-gray-100 hover:bg-gray-200 dark:bg-gray-700 dark:hover:bg-gray-600
                     text-gray-600 dark:text-gray-300 transition-colors"
        >
          Cancel
        </button>
      </div>
    </div>
  );
};
