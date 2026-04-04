# ─────────────────────────────────────────────
# Azure authentication (service principal)
# ─────────────────────────────────────────────

variable "subscription_id" {
  description = "Azure subscription ID"
  type        = string
  sensitive   = true
}

variable "tenant_id" {
  description = "Azure Active Directory tenant ID"
  type        = string
  sensitive   = true
}

variable "client_id" {
  description = "Service principal application (client) ID"
  type        = string
  sensitive   = true
}

variable "client_secret" {
  description = "Service principal client secret"
  type        = string
  sensitive   = true
}

# ─────────────────────────────────────────────
# Deployment targeting
# ─────────────────────────────────────────────

variable "project_name" {
  description = "Short identifier prefix for all resource names (e.g. 'ra'). Keep short — Azure Container App names max 32 chars."
  type        = string
  default     = "ra"

  validation {
    condition     = length(var.project_name) <= 8 && can(regex("^[a-z0-9]+$", var.project_name))
    error_message = "project_name must be 1-8 lowercase alphanumeric characters."
  }
}

variable "environment" {
  description = "Deployment environment label (e.g. 'prod', 'staging', 'dev')"
  type        = string
  default     = "prod"
}

variable "location" {
  description = "Azure region for all resources (e.g. 'eastus', 'westeurope')"
  type        = string
  default     = "eastus"
}

# ─────────────────────────────────────────────
# Container images
# ─────────────────────────────────────────────

variable "image_tag" {
  description = "Docker image tag to pull from ACR for all services. Defaults to 'latest'; pin to a SHA digest in production."
  type        = string
  default     = "latest"
}

# ─────────────────────────────────────────────
# Azure Container Registry
# ─────────────────────────────────────────────

variable "acr_sku" {
  description = "ACR SKU.  Basic is sufficient for a single-region deployment; use Premium for geo-replication."
  type        = string
  default     = "Basic"

  validation {
    condition     = contains(["Basic", "Standard", "Premium"], var.acr_sku)
    error_message = "acr_sku must be Basic, Standard, or Premium."
  }
}

# ─────────────────────────────────────────────
# LLM / model backend
# ─────────────────────────────────────────────

variable "model_backend" {
  description = "LLM backend to use: openai | azure_openai | bedrock | gcp | ollama | huggingface"
  type        = string
  default     = "openai"
}

variable "openai_api_key" {
  description = "OpenAI API key (required when model_backend = 'openai')"
  type        = string
  sensitive   = true
  default     = ""
}

variable "openai_model" {
  description = "OpenAI model name"
  type        = string
  default     = "gpt-4o"
}

variable "openai_base_url" {
  description = "OpenAI-compatible API base URL (override for compatible proxies)"
  type        = string
  default     = "https://api.openai.com/v1"
}

variable "azure_openai_endpoint" {
  description = "Azure OpenAI endpoint URL (required when model_backend = 'azure_openai')"
  type        = string
  default     = ""
}

variable "azure_openai_api_key" {
  description = "Azure OpenAI API key (required when model_backend = 'azure_openai')"
  type        = string
  sensitive   = true
  default     = ""
}

variable "azure_openai_deployment" {
  description = "Azure OpenAI deployment name"
  type        = string
  default     = ""
}

variable "azure_api_version" {
  description = "Azure OpenAI API version"
  type        = string
  default     = "2024-02-15-preview"
}

# ─────────────────────────────────────────────
# MCP server authentication
# ─────────────────────────────────────────────

variable "mcp_api_key" {
  description = "API key the backend uses to authenticate with all MCP servers (JWT payload)"
  type        = string
  sensitive   = true
}

variable "mcp_secret_key" {
  description = "JWT HMAC secret used by MCP servers to sign and verify tokens"
  type        = string
  sensitive   = true
}

# ─────────────────────────────────────────────
# AWS Bedrock
# ─────────────────────────────────────────────

variable "aws_base_url" {
  description = "AWS Bedrock OpenAI-compatible base URL (required when model_backend = 'bedrock')"
  type        = string
  default     = ""
}

variable "aws_api_key" {
  description = "AWS Bedrock API key (required when model_backend = 'bedrock')"
  type        = string
  sensitive   = true
  default     = ""
}

# ─────────────────────────────────────────────
# Backend tuning (optional overrides)
# ─────────────────────────────────────────────

variable "embeddings_enabled" {
  description = "Enable in-process sentence-transformer embeddings on the backend. Disable to reduce memory usage (falls back to insertion-order RAG ranking)."
  type        = bool
  default     = false
}

variable "agent_mode" {
  description = "Backend agent mode: research | chat | self-optimization"
  type        = string
  default     = "research"
}
