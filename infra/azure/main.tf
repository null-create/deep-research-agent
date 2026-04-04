terraform {
  required_version = ">= 1.9.0"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }

  # Partial backend configuration — connection values are supplied via
  # backend.conf at init time:
  #   terraform init -backend-config=backend.conf
  backend "azurerm" {}
}

provider "azurerm" {
  features {}

  subscription_id = var.subscription_id
  tenant_id       = var.tenant_id
  client_id       = var.client_id
  client_secret   = var.client_secret
}

# Random 4-byte hex suffix used to ensure globally unique names (ACR, storage).
resource "random_id" "suffix" {
  byte_length = 4
}

locals {
  prefix = "${var.project_name}-${var.environment}"

  tags = {
    project     = var.project_name
    environment = var.environment
    managed_by  = "terraform"
  }

  # Internal FQDN patterns (resolved after MCP Container Apps are created)
  mcp_web_search_url   = "https://${azurerm_container_app.mcp_web_search.ingress[0].fqdn}/mcp"
  mcp_web_scrape_url   = "https://${azurerm_container_app.mcp_web_scrape.ingress[0].fqdn}/mcp"
  mcp_file_handler_url = "https://${azurerm_container_app.mcp_file_handler.ingress[0].fqdn}/mcp"
}
