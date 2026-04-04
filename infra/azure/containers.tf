# ─────────────────────────────────────────────────────────────────────────────
# Shared registry block (referenced by every Container App below).
# All apps use the same user-assigned managed identity to pull from ACR.
# ─────────────────────────────────────────────────────────────────────────────

locals {
  acr_registry_block = {
    server   = azurerm_container_registry.main.login_server
    identity = azurerm_user_assigned_identity.acr_pull.id
  }
}

# ─────────────────────────────────────────────────────────────────────────────
# MCP Web Search Server  (internal — DuckDuckGo search)
# Port 9393
# ─────────────────────────────────────────────────────────────────────────────

resource "azurerm_container_app" "mcp_web_search" {
  name                         = "${local.prefix}-mcp-web-search"
  resource_group_name          = azurerm_resource_group.main.name
  container_app_environment_id = azurerm_container_app_environment.main.id
  revision_mode                = "Single"
  tags                         = local.tags

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.acr_pull.id]
  }

  registry {
    server   = local.acr_registry_block.server
    identity = local.acr_registry_block.identity
  }

  ingress {
    external_enabled = false
    target_port      = 9393
    transport        = "http"

    traffic_weight {
      percentage      = 100
      latest_revision = true
    }
  }

  template {
    min_replicas = 1
    max_replicas = 2

    container {
      name   = "mcp-web-search"
      image  = "${azurerm_container_registry.main.login_server}/mcp-web-search:${var.image_tag}"
      cpu    = 0.25
      memory = "0.5Gi"

      env {
        name  = "HOST_PORT"
        value = "9393"
      }
      env {
        name  = "HOST_ADDRESS"
        value = "0.0.0.0"
      }
      env {
        name  = "MCP_API_KEY"
        value = var.mcp_api_key
      }
      env {
        name  = "MCP_SECRET_KEY"
        value = var.mcp_secret_key
      }
    }
  }
}

# ─────────────────────────────────────────────────────────────────────────────
# MCP Web Scrape Server  (internal — HTML extraction)
# Port 9292
# ─────────────────────────────────────────────────────────────────────────────

resource "azurerm_container_app" "mcp_web_scrape" {
  name                         = "${local.prefix}-mcp-web-scrape"
  resource_group_name          = azurerm_resource_group.main.name
  container_app_environment_id = azurerm_container_app_environment.main.id
  revision_mode                = "Single"
  tags                         = local.tags

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.acr_pull.id]
  }

  registry {
    server   = local.acr_registry_block.server
    identity = local.acr_registry_block.identity
  }

  ingress {
    external_enabled = false
    target_port      = 9292
    transport        = "http"

    traffic_weight {
      percentage      = 100
      latest_revision = true
    }
  }

  template {
    min_replicas = 1
    max_replicas = 2

    container {
      name   = "mcp-web-scrape"
      image  = "${azurerm_container_registry.main.login_server}/mcp-web-scrape:${var.image_tag}"
      cpu    = 0.25
      memory = "0.5Gi"

      env {
        name  = "HOST_PORT"
        value = "9292"
      }
      env {
        name  = "HOST_ADDRESS"
        value = "0.0.0.0"
      }
      env {
        name  = "MCP_API_KEY"
        value = var.mcp_api_key
      }
      env {
        name  = "MCP_SECRET_KEY"
        value = var.mcp_secret_key
      }
    }
  }
}

# ─────────────────────────────────────────────────────────────────────────────
# MCP File Handler Server  (internal — file I/O)
# Port 9191 | mounts file-handler Azure File Share at /app/data
# ─────────────────────────────────────────────────────────────────────────────

