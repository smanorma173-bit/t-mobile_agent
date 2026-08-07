"""
generator/terraform_gen.py
Writes all Terraform files deterministically.

Key improvement over v1:
  The root terraform_output/ no longer has a monolithic main.tf.
  Instead resources are split into focused files:
    - vpcs.tf        : module "vpc" call
    - subnets.tf     : module "subnet" call
    - firewalls.tf   : module "firewall" call
    - routers.tf     : module "router" call
    - addresses.tf   : module "address" call
    - negs.tf        : module "neg" / Network Endpoint Groups call

  Shared config lives where it always did:
    - providers.tf, backend.tf, variables.tf, outputs.tf, terraform.tfvars
"""

from pathlib import Path
from agent.config import OUTPUT_DIR
from agent.utils import write
from agent.generator.tfvars_writer import generate as gen_tfvars

# ── Root: providers.tf ────────────────────────────────────────────────────

_PROVIDERS_TF = """\
terraform {
  required_version = ">= 1.6"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}
"""

# ── Root: backend.tf ──────────────────────────────────────────────────────

_BACKEND_TF = """\
# backend.tf — GCS remote state.
# Create the bucket once before `terraform init`:
#   gsutil mb -p {project_id} gs://{project_id}-tfstate
terraform {{
  backend "gcs" {{
    bucket = "{project_id}-tfstate"
    prefix = "network/terraform.tfstate"
  }}
}}
"""

# ── Root: split module-call files ─────────────────────────────────────────

_VPCS_TF = """\
# vpcs.tf — VPC network resources.
# No dependencies on other modules.

module "vpc" {
  source = "./modules/vpc"
  vpcs   = var.vpcs
}
"""

_SUBNETS_TF = """\
# subnets.tf — Subnet resources.
# Depends on: module.vpc (for network self_links)

module "subnet" {
  source         = "./modules/subnet"
  subnets        = var.subnets
  vpc_self_links = module.vpc.network_self_link
}
"""

_FIREWALLS_TF = """\
# firewalls.tf — Firewall rules.
# Depends on: module.vpc (for network self_links)

module "firewall" {
  source         = "./modules/firewall"
  firewalls      = var.firewalls
  vpc_self_links = module.vpc.network_self_link
}
"""

_ROUTERS_TF = """\
# routers.tf — Cloud Routers (and implicitly Cloud NAT).
# Depends on: module.vpc (for network self_links)

module "router" {
  source         = "./modules/router"
  routers        = var.routers
  vpc_self_links = module.vpc.network_self_link
}
"""

_ADDRESSES_TF = """\
# addresses.tf — Static IP addresses (global and regional).
# No dependencies on other modules.

module "address" {
  source    = "./modules/address"
  addresses = var.addresses
}
"""

_NEGS_TF = """\
# negs.tf — Network Endpoint Groups.
# Depends on: module.vpc (for network self_links)

module "neg" {
  source         = "./modules/neg"
  negs           = var.negs
  vpc_self_links = module.vpc.network_self_link
}
"""

# ── Root: variables.tf ────────────────────────────────────────────────────

