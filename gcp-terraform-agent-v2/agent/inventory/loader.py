"""
inventory/loader.py
Parses GCP asset inventory → normalised dict for Terraform generation.
Supports two input modes:
  1. CSV file path (str | Path) — legacy mode, same as before.
  2. List of row dicts — produced by gcp_fetcher.fetch() for live API mode.
Excluded:
  compute.Route — all routes in this project are GCP-managed system routes
  (default-route-* / peering-route-*). They have no usable next_hop data
  and cannot be represented as google_compute_route resources.
"""
import re
import json
import pandas as pd
from pathlib import Path
NETWORK_TYPES = {
    "compute.Network",
    "compute.Subnetwork",
    "compute.Firewall",
    "compute.Router",
    "compute.Address",
    "compute.NetworkEndpointGroup",
}

def load(source: "str | Path | list[dict]") -> dict:
    """
    Accept either:
      - a CSV file path  → parse with pandas
      - a list of dicts  → already normalised rows from gcp_fetcher
    Returns the standard inventory dict.
    """
    if isinstance(source, list):
        rows_df = pd.DataFrame(source)
    else:
        df = pd.read_csv(source)
        rows_df = df[df["Resource type"].isin(NETWORK_TYPES)].copy()
    rows_df = rows_df[rows_df["Resource type"].isin(NETWORK_TYPES)].copy()
    project_id = (
        rows_df["Project Id"].dropna().iloc[0]
        if not rows_df["Project Id"].dropna().empty
        else "my-project"
    )
    vpcs:      list = []
    subnets:   list = []
    firewalls: list = []
    routers:   list = []
    addresses: list = []
    negs:      list = []
    seen: dict[str, set] = {k: set() for k in
                            ["vpc", "subnet", "firewall", "router", "address", "neg"]}
    for _, row in rows_df.iterrows():
        base  = _name(str(row["Name"]))
        rtype = row["Resource type"]
        loc   = str(row.get("Location", "global")).strip()
        attrs = _parse_attrs(row.get("Additional attributes", ""))
        proj  = str(row.get("Project Id", "")).strip()
        vpc_names = [v["name"] for v in vpcs]
        # if rtype == "compute.Network":
        #    key = _key(base, seen["vpc"])
        #    vpcs.append({"name": key, "project": proj,
        #                 "auto_create_subnetworks": False})
        if rtype == "compute.Network":
            key = _key(base, seen["vpc"])
            auto_create = str(
                attrs.get("autoCreateSubnetworks", "false")
            ).strip().lower() == "true"
            vpcs.append({
                "name": key,
                "project": proj,
                "auto_create_subnetworks": auto_create
            })

        elif rtype == "compute.Subnetwork":
           # cidr = _gw_to_cidr(attrs.get("gatewayAddress", ""))
            cidr = _gw_to_cidr2(attrs.get("cidr", ""))
            key  = _key(base, seen["subnet"], loc)
            subnets.append({
                "name": key, "project": proj, "region": loc,
                "ip_cidr_range": cidr or "10.0.0.0/24",
                "parent_vpc": _parent(base, vpc_names),
            })
        elif rtype == "compute.Firewall":
            key = _key(base, seen["firewall"])
            allow_rules = _extract_fw_rules(attrs, "allowed")
            deny_rules  = _extract_fw_rules(attrs, "denied")
            direction   = str(attrs.get("direction", "INGRESS")).upper()
            src_ranges  = attrs.get("sourceRanges") or attrs.get("source_ranges") or None
            tgt_tags    = attrs.get("targetTags") or attrs.get("target_tags") or None
            firewalls.append({
                "name":          key,
                "project":       proj,
                "parent_vpc":    _parent(base, vpc_names),
                "direction":     direction,
                "allow":         allow_rules,
                "deny":          deny_rules,
                "source_ranges": src_ranges if isinstance(src_ranges, list) else None,
                "target_tags":   tgt_tags if isinstance(tgt_tags, list) else None,
            })
        elif rtype == "compute.Router":
            key = _key(base, seen["router"], loc)
            routers.append({
                "name": key, "project": proj, "region": loc,
                "parent_vpc": _parent(base, vpc_names),
            })
        elif rtype == "compute.Address":
            scope = "global" if loc == "global" else "regional"
            key   = _key(base, seen["address"], loc)
            addresses.append({
                "name": key, "project": proj, "region": loc, "scope": scope,
                "address": str(attrs.get("address", "") or ""),
            })
        elif rtype == "compute.NetworkEndpointGroup":
            # Skip GKE/GCP auto-managed NEGs — they are created and deleted
            # automatically and must not be managed by Terraform.
            # Patterns: k8s1-*, k8s2-*, gke-*
            if _is_auto_neg(base):
                continue
            key = _key(base, seen["neg"], loc)
            negs.append({
                "name":       key,
                "project":    proj,
                "zone":       loc,
                "parent_vpc": _parent(base, vpc_names),
            })
    return {
        "project_id": project_id,
        "vpcs":       vpcs,
        "subnets":    subnets,
        "firewalls":  firewalls,
        "routers":    routers,
        "addresses":  addresses,
        "negs":       negs,
    }

