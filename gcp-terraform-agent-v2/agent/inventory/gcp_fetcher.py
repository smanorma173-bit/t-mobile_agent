"""
inventory/gcp_fetcher.py
Fetches GCP network resources directly from the Cloud Asset Inventory API
using the `gcloud` CLI. No CSV/Excel upload required — just pass --project.

Requirements:
  - gcloud CLI installed and authenticated (gcloud auth login / ADC)
  - The caller's account must have roles/cloudasset.viewer on the project.

Supported resource types (same as the CSV-based loader):
  compute.Network, compute.Subnetwork, compute.Firewall,
  compute.Router, compute.Address, compute.NetworkEndpointGroup
"""

import json
import re
import subprocess
from typing import Any

ASSET_TYPES = [
    "compute.googleapis.com/Network",
    "compute.googleapis.com/Subnetwork",
    "compute.googleapis.com/Firewall",
    "compute.googleapis.com/Router",
    "compute.googleapis.com/Address",
    "compute.googleapis.com/NetworkEndpointGroup",
]

# Short type name → canonical loader key
_TYPE_MAP = {
    "Network":               "compute.Network",
    "Subnetwork":            "compute.Subnetwork",
    "Firewall":              "compute.Firewall",
    "Router":                "compute.Router",
    "Address":               "compute.Address",
    "NetworkEndpointGroup":  "compute.NetworkEndpointGroup",
}


def fetch(project_id: str) -> list[dict]:
    """
    Call Cloud Asset Inventory for `project_id` and return a list of
    normalised rows that look exactly like what loader.py expects.

    The raw JSON and the normalised CSV are both saved to:
      inventory_cache/<project_id>_raw.json
      inventory_cache/<project_id>_inventory.csv
    """
    import csv
    from pathlib import Path

    print(f"  🔍  Fetching GCP assets for project: {project_id} ...")
    raw = _call_gcloud(project_id)

    # ── Save raw JSON ───────────────────────────────────────────────────
    cache_dir = Path(__file__).parent.parent.parent / "inventory_cache"
    cache_dir.mkdir(exist_ok=True)
    raw_path = cache_dir / f"{project_id}_raw.json"
    raw_path.write_text(json.dumps(raw, indent=2), encoding="utf-8")
    print(f"  💾  Raw asset JSON saved → {raw_path}")

    rows = []
    for asset in raw:
        row = _asset_to_row(asset, project_id)
        if row:
            rows.append(row)

    # ── Save normalised CSV ─────────────────────────────────────────────
    if rows:
        csv_path = cache_dir / f"{project_id}_inventory.csv"
        fieldnames = ["Name", "Resource type", "Project Id", "Location", "Additional attributes"]
        with open(csv_path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        print(f"  💾  Normalised inventory CSV saved → {csv_path}")

    print(f"  📋  Retrieved {len(rows)} network resources from Cloud Asset API.")
    return rows


# ── gcloud call ────────────────────────────────────────────────────────────

def _call_gcloud(project_id: str) -> list[dict]:
    asset_type_flags = ",".join(ASSET_TYPES)
    cmd = [
        "gcloud", "asset", "list",
        f"--project={project_id}",
        f"--asset-types={asset_type_flags}",
        "--content-type=resource",
        "--format=json",
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, check=True
        )
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"gcloud asset list failed:\n{exc.stderr.strip()}\n\n"
            "Make sure:\n"
            "  1. gcloud CLI is installed and on PATH.\n"
            "  2. You are authenticated: gcloud auth application-default login\n"
            "  3. Cloud Asset API is enabled: "
            f"gcloud services enable cloudasset.googleapis.com --project={project_id}\n"
            f"  4. Your account has roles/cloudasset.viewer on project {project_id}."
        ) from exc
    except FileNotFoundError:
        raise RuntimeError(
            "gcloud CLI not found. Install Google Cloud SDK:\n"
            "  https://cloud.google.com/sdk/docs/install"
        )

    try:
        return json.loads(result.stdout) or []
    except json.JSONDecodeError:
        raise RuntimeError(
            f"Failed to parse gcloud output as JSON:\n{result.stdout[:500]}"
        )


# ── Asset → row ────────────────────────────────────────────────────────────

def _asset_to_row(asset: dict, project_id: str) -> dict | None:
    """Convert a Cloud Asset Inventory entry to a loader-compatible row."""
    asset_type = asset.get("assetType", "")          # e.g. compute.googleapis.com/Network
    resource   = asset.get("resource", {})
    data       = resource.get("data", {})
    name_full  = asset.get("name", "")               # //compute.googleapis.com/projects/.../networks/my-vpc

    # Derive short type name
    short_type = asset_type.split("/")[-1]            # e.g. "Network"
    canonical  = _TYPE_MAP.get(short_type)
    if not canonical:
        return None

    # Resource name (last path segment)
    res_name = data.get("name") or name_full.split("/")[-1]

    # Location — extract from selfLink or name path
    location = _extract_location(data, name_full, canonical)

    # Additional attributes (gateway for subnets, address for IPs, etc.)
    extra = _extract_attrs(data, canonical)

    return {
        "Name":                  res_name,
        "Resource type":         canonical,
        "Project Id":            data.get("projectId") or project_id,
        "Location":              location,
        "Additional attributes": extra,
    }


def _extract_location(data: dict, name_full: str, rtype: str) -> str:
    """Infer region/zone/global from resource data or asset name."""
    # Addresses and Routers have a 'region' field
    region = data.get("region", "")
    if region:
        return region.split("/")[-1]

    # Subnets
    if rtype == "compute.Subnetwork":
        region = data.get("region", "")
        if region:
            return region.split("/")[-1]

    # NEGs have a 'zone' field
    zone = data.get("zone", "")
    if zone:
        return zone.split("/")[-1]

    # Parse from the asset name path: .../regions/us-central1/...
    m = re.search(r"/regions/([^/]+)", name_full)
    if m:
        return m.group(1)
    m = re.search(r"/zones/([^/]+)", name_full)
    if m:
        return m.group(1)

    return "global"


def _extract_attrs(data: dict, rtype: str) -> str:
    """Return a JSON string of relevant extra attributes for each resource type."""
    attrs: dict[str, Any] = {}
    if rtype == "compute.Subnetwork":
        gw = data.get("gatewayAddress", "")
        if gw:
            attrs["gatewayAddress"] = gw
    elif rtype == "compute.Address":
        addr = data.get("address", "")
        if addr:
            attrs["address"] = addr
    elif rtype == "compute.Firewall":
        # Preserve allow/deny/direction/sourceRanges for accurate tfvars generation.
        for field in ("allowed", "denied", "direction", "sourceRanges",
                      "targetTags", "priority", "description"):
            val = data.get(field)
            if val is not None:
                attrs[field] = val
    return json.dumps(attrs) if attrs else ""