_VARIABLES_TF = """\
variable "project_id" {
  description = "GCP project ID."
  type        = string
}

variable "region" {
  description = "Default GCP region."
  type        = string
  default     = "us-central1"
}

variable "vpcs" {
  description = "Map of VPC networks. Key = network name."
  type = map(object({
    project                 = string
    auto_create_subnetworks = optional(bool, false)
    description             = optional(string, null)
  }))
  default = {}
}

variable "subnets" {
  description = "Map of subnets. Key = subnet name."
  type = map(object({
    project                  = string
    region                   = string
    ip_cidr_range            = string
    parent_vpc               = string
    private_ip_google_access = optional(bool, true)
    description              = optional(string, null)
  }))
  default = {}
}

variable "firewalls" {
  description = "Map of firewall rules. Key = rule name."
  type = map(object({
    project       = string
    parent_vpc    = string
    direction     = optional(string, "INGRESS")
    priority      = optional(number, 1000)
    description   = optional(string, null)
    source_ranges = optional(list(string), null)
    target_tags   = optional(list(string), null)
    allow = optional(list(object({
      protocol = string
      ports    = optional(list(string), [])
    })), [])
    deny = optional(list(object({
      protocol = string
      ports    = optional(list(string), [])
    })), [])
  }))
  default = {}
}

variable "routers" {
  description = "Map of Cloud Routers. Key = router name."
  type = map(object({
    project    = string
    region     = string
    parent_vpc = string
  }))
  default = {}
}

variable "addresses" {
  description = "Map of IP addresses (global and regional). Key = address name."
  type = map(object({
    project = string
    region  = string
    scope   = string
    address = optional(string, null)
  }))
  default = {}
}

variable "negs" {
  description = "Map of user-managed NEGs. Auto-managed GKE NEGs (k8s1-*, k8s2-*, gke-*) are excluded."
  type = map(object({
    project               = string
    zone                  = string
    parent_vpc            = string
    network_endpoint_type = optional(string, "GCE_VM_IP_PORT")
  }))
  default = {}
}
"""

# ── Root: outputs.tf ──────────────────────────────────────────────────────

_OUTPUTS_TF = """\
output "vpc_self_links" {
  description = "VPC self_links keyed by name."
  value       = module.vpc.network_self_link
}

output "subnet_self_links" {
  description = "Subnet self_links keyed by name."
  value       = module.subnet.subnet_self_link
}

output "firewall_self_links" {
  description = "Firewall self_links keyed by name."
  value       = module.firewall.firewall_self_link
}

output "router_self_links" {
  description = "Router self_links keyed by name."
  value       = module.router.router_self_link
}

output "global_address_self_links" {
  description = "Global address self_links keyed by name."
  value       = module.address.global_address_self_link
}

output "regional_address_self_links" {
  description = "Regional address self_links keyed by name."
  value       = module.address.regional_address_self_link
}

output "neg_self_links" {
  description = "NEG self_links keyed by name."
  value       = module.neg.neg_self_link
}
"""

# ── Module: vpc ───────────────────────────────────────────────────────────

_VPC_MAIN = """\
resource "google_compute_network" "this" {
  for_each                = var.vpcs
  name                    = each.key
  project                 = each.value.project
  auto_create_subnetworks = each.value.auto_create_subnetworks
  description             = each.value.description
}
"""

_VPC_VARS = """\
variable "vpcs" {
  description = "Map of VPC networks. Key = network name."
  type = map(object({
    project                 = string
    auto_create_subnetworks = optional(bool, false)
    description             = optional(string, null)
  }))
}
"""

_VPC_OUTPUTS = """\
output "network_self_link" {
  value = { for k, v in google_compute_network.this : k => v.self_link }
}
output "network_name" {
  value = { for k, v in google_compute_network.this : k => v.name }
}
"""

# ── Module: subnet ────────────────────────────────────────────────────────

_SUBNET_MAIN = """\
resource "google_compute_subnetwork" "this" {
  for_each                 = var.subnets
  name                     = each.key
  project                  = each.value.project
  region                   = each.value.region
  ip_cidr_range            = each.value.ip_cidr_range
  network                  = var.vpc_self_links[each.value.parent_vpc]
  private_ip_google_access = each.value.private_ip_google_access
  description              = each.value.description
}
"""

_SUBNET_VARS = """\
variable "subnets" {
  type = map(object({
    project                  = string
    region                   = string
    ip_cidr_range            = string
    parent_vpc               = string
    private_ip_google_access = optional(bool, true)
    description              = optional(string, null)
  }))
}
variable "vpc_self_links" {
  description = "From module.vpc.network_self_link"
  type        = map(string)
}
"""

_SUBNET_OUTPUTS = """\
output "subnet_self_link" {
  value = { for k, v in google_compute_subnetwork.this : k => v.self_link }
}
output "subnet_name" {
  value = { for k, v in google_compute_subnetwork.this : k => v.name }
}
"""