# ── Helpers ────────────────────────────────────────────────────────────────
def _name(full: str) -> str:
    return full.strip().split("/")[-1]

def _parse_attrs(raw) -> dict:
    if pd.isna(raw) if not isinstance(raw, str) else not raw:
        return {}
    try:
        s = str(raw).strip()
        if s.startswith("{"):
            try:
                return json.loads(s)
            except Exception:
                pass
        s = re.sub(r'(\w+):', r'"\1":', s)
        s = re.sub(r':(\w[\w.]*)', r':"\1"', s)
        return json.loads(s)
    except Exception:
        return {}

def _sanitise(name: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9\-]", "-", name).strip("-")
    if s and s[0].isdigit():
        s = "r-" + s
    return s or "resource"

def _key(base: str, seen: set, disambiguator: str = "") -> str:
    candidate = _sanitise(base)
    if candidate not in seen:
        seen.add(candidate)
        return candidate
    if disambiguator:
        slug = _sanitise(disambiguator)
        alt  = f"{candidate}-{slug}"
        if alt not in seen:
            seen.add(alt)
            return alt
    idx = 2
    while True:
        alt = f"{candidate}-{idx}"
        if alt not in seen:
            seen.add(alt)
            return alt
        idx += 1

def _gw_to_cidr(gw: str) -> str:
    parts = (gw or "").split(".")
    return f"{parts[0]}.{parts[1]}.{parts[2]}.0/24" if len(parts) == 4 else ""
def _gw_to_cidr2(gw: str) -> str:
    parts = (gw or "").split(".")
    return f"{parts[0]}.{parts[1]}.{parts[2]}.{parts[3]}" if len(parts) == 4 else ""

def _parent(resource_name: str, vpc_names: list) -> str:
    if resource_name in vpc_names:
        return resource_name
    for vpc in vpc_names:
        if resource_name.startswith(vpc):
            return vpc
    patterns = [
        ("data-vpc",       "data-vpc"),
        ("packer",         "packer-vpc"),
        ("gcve-us-east1",  "gcve-custom-vpc"),
        ("secure",         "secure-vpc"),
        ("gcve",           "gcve-cloud-factory-vpc"),
        ("gcp-vpc-2",      "gcp-vpc-2"),
        ("gcp-vpc",        "gcp-vpc"),
        ("extlb",          "default-vpc"),
        ("my-custom",      "my-custom-vpc-06"),
        ("default",        "default"),
    ]
    for key, vpc in patterns:
        if key in resource_name and vpc in vpc_names:
            return vpc
    return vpc_names[0] if vpc_names else "default"

def _extract_fw_rules(attrs: dict, key: str) -> list:
    """
    Extract allow/deny rules from Cloud Asset Inventory firewall data.
    `key` is "allowed" or "denied".
    Returns list of {protocol, ports} dicts, or [] if none.
    """
    raw = attrs.get(key, [])
    if not isinstance(raw, list):
        return []
    rules = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        proto = str(entry.get("IPProtocol") or entry.get("protocol") or "all")
        ports = entry.get("ports", [])
        rules.append({"protocol": proto, "ports": ports if isinstance(ports, list) else []})
    return rules

def _is_auto_neg(name: str) -> bool:
    """
    Returns True for NEGs that are auto-created and auto-deleted by GKE/GCP.
    These must NOT be managed by Terraform.
    """
    prefixes = ("k8s1-", "k8s2-", "gke-")
    return any(name.lower().startswith(p) for p in prefixes)