"""
main.py — Entry point for GCP → Terraform Agent v2.

Usage (live GCP inventory — recommended):
    python -m agent.main --project my-gcp-project-id

Usage (legacy CSV mode):
    python -m agent.main --inventory gcp-asset-inventory.csv

LLM backend (for tfvars generation):
    export ANTHROPIC_API_KEY='sk-ant-...'   # Claude (preferred)
    export GEMINI_API_KEY='AIza...'          # Gemini (fallback)
    export LLM_PROVIDER=claude|gemini|auto   # force a backend (default: auto)

GCP authentication (for --project mode):
    gcloud auth application-default login
    gcloud services enable cloudasset.googleapis.com --project=PROJECT_ID
"""

import argparse
import sys
from agent.config import OUTPUT_DIR
from agent.inventory import load
from agent.generator import run


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate Terraform from a GCP project or asset inventory CSV."
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--project", metavar="PROJECT_ID",
        help="GCP project ID — fetches live inventory via Cloud Asset API (gcloud)."
    )
    source.add_argument(
        "--inventory", metavar="CSV_PATH",
        help="Path to a previously exported gcp-asset-inventory.csv (legacy mode)."
    )
    args = parser.parse_args()

    print("\n🤖  GCP → Terraform Agent v2 starting...\n")

    if args.project:
        from agent.inventory.gcp_fetcher import fetch
        rows = fetch(args.project)
        inventory = load(rows)
    else:
        inventory = load(args.inventory)

    print("\n📦  Inventory loaded:")
    print(f"    Project  : {inventory['project_id']}")
    print(f"    VPCs     : {len(inventory['vpcs'])}")
    print(f"    Subnets  : {len(inventory['subnets'])}")
    print(f"    Firewalls: {len(inventory['firewalls'])}")
    print(f"    Routers  : {len(inventory['routers'])}")
    print(f"    Addresses: {len(inventory['addresses'])}")
    print(f"    NEGs     : {len(inventory['negs'])}")
    print()

    run(inventory)

    print(f"\n✅  Done — files written to: {OUTPUT_DIR}\n")
    print("Terraform output structure:")
    print("  terraform_output/")
    print("  ├── providers.tf      ← provider & terraform block")
    print("  ├── backend.tf        ← GCS remote state")
    print("  ├── variables.tf      ← all variable declarations")
    print("  ├── outputs.tf        ← all output declarations")
    print("  ├── terraform.tfvars  ← generated values")
    print("  ├── vpcs.tf           ← module \"vpc\" call")
    print("  ├── subnets.tf        ← module \"subnet\" call")
    print("  ├── firewalls.tf      ← module \"firewall\" call")
    print("  ├── routers.tf        ← module \"router\" call")
    print("  ├── addresses.tf      ← module \"address\" call")
    print("  ├── negs.tf           ← module \"neg\" call")
    print("  └── modules/")
    print("      ├── vpc/")
    print("      ├── subnet/")
    print("      ├── firewall/")
    print("      ├── router/")
    print("      ├── address/")
    print("      └── neg/")
    print()
    print("Next steps (Cloud Shell / local):")
    pid = inventory["project_id"]
    print(f"  cd terraform_output")
    print(f"  gsutil mb -p {pid} gs://{pid}-tfstate")
    print(f"  terraform init")
    print(f"  terraform validate")
    print(f"  terraform plan")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\nERROR: {e}", file=sys.stderr)
        sys.exit(1)