# ── Module: firewall ──────────────────────────────────────────────────────

_FIREWALL_MAIN = """\
locals {
  # Firewalls with no allow/deny rules: inject a placeholder allow-all so
  # Terraform does not reject the resource. Review and tighten after import.
  firewalls_normalised = {
    for k, v in var.firewalls : k => merge(v, {
      allow = (length(v.allow) == 0 && length(v.deny) == 0) ? [
        { protocol = "all", ports = [] }
      ] : v.allow
      deny = (length(v.allow) == 0 && length(v.deny) == 0) ? [] : v.deny
    })
  }
}

resource "google_compute_firewall" "this" {
  for_each    = local.firewalls_normalised
  name        = each.key
  project     = each.value.project
  network     = var.vpc_self_links[each.value.parent_vpc]
  direction   = each.value.direction
  priority    = each.value.priority
  description = each.value.description

  source_ranges = each.value.direction == "INGRESS" ? (
    each.value.source_ranges != null ? each.value.source_ranges : ["0.0.0.0/0"]
  ) : null

  target_tags = each.value.target_tags

  dynamic "allow" {
    for_each = each.value.allow
    content {
      protocol = allow.value.protocol
      ports    = allow.value.ports
    }
  }

  dynamic "deny" {
    for_each = each.value.deny
    content {
      protocol = deny.value.protocol
      ports    = deny.value.ports
    }
  }

  lifecycle {
    # Rule details (ports, protocols, ranges) are managed in tfvars.
    # Prevent accidental drift if imported from existing rules.
    ignore_changes = [description]
  }
}
"""

_FIREWALL_VARS = """\
variable "firewalls" {
  type = map(object({
    project       = string
    parent_vpc    = string
    direction     = optional(string, "INGRESS")
    priority      = optional(number, 1000)
    description   = optional(string, null)
    source_ranges = optional(list(string), null)
    target_tags   = optional(list(string), null)
    allow = optional(list(object({
      protocol = string
      ports    = optional(list(string), [])
    })), [])
    deny = optional(list(object({
      protocol = string
      ports    = optional(list(string), [])
    })), [])
  }))
}
variable "vpc_self_links" {
  description = "From module.vpc.network_self_link"
  type        = map(string)
}
"""

_FIREWALL_OUTPUTS = """\
output "firewall_self_link" {
  value = { for k, v in google_compute_firewall.this : k => v.self_link }
}
"""

# ── Module: router ────────────────────────────────────────────────────────

_ROUTER_MAIN = """\
resource "google_compute_router" "this" {
  for_each = var.routers
  name     = each.key
  project  = each.value.project
  region   = each.value.region
  network  = var.vpc_self_links[each.value.parent_vpc]
}
"""

_ROUTER_VARS = """\
variable "routers" {
  type = map(object({
    project    = string
    region     = string
    parent_vpc = string
  }))
}
variable "vpc_self_links" {
  description = "From module.vpc.network_self_link"
  type        = map(string)
}
"""

_ROUTER_OUTPUTS = """\
output "router_self_link" {
  value = { for k, v in google_compute_router.this : k => v.self_link }
}
"""

# ── Module: address ───────────────────────────────────────────────────────

_ADDRESS_MAIN = """\
resource "google_compute_global_address" "this" {
  for_each = { for k, v in var.addresses : k => v if v.scope == "global" }
  name     = each.key
  project  = each.value.project
}

resource "google_compute_address" "this" {
  for_each = { for k, v in var.addresses : k => v if v.scope == "regional" }
  name     = each.key
  project  = each.value.project
  region   = each.value.region
  address  = each.value.address != null && each.value.address != "" ? each.value.address : null
}
"""

_ADDRESS_VARS = """\
variable "addresses" {
  type = map(object({
    project = string
    region  = string
    scope   = string
    address = optional(string, null)
  }))
}
"""

