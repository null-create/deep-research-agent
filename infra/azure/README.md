# Research Assistant — Azure Container Apps Deployment

Terraform configuration for deploying the full Research Assistant stack to [Azure Container Apps](https://learn.microsoft.com/en-us/azure/container-apps/overview).

**Services deployed:**

| Container App | Ingress | Port | Description |
|---|---|---|---|
| `<prefix>-mcp-web-search` | Internal | 9393 | Web search MCP server |
| `<prefix>-mcp-web-scrape` | Internal | 9292 | Web scraping MCP server |
| `<prefix>-mcp-file-handler` | Internal | 9191 | File I/O MCP server (Azure File Share at `/app/data`) |
| `<prefix>-backend` | **External** | 9999 | FastAPI + WebSocket research backend — 4 Azure File Share mounts: ChromaDB (`/app/chroma_data`), logs/sessions (`/app/logs`), config (`/app/config`), instructions (`/app/instructions`) |
| `<prefix>-frontend` | **External** | 80 | React SPA served by nginx — proxies `/ws` + `/api` → backend, `/files` → file-handler |

All five apps run in a shared Container Apps environment and communicate over the environment's internal network. MCP servers are not reachable from the public internet.

---

## Prerequisites

- [Terraform](https://developer.hashicorp.com/terraform/downloads) ≥ 1.9
- [Azure CLI](https://learn.microsoft.com/en-us/cli/azure/install-azure-cli) ≥ 2.60
- [Docker](https://docs.docker.com/get-docker/) (to build and push images)
- An Azure subscription with sufficient quota for Container Apps in your target region
- A service principal with **Contributor** on the subscription (or a scoped role — see below)

### Minimum service principal permissions

```
Contributor         on the resource group (or subscription)
AcrPush             on the Container Registry (granted after first apply)
```

Create a service principal and capture its credentials:

```bash
az ad sp create-for-rbac \
  --name "sp-research-assistant-deploy" \
  --role Contributor \
  --scopes /subscriptions/<SUBSCRIPTION_ID> \
  --output json
```

Save the output — you will need `appId`, `password`, and `tenant` for `terraform.tfvars`.

---

## Directory layout

```
infra/azure/
├── main.tf             Terraform + provider block, random suffix, shared locals
├── variables.tf        All input variable declarations
├── outputs.tf          Key outputs (URLs, image names, ACR login server)
├── terraform.tfvars    ⚠ Credential values — never commit real secrets
├── backend.conf        Remote state connection — populate before `terraform init`
├── resource_group.tf   Azure resource group
├── registry.tf         Azure Container Registry + managed identity for ACR pull
├── storage.tf          Storage account + Azure File Shares for persistent volumes
├── log_analytics.tf    Log Analytics workspace (required by Container Apps)
├── container_env.tf    Container Apps environment + environment-level storage mounts
└── containers.tf       All six Container App definitions
```

---

## Step 1 — Bootstrap the Terraform state storage

The remote state storage account must exist **before** running `terraform init`. Create it once:

```bash
LOCATION="eastus"
TFSTATE_RG="rg-tfstate"
TFSTATE_SA="stresearchassttfstate"   # must be globally unique, 3-24 alphanumeric
TFSTATE_CONTAINER="tfstate"

az group create --name "$TFSTATE_RG" --location "$LOCATION"

az storage account create \
  --name "$TFSTATE_SA" \
  --resource-group "$TFSTATE_RG" \
  --location "$LOCATION" \
  --sku Standard_LRS \
  --min-tls-version TLS1_2 \
  --allow-blob-public-access false

az storage container create \
  --name "$TFSTATE_CONTAINER" \
  --account-name "$TFSTATE_SA"
```

Update `backend.conf` with the values you used above.

---

## Step 2 — Populate configuration files

Copy the example values and fill in real credentials:

```bash
# 1. Update backend.conf with the tfstate storage account name created in Step 1.

# 2. Update terraform.tfvars:
#    - subscription_id, tenant_id, client_id, client_secret (service principal)
#    - openai_api_key (or the relevant keys for your chosen model_backend)
#    - mcp_api_key and mcp_secret_key (generate strong random strings)
```

> ⚠ **`terraform.tfvars` contains secrets. It is in `.gitignore` by default — confirm it is NOT tracked by git before pushing.**

To generate strong random values for the MCP keys:

```bash
openssl rand -hex 32   # run twice — once for mcp_api_key, once for mcp_secret_key
```

---

## Step 3 — Apply the infrastructure

```bash
cd infra/azure

# Initialise Terraform with the remote state backend
terraform init -backend-config=backend.conf

# Review the plan
terraform plan -var-file=terraform.tfvars

# Apply (creates ~15 resources, takes ~5 minutes)
terraform apply -var-file=terraform.tfvars
```

After apply, note the outputs — you will need them in Step 4:

```bash
terraform output
```

Key outputs:

| Output | Used for |
|---|---|
| `container_registry_login_server` | Docker image push target |
| `backend_ws_url` | `VITE_WS_URL` build arg for the frontend image |
| `backend_url` | nginx proxy target for the frontend container |
| `frontend_url` | Where to open the app in a browser |

---

## Step 4 — Build and push container images

Log in to ACR:

```bash
az acr login --name "$(terraform output -raw container_registry_name)"
ACR=$(terraform output -raw container_registry_login_server)
```

Build and push all service images from the repository root:

```bash
# Backend — build context is the repo root so docs/ gets bundled in the image.
# The /project-docs API endpoint serves these docs in Azure (no bind-mount there).
docker build -f backend/Dockerfile -t "$ACR/research-assistant:latest" .
docker push "$ACR/research-assistant:latest"

# MCP servers
docker build -t "$ACR/mcp-web-search:latest"   ./mcp/web_search
docker build -t "$ACR/mcp-web-scrape:latest"   ./mcp/web_scrape
docker build -t "$ACR/mcp-file-handler:latest" ./mcp/file_handler

docker push "$ACR/mcp-web-search:latest"
docker push "$ACR/mcp-web-scrape:latest"
docker push "$ACR/mcp-file-handler:latest"
```

### Frontend image

The React SPA connects to the backend via WebSocket and REST. The nginx container proxies `/ws` and `/api` to the backend, and `/files` to the file-handler MCP server, using env vars injected at runtime via nginx's built-in template mechanism.

Build and push the frontend image — **no special build-time configuration needed**:

```bash
docker build -t "$ACR/research-agent-ui:latest" ./src
docker push "$ACR/research-agent-ui:latest"
```

Terraform automatically sets `NGINX_BACKEND_URL` and `NGINX_FILE_HANDLER_URL` on the frontend Container App. nginx substitutes these into its config when the container starts.

---

## Step 5 — Trigger a Container App revision

After pushing new images, force the Container Apps to pull the latest tag:

```bash
RG=$(terraform output -raw resource_group_name)
PREFIX="ra-prod"   # project_name + environment from tfvars

for APP in mcp-web-search mcp-web-scrape mcp-file-handler backend frontend; do
  az containerapp update \
    --name "${PREFIX}-${APP}" \
    --resource-group "$RG" \
    --image "$ACR/$(echo $APP | sed 's/mcp-/mcp-/'):latest"
done
```

Or re-run `terraform apply` after updating `image_tag` in `terraform.tfvars` to a new pinned tag (recommended for production).

---

## Step 6 — Verify the deployment

```bash
# Open the frontend in your browser
open "$(terraform output -raw frontend_url)"

# Tail backend logs
az containerapp logs show \
  --name "$(terraform output -raw resource_group_name | sed 's/rg-//')-backend" \
  --resource-group "$(terraform output -raw resource_group_name)" \
  --follow
```

---

## Enabling in-process embeddings

The backend's RAG store uses sentence-transformer embeddings for semantic retrieval. Embeddings are **disabled by default** in the Terraform config (`embeddings_enabled = false`) to keep memory usage predictable on first deploy.

To enable:

1. Set `embeddings_enabled = true` in `terraform.tfvars`.
2. Run `terraform apply -var-file=terraform.tfvars`.
3. On first start the backend will download ~90 MB of model weights (`all-MiniLM-L6-v2`). Set `HF_TOKEN` to avoid HuggingFace download throttling.

---

## Scaling

Container App replica counts are set conservatively:

| Service | min | max | Notes |
|---|---|---|---|
| mcp-file-handler | 1 | 1 | Stateful (Azure File Share) |
| mcp-web-search | 1 | 2 | Stateless, can scale |
| mcp-web-scrape | 1 | 2 | Stateless, can scale |
| backend | 1 | 3 | 4 Azure File Shares mounted (`chroma_data`, `logs`, `config`, `instructions`) — do not scale to zero; sticky sessions for WebSocket |
| frontend | 1 | 3 | Stateless nginx |

Adjust `min_replicas` / `max_replicas` in `containers.tf` as needed.

> **Scaling note for backend:** The backend mounts Azure File Shares for ChromaDB, logs/session checkpoints, model settings config, and self-optimization instructions. Multi-replica deployments share the same file shares — ChromaDB is not designed for concurrent multi-writer access, so keep `max_replicas = 1` if self-optimization mode is in use.

---

## CI/CD workflow integration

In a GitHub Actions or Azure DevOps pipeline:

1. Store all `terraform.tfvars` values as pipeline secrets.
2. At runtime, generate the files before Terraform runs:

   ```bash
   # Generate backend.conf
   cat > infra/azure/backend.conf <<EOF
   resource_group_name  = "$TF_STATE_RG"
   storage_account_name = "$TF_STATE_SA"
   container_name       = "tfstate"
   key                  = "research-assistant/terraform.tfstate"
   EOF

   # Generate terraform.tfvars
   cat > infra/azure/terraform.tfvars <<EOF
   subscription_id = "$ARM_SUBSCRIPTION_ID"
   tenant_id       = "$ARM_TENANT_ID"
   client_id       = "$ARM_CLIENT_ID"
   client_secret   = "$ARM_CLIENT_SECRET"
   ...
   EOF
   ```

3. Run `terraform init -backend-config=backend.conf && terraform apply -var-file=terraform.tfvars -auto-approve`.

Alternatively, pass all values via `-var` flags or `TF_VAR_*` environment variables to avoid writing secrets to disk.
