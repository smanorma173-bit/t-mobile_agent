"""
generator/tfvars_writer.py
Writes terraform.tfvars deterministically from the inventory dict.
No LLM involved — pure Python string formatting guarantees valid HCL.
"""

import json


def generate(inventory: dict) -> str:
    """Return the full content of terraform.tfvars as a string."""
    lines = []
    pid = inventory["project_id"]

    lines += [f'project_id = "{pid}"', 'region     = "us-central1"', ""]

    lines += _map_block("vpcs",       inventory["vpcs"],       _vpc_obj)
    lines += _map_block("subnets",    inventory["subnets"],    _subnet_obj)
    lines += _map_block("firewalls",  inventory["firewalls"],  _firewall_obj)
    lines += _map_block("routers",    inventory["routers"],    _router_obj)
    lines += _map_block("addresses",  inventory["addresses"],  _address_obj)
    lines += _map_block("negs",       inventory["negs"],       _neg_obj)

    return "\n".join(lines)


# ── Block formatters ──────────────────────────────────────────────────────────

def _map_block(var_name: str, items: list, formatter) -> list:
    if not items:
        return [f"{var_name} = {{}}", ""]
    lines = [f"{var_name} = {{"]
    for item in items:
        key = item["name"]
        lines.append(f"  {key} = {{")
        lines += formatter(item)
        lines.append("  }")
    lines += ["}", ""]
    return lines


def _vpc_obj(v: dict) -> list:
    return [
        f'    project                 = "{v["project"]}"',
        f'    auto_create_subnetworks = false',
    ]


def _subnet_obj(v: dict) -> list:
    return [
        f'    project       = "{v["project"]}"',
        f'    region        = "{v["region"]}"',
        f'    ip_cidr_range = "{v["ip_cidr_range"]}"',
        f'    parent_vpc    = "{v["parent_vpc"]}"',
    ]


def _firewall_obj(v: dict) -> list:
    lines = [
        f'    project    = "{v["project"]}"',
        f'    parent_vpc = "{v["parent_vpc"]}"',
        f'    direction  = "{v.get("direction", "INGRESS")}"',
    ]

    # source_ranges
    src = v.get("source_ranges")
    if src and isinstance(src, list):
        quoted = ", ".join(f'"{r}"' for r in src)
        lines.append(f'    source_ranges = [{quoted}]')

    # target_tags
    tags = v.get("target_tags")
    if tags and isinstance(tags, list):
        quoted = ", ".join(f'"{t}"' for t in tags)
        lines.append(f'    target_tags = [{quoted}]')

    # allow rules
    allow = v.get("allow", [])
    if allow:
        lines.append("    allow = [")
        for rule in allow:
            proto = rule.get("protocol", "all")
            ports = rule.get("ports", [])
            ports_hcl = ", ".join(f'"{p}"' for p in ports)
            lines.append("      {")
            lines.append(f'        protocol = "{proto}"')
            lines.append(f'        ports    = [{ports_hcl}]')
            lines.append("      },")
        lines.append("    ]")
    else:
        lines.append("    allow = []")

    # deny rules
    deny = v.get("deny", [])
    if deny:
        lines.append("    deny = [")
        for rule in deny:
            proto = rule.get("protocol", "all")
            ports = rule.get("ports", [])
            ports_hcl = ", ".join(f'"{p}"' for p in ports)
            lines.append("      {")
            lines.append(f'        protocol = "{proto}"')
            lines.append(f'        ports    = [{ports_hcl}]')
            lines.append("      },")
        lines.append("    ]")
    else:
        lines.append("    deny = []")

    return lines


def _router_obj(v: dict) -> list:
    return [
        f'    project    = "{v["project"]}"',
        f'    region     = "{v["region"]}"',
        f'    parent_vpc = "{v["parent_vpc"]}"',
    ]


def _address_obj(v: dict) -> list:
    lines = [
        f'    project = "{v["project"]}"',
        f'    region  = "{v["region"]}"',
        f'    scope   = "{v["scope"]}"',
    ]
    if v.get("address"):
        lines.append(f'    address = "{v["address"]}"')
    return lines


def _neg_obj(v: dict) -> list:
    return [
        f'    project    = "{v["project"]}"',
        f'    zone       = "{v["zone"]}"',
        f'    parent_vpc = "{v["parent_vpc"]}"',
    ]