_ADDRESS_OUTPUTS = """\
output "global_address_self_link" {
  value = { for k, v in google_compute_global_address.this : k => v.self_link }
}
output "regional_address_self_link" {
  value = { for k, v in google_compute_address.this : k => v.self_link }
}
"""

# ── Module: neg ───────────────────────────────────────────────────────────

_NEG_MAIN = """\
resource "google_compute_network_endpoint_group" "this" {
  for_each              = var.negs
  name                  = each.key
  project               = each.value.project
  zone                  = each.value.zone
  network               = var.vpc_self_links[each.value.parent_vpc]
  network_endpoint_type = each.value.network_endpoint_type
}
"""

_NEG_VARS = """\
variable "negs" {
  type = map(object({
    project               = string
    zone                  = string
    parent_vpc            = string
    network_endpoint_type = optional(string, "GCE_VM_IP_PORT")
  }))
}
variable "vpc_self_links" {
  description = "From module.vpc.network_self_link"
  type        = map(string)
}
"""

_NEG_OUTPUTS = """\
output "neg_self_link" {
  value = { for k, v in google_compute_network_endpoint_group.this : k => v.self_link }
}
"""


# ── Orchestrator ──────────────────────────────────────────────────────────

def run(inventory: dict) -> None:
    o   = OUTPUT_DIR
    pid = inventory["project_id"]

    print("\n  📝  Writing root configuration files ...")
    _w(o / "providers.tf",     _PROVIDERS_TF)
    _w(o / "backend.tf",       _BACKEND_TF.format(project_id=pid))
    _w(o / "variables.tf",     _VARIABLES_TF)
    _w(o / "outputs.tf",       _OUTPUTS_TF)
    _w(o / "terraform.tfvars", gen_tfvars(inventory))

    print("\n  📝  Writing resource-specific module-call files ...")
    _w(o / "vpcs.tf",      _VPCS_TF)
    _w(o / "subnets.tf",   _SUBNETS_TF)
    _w(o / "firewalls.tf", _FIREWALLS_TF)
    _w(o / "routers.tf",   _ROUTERS_TF)
    _w(o / "addresses.tf", _ADDRESSES_TF)
    _w(o / "negs.tf",      _NEGS_TF)

    print("\n  📝  Writing module implementations ...")
    _w(o / "modules/vpc/main.tf",         _VPC_MAIN)
    _w(o / "modules/vpc/variables.tf",    _VPC_VARS)
    _w(o / "modules/vpc/outputs.tf",      _VPC_OUTPUTS)

    _w(o / "modules/subnet/main.tf",      _SUBNET_MAIN)
    _w(o / "modules/subnet/variables.tf", _SUBNET_VARS)
    _w(o / "modules/subnet/outputs.tf",   _SUBNET_OUTPUTS)

    _w(o / "modules/firewall/main.tf",      _FIREWALL_MAIN)
    _w(o / "modules/firewall/variables.tf", _FIREWALL_VARS)
    _w(o / "modules/firewall/outputs.tf",   _FIREWALL_OUTPUTS)

    _w(o / "modules/router/main.tf",      _ROUTER_MAIN)
    _w(o / "modules/router/variables.tf", _ROUTER_VARS)
    _w(o / "modules/router/outputs.tf",   _ROUTER_OUTPUTS)

    _w(o / "modules/address/main.tf",      _ADDRESS_MAIN)
    _w(o / "modules/address/variables.tf", _ADDRESS_VARS)
    _w(o / "modules/address/outputs.tf",   _ADDRESS_OUTPUTS)

    _w(o / "modules/neg/main.tf",      _NEG_MAIN)
    _w(o / "modules/neg/variables.tf", _NEG_VARS)
    _w(o / "modules/neg/outputs.tf",   _NEG_OUTPUTS)


def _w(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.strip() + "\n", encoding="utf-8")
    print(f"    ✅  {path.relative_to(OUTPUT_DIR)}")
