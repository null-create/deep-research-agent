# ACR name: globally unique, 5-50 alphanumeric only (no dashes).
# Format: <project><env>acr<random-4-byte-hex>
resource "azurerm_container_registry" "main" {
  name                = "${replace(local.prefix, "-", "")}acr${random_id.suffix.hex}"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  sku                 = var.acr_sku
  admin_enabled       = false
  tags                = local.tags
}

# User-assigned managed identity used by all Container Apps to pull images from ACR.
# A single shared identity is sufficient; scope it narrowly to this ACR only.
resource "azurerm_user_assigned_identity" "acr_pull" {
  name                = "id-${local.prefix}-acr-pull"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  tags                = local.tags
}

resource "azurerm_role_assignment" "acr_pull" {
  principal_id                     = azurerm_user_assigned_identity.acr_pull.principal_id
  role_definition_name             = "AcrPull"
  scope                            = azurerm_container_registry.main.id
  skip_service_principal_aad_check = true
}
