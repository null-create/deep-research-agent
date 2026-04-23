export type Backend = 'ollama' | 'openai' | 'azure' | 'aws' | 'bedrock' | 'anthropic' | 'gcp' | 'huggingface';

export interface AgentParams {
  model: string;
  temperature: number;
  top_p: number;
  max_tokens: number;
}

export interface FormState {
  backend: Backend;
  api_key: string;
  api_base_url: string;
  root: AgentParams;
  search: AgentParams;
  analyst: AgentParams;
  qa: AgentParams;
}
