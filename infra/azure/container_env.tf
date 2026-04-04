resource "azurerm_container_app_environment" "main" {
  name                       = "cae-${local.prefix}"
  resource_group_name        = azurerm_resource_group.main.name
  location                   = azurerm_resource_group.main.location
  log_analytics_workspace_id = azurerm_log_analytics_workspace.main.id
  tags                       = local.tags
}

# Mount the chroma file share into the Container Apps environment so the
# backend container can reference it by name in its volume block.
resource "azurerm_container_app_environment_storage" "chroma" {
  name                         = "chroma-storage"
  container_app_environment_id = azurerm_container_app_environment.main.id
  account_name                 = azurerm_storage_account.app.name
  share_name                   = azurerm_storage_share.chroma.name
  access_key                   = azurerm_storage_account.app.primary_access_key
  access_mode                  = "ReadWrite"
}

# Mount the file handler file share into the environment for the mcp-file-handler.
resource "azurerm_container_app_environment_storage" "file_handler" {
  name                         = "file-handler-storage"
  container_app_environment_id = azurerm_container_app_environment.main.id
  account_name                 = azurerm_storage_account.app.name
  share_name                   = azurerm_storage_share.file_handler.name
  access_key                   = azurerm_storage_account.app.primary_access_key
  access_mode                  = "ReadWrite"
}

# Mount the logs file share for backend session checkpoints and NDJSON logs.
resource "azurerm_container_app_environment_storage" "logs" {
  name                         = "logs-storage"
  container_app_environment_id = azurerm_container_app_environment.main.id
  account_name                 = azurerm_storage_account.app.name
  share_name                   = azurerm_storage_share.logs.name
  access_key                   = azurerm_storage_account.app.primary_access_key
  access_mode                  = "ReadWrite"
}

# Mount the config file share for persisted model settings (model_settings.json).
resource "azurerm_container_app_environment_storage" "config" {
  name                         = "config-storage"
  container_app_environment_id = azurerm_container_app_environment.main.id
  account_name                 = azurerm_storage_account.app.name
  share_name                   = azurerm_storage_share.config.name
  access_key                   = azurerm_storage_account.app.primary_access_key
  access_mode                  = "ReadWrite"
}

# Mount the instructions file share for RESEARCH-METHODS.md written by self-optimization.
resource "azurerm_container_app_environment_storage" "instructions" {
  name                         = "instructions-storage"
  container_app_environment_id = azurerm_container_app_environment.main.id
  account_name                 = azurerm_storage_account.app.name
  share_name                   = azurerm_storage_share.instructions.name
  access_key                   = azurerm_storage_account.app.primary_access_key
  access_mode                  = "ReadWrite"
}