resource "azurerm_container_app" "mcp_file_handler" {
  name                         = "${local.prefix}-mcp-file-handler"
  resource_group_name          = azurerm_resource_group.main.name
  container_app_environment_id = azurerm_container_app_environment.main.id
  revision_mode                = "Single"
  tags                         = local.tags

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.acr_pull.id]
  }

  registry {
    server   = local.acr_registry_block.server
    identity = local.acr_registry_block.identity
  }

  ingress {
    external_enabled = false
    target_port      = 9191
    transport        = "http"

    traffic_weight {
      percentage      = 100
      latest_revision = true
    }
  }

  template {
    min_replicas = 1
    max_replicas = 1

    volume {
      name         = "file-handler-data"
      storage_type = "AzureFile"
      storage_name = azurerm_container_app_environment_storage.file_handler.name
    }

    container {
      name   = "mcp-file-handler"
      image  = "${azurerm_container_registry.main.login_server}/mcp-file-handler:${var.image_tag}"
      cpu    = 0.25
      memory = "0.5Gi"

      env {
        name  = "HOST_PORT"
        value = "9191"
      }
      env {
        name  = "HOST_ADDR"
        value = "0.0.0.0"
      }
      env {
        name  = "MCP_API_KEY"
        value = var.mcp_api_key
      }
      env {
        name  = "MCP_SECRET_KEY"
        value = var.mcp_secret_key
      }

      volume_mounts {
        name = "file-handler-data"
        path = "/app/data"
      }
    }
  }
}

# ─────────────────────────────────────────────────────────────────────────────
# Backend (research-assistant)
# Port 9999 | external ingress with WebSocket support
# Depends on all three MCP servers so their internal FQDNs are known at plan time.
# ─────────────────────────────────────────────────────────────────────────────

resource "azurerm_container_app" "backend" {
  name                         = "${local.prefix}-backend"
  resource_group_name          = azurerm_resource_group.main.name
  container_app_environment_id = azurerm_container_app_environment.main.id
  revision_mode                = "Single"
  tags                         = local.tags

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.acr_pull.id]
  }

  registry {
    server   = local.acr_registry_block.server
    identity = local.acr_registry_block.identity
  }

  ingress {
    external_enabled = true
    target_port      = 9999
    # "auto" enables both HTTP/1.1 and HTTP/2, which is required for WebSocket.
    transport = "auto"

    traffic_weight {
      percentage      = 100
      latest_revision = true
    }
  }

  template {
    min_replicas = 1
    max_replicas = 3

    volume {
      name         = "chroma-data"
      storage_type = "AzureFile"
      storage_name = azurerm_container_app_environment_storage.chroma.name
    }
    volume {
      name         = "backend-logs"
      storage_type = "AzureFile"
      storage_name = azurerm_container_app_environment_storage.logs.name
    }
    volume {
      name         = "backend-config"
      storage_type = "AzureFile"
      storage_name = azurerm_container_app_environment_storage.config.name
    }
    volume {
      name         = "backend-instructions"
      storage_type = "AzureFile"
      storage_name = azurerm_container_app_environment_storage.instructions.name
    }

    container {
      name  = "backend"
      image = "${azurerm_container_registry.main.login_server}/research-assistant:${var.image_tag}"
      # 2 vCPU / 4 GiB: accommodates concurrent LLM calls + optional embeddings.
      cpu    = 2.0
      memory = "4Gi"

      # ── Agent / LLM config
      env {
        name  = "AGENT_MODE"
        value = var.agent_mode
      }
      env {
        name  = "MODEL_BACKEND"
        value = var.model_backend
      }
      env {
        name  = "OPENAI_API_KEY"
        value = var.openai_api_key
      }
      env {
        name  = "OPENAI_MODEL"
        value = var.openai_model
      }
      env {
        name  = "OPENAI_BASE_URL"
        value = var.openai_base_url
      }
      env {
        name  = "AZURE_OPENAI_ENDPOINT"
        value = var.azure_openai_endpoint
      }
      env {
        name  = "AZURE_OPENAI_API_KEY"
        value = var.azure_openai_api_key
      }
      env {
        name  = "AZURE_OPENAI_DEPLOYMENT"
        value = var.azure_openai_deployment
      }
      env {
        name  = "AZURE_API_VERSION"
        value = var.azure_api_version
      }

      # ── AWS Bedrock (required when model_backend = 'bedrock')
      env {
        name  = "AWS_BASE_URL"
        value = var.aws_base_url
      }
      env {
        name  = "AWS_API_KEY"
        value = var.aws_api_key
      }

      # ── Embeddings
      # Set embeddings_enabled = true in tfvars once the deployment is stable.
      # The backend will download ~90 MB of sentence-transformer weights on first start.
      env {
        name  = "EMBEDDINGS_ENABLED"
        value = tostring(var.embeddings_enabled)
      }

      # ── MCP server URLs (internal FQDNs within the Container Apps environment)
      env {
        name  = "SEARCH_SERVER_URL"
        value = local.mcp_web_search_url
      }
      env {
        name  = "SCRAPER_SERVER_URL"
        value = local.mcp_web_scrape_url
      }
      env {
        name  = "FILE_SERVER_URL"
        value = local.mcp_file_handler_url
      }

      # ── MCP API keys (must match the keys configured on the MCP servers)
      env {
        name  = "SEARCH_SERVER_API_KEY"
        value = var.mcp_api_key
      }
      env {
        name  = "SCRAPER_SERVER_API_KEY"
        value = var.mcp_api_key
      }
      env {
        name  = "FILE_SERVER_API_KEY"
        value = var.mcp_api_key
      }

      # ── ChromaDB (in-process long-term memory)
      env {
        name  = "CHROMA_PERSIST_DIR"
        value = "/app/chroma_data"
      }
      env {
        name  = "CHROMA_COLLECTION_NAME"
        value = "agent_memories"
      }

      # ── Persistent storage paths
      env {
        name  = "LOG_DIR"
        value = "/app/logs"
      }

      volume_mounts {
        name = "chroma-data"
        path = "/app/chroma_data"
      }
      volume_mounts {
        name = "backend-logs"
        path = "/app/logs"
      }
      volume_mounts {
        name = "backend-config"
        path = "/app/config"
      }
      volume_mounts {
        name = "backend-instructions"
        path = "/app/instructions"
      }
    }
  }
}

