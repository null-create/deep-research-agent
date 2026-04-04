output "resource_group_name" {
  description = "Name of the deployed Azure resource group"
  value       = azurerm_resource_group.main.name
}

output "container_registry_login_server" {
  description = "ACR login server — use as the Docker registry for image pushes"
  value       = azurerm_container_registry.main.login_server
}

output "container_registry_name" {
  description = "ACR resource name (used with `az acr login --name`)"
  value       = azurerm_container_registry.main.name
}

output "frontend_url" {
  description = "Public HTTPS URL of the research assistant frontend"
  value       = "https://${azurerm_container_app.frontend.ingress[0].fqdn}"
}

output "backend_url" {
  description = "Public HTTPS URL of the research assistant backend API"
  value       = "https://${azurerm_container_app.backend.ingress[0].fqdn}"
}

output "backend_ws_url" {
  description = "WebSocket URL for the backend — use as VITE_WS_URL when building the frontend image"
  value       = "wss://${azurerm_container_app.backend.ingress[0].fqdn}/ws/research"
}

output "container_app_environment_default_domain" {
  description = "Default domain of the Container Apps environment (used to construct internal FQDNs)"
  value       = azurerm_container_app_environment.main.default_domain
}

output "image_names" {
  description = "Fully qualified ACR image names for each service (tag with var.image_tag)"
  value = {
    backend          = "${azurerm_container_registry.main.login_server}/research-assistant:${var.image_tag}"
    frontend         = "${azurerm_container_registry.main.login_server}/research-agent-ui:${var.image_tag}"
    mcp_web_search   = "${azurerm_container_registry.main.login_server}/mcp-web-search:${var.image_tag}"
    mcp_web_scrape   = "${azurerm_container_registry.main.login_server}/mcp-web-scrape:${var.image_tag}"
    mcp_file_handler = "${azurerm_container_registry.main.login_server}/mcp-file-handler:${var.image_tag}"
  }
}
