# Storage account for persistent volume mounts used by the MCP servers.
# Name must be globally unique, 3-24 lowercase alphanumeric chars only.
resource "azurerm_storage_account" "app" {
  name                            = "st${replace(local.prefix, "-", "")}${random_id.suffix.hex}"
  resource_group_name             = azurerm_resource_group.main.name
  location                        = azurerm_resource_group.main.location
  account_tier                    = "Standard"
  account_replication_type        = "LRS"
  min_tls_version                 = "TLS1_2"
  allow_nested_items_to_be_public = false
  tags                            = local.tags
}

# File share for ChromaDB data used by the backend's in-process long-term memory store.
resource "azurerm_storage_share" "chroma" {
  name               = "chroma-data"
  storage_account_id = azurerm_storage_account.app.id
  quota              = 10 # GB
}

# File share for file I/O data used by the MCP file handler server.
resource "azurerm_storage_share" "file_handler" {
  name               = "file-handler-data"
  storage_account_id = azurerm_storage_account.app.id
  quota              = 10 # GB
}

# File share for backend log output — persists session checkpoints across container restarts.
# Without this, research sessions are unrecoverable after a container restart.
resource "azurerm_storage_share" "logs" {
  name               = "backend-logs"
  storage_account_id = azurerm_storage_account.app.id
  quota              = 10 # GB
}

# File share for backend configuration (model_settings.json written by POST /config).
resource "azurerm_storage_share" "config" {
  name               = "backend-config"
  storage_account_id = azurerm_storage_account.app.id
  quota              = 1 # GB
}

# File share for backend instructions (RESEARCH-METHODS.md written by the self-optimization pipeline).
resource "azurerm_storage_share" "instructions" {
  name               = "backend-instructions"
  storage_account_id = azurerm_storage_account.app.id
  quota              = 1 # GB
}