# ─────────────────────────────────────────────────────────────────────────────
# Frontend (research-agent-ui — nginx serving the React SPA)
# Port 80 | external ingress
#
# nginx proxies /api and /ws to NGINX_BACKEND_URL and /files to
# NGINX_FILE_HANDLER_URL.  These env vars are substituted into nginx.conf at
# container startup via the nginx template mechanism (envsubst).
# ─────────────────────────────────────────────────────────────────────────────

resource "azurerm_container_app" "frontend" {
  name                         = "${local.prefix}-frontend"
  resource_group_name          = azurerm_resource_group.main.name
  container_app_environment_id = azurerm_container_app_environment.main.id
  revision_mode                = "Single"
  tags                         = local.tags

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.acr_pull.id]
  }

  registry {
    server   = local.acr_registry_block.server
    identity = local.acr_registry_block.identity
  }

  ingress {
    external_enabled = true
    target_port      = 80
    transport        = "http"

    traffic_weight {
      percentage      = 100
      latest_revision = true
    }
  }

  template {
    min_replicas = 1
    max_replicas = 3

    container {
      name   = "frontend"
      image  = "${azurerm_container_registry.main.login_server}/research-agent-ui:${var.image_tag}"
      cpu    = 0.25
      memory = "0.5Gi"

      # nginx reads these at container startup to configure its proxy_pass targets.
      env {
        name  = "NGINX_BACKEND_URL"
        value = "https://${azurerm_container_app.backend.ingress[0].fqdn}"
      }
      env {
        name  = "NGINX_FILE_HANDLER_URL"
        value = "https://${azurerm_container_app.mcp_file_handler.ingress[0].fqdn}"
      }
    }
  }

  depends_on = [azurerm_container_app.backend]
}
