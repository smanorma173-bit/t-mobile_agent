# GCP → Terraform Agent v2

Converts a live GCP project (or an exported CSV) into production-ready Terraform.

## What's new in v2

| Feature | v1 | v2 |
|---|---|---|
| LLM backend | Gemini only | **Claude + Gemini** (auto-detected) |
| Inventory source | CSV/Excel upload required | **`--project PROJECT_ID`** (live API) or CSV |
| Terraform output | monolithic `main.tf` | **Split files** per resource type |

---

## Quick start

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Set your LLM API key

Claude (recommended):
```bash
export ANTHROPIC_API_KEY='sk-ant-...'
```

Or Gemini:
```bash
export GEMINI_API_KEY='AIza...'
```

Both keys set? The agent auto-picks Claude. Override with:
```bash
export LLM_PROVIDER=claude   # or gemini
```

### 3. Run — live GCP project (recommended)

```bash
# Authenticate first
gcloud auth application-default login
gcloud services enable cloudasset.googleapis.com --project=my-project-id

# Generate Terraform
python -m agent.main --project my-project-id
```

### 3b. Run — legacy CSV mode

```bash
python -m agent.main --inventory gcp-asset-inventory.csv
```

---

## Output structure

```
terraform_output/
├── providers.tf      ← provider & terraform version block
├── backend.tf        ← GCS remote state configuration
├── variables.tf      ← all variable declarations
├── outputs.tf        ← all output declarations
├── terraform.tfvars  ← generated values from your inventory
│
├── vpcs.tf           ← module "vpc" call
├── subnets.tf        ← module "subnet" call
├── firewalls.tf      ← module "firewall" call
├── routers.tf        ← module "router" call
├── addresses.tf      ← module "address" call
├── negs.tf           ← module "neg" (Network Endpoint Groups) call
│
└── modules/
    ├── vpc/           main.tf · variables.tf · outputs.tf
    ├── subnet/        main.tf · variables.tf · outputs.tf
    ├── firewall/      main.tf · variables.tf · outputs.tf
    ├── router/        main.tf · variables.tf · outputs.tf
    ├── address/       main.tf · variables.tf · outputs.tf
    └── neg/           main.tf · variables.tf · outputs.tf
```

---

## Deploy

```bash
cd terraform_output

# Create GCS bucket for state (once only)
gsutil mb -p my-project-id gs://my-project-id-tfstate

terraform init
terraform validate
terraform plan
terraform apply
```

---

## Requirements

- Python ≥ 3.11
- Terraform ≥ 1.6
- Google Cloud SDK (`gcloud`) — only needed for `--project` mode
- IAM role `roles/cloudasset.viewer` on the target project
