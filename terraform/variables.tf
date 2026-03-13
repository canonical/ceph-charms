# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

# External integration descriptors share one schema across optional relations
# so callers can switch between in-model endpoints and cross-model offers.
variable "bootstrap_source" {
  description = "Optional external integration to satisfy ceph-mon:bootstrap-source. Use kind=\"endpoint\" for in-model integrations or kind=\"offer\" for cross-model integrations."
  type = object({
    endpoint = optional(string, null)
    kind     = string
    name     = optional(string, null)
    url      = optional(string, null)
  })
  default = null

  validation {
    condition     = var.bootstrap_source == null || contains(["endpoint", "offer"], var.bootstrap_source.kind)
    error_message = "bootstrap_source.kind must be either \"endpoint\" or \"offer\"."
  }

  validation {
    condition = var.bootstrap_source == null || (
      var.bootstrap_source.kind == "offer"
      ? var.bootstrap_source.url != null && var.bootstrap_source.url != ""
      : var.bootstrap_source.name != null && var.bootstrap_source.name != "" && var.bootstrap_source.endpoint != null && var.bootstrap_source.endpoint != ""
    )
    error_message = "bootstrap_source requires url when kind=offer, or both name and endpoint when kind=endpoint."
  }
}

variable "ceph_mon" {
  description = "Configuration object for ceph-mon deployment."
  type = object({
    app_name = optional(string, "ceph-mon")
    base     = optional(string, "ubuntu@24.04")
    channel  = optional(string, "squid/stable")
    config = optional(map(string), {
      "expected-osd-count" = "1"
      "monitor-count"      = "1"
    })
    constraints       = optional(string, null)
    offered_endpoints = optional(list(string), [])
    resources         = optional(map(string), {})
    revision          = optional(number)
    units             = optional(number, 1)
  })
  default = {}

  validation {
    condition = alltrue([
      for alias in var.ceph_mon.offered_endpoints :
      contains(
        [
          "admin",
          "client",
          "cos_agent",
          "dashboard",
          "mds",
          "metrics_endpoint",
          "nrpe_external_master",
          "osd",
          "prometheus",
          "radosgw",
          "rbd_mirror",
        ],
        alias,
      )
    ])
    error_message = "ceph_mon.offered_endpoints must only contain ceph-mon provides aliases."
  }
}

variable "ceph_osd" {
  description = "Configuration object for ceph-osd deployment."
  type = object({
    app_name          = optional(string, "ceph-osd")
    base              = optional(string, "ubuntu@24.04")
    channel           = optional(string, "squid/stable")
    config            = optional(map(string), {})
    constraints       = optional(string, null)
    offered_endpoints = optional(list(string), [])
    resources         = optional(map(string), {})
    revision          = optional(number)
    storage_directives = optional(map(string), {
      "osd-devices"  = "1G,1"
      "osd-journals" = "1G,1"
    })
    units = optional(number, 1)
  })
  default = {}

  validation {
    condition = alltrue([
      for alias in var.ceph_osd.offered_endpoints :
      contains(["nrpe_external_master"], alias)
    ])
    error_message = "ceph_osd.offered_endpoints must only contain ceph-osd provides aliases."
  }
}

variable "ceph_radosgw" {
  description = "Configuration object for ceph-radosgw deployment."
  type = object({
    app_name          = optional(string, "ceph-radosgw")
    base              = optional(string, "ubuntu@24.04")
    channel           = optional(string, "squid/stable")
    config            = optional(map(string), {})
    constraints       = optional(string, null)
    offered_endpoints = optional(list(string), [])
    resources         = optional(map(string), {})
    revision          = optional(number)
    units             = optional(number, 1)
  })
  default = {}

  validation {
    condition = alltrue([
      for alias in var.ceph_radosgw.offered_endpoints :
      contains(
        [
          "gateway",
          "master",
          "nrpe_external_master",
          "object_store",
          "primary",
          "radosgw_user",
          "s3",
        ],
        alias,
      )
    ])
    error_message = "ceph_radosgw.offered_endpoints must only contain ceph-radosgw provides aliases."
  }
}

variable "expose_endpoints" {
  description = "Optional list of provided endpoint keys to expose as offers, using the <charm>_<endpoint_alias> format."
  type        = list(string)
  default     = []

  validation {
    condition = alltrue([
      for key in var.expose_endpoints :
      contains(
        [
          "ceph_mon_admin",
          "ceph_mon_client",
          "ceph_mon_cos_agent",
          "ceph_mon_dashboard",
          "ceph_mon_mds",
          "ceph_mon_metrics_endpoint",
          "ceph_mon_nrpe_external_master",
          "ceph_mon_osd",
          "ceph_mon_prometheus",
          "ceph_mon_radosgw",
          "ceph_mon_rbd_mirror",
          "ceph_osd_nrpe_external_master",
          "ceph_radosgw_gateway",
          "ceph_radosgw_master",
          "ceph_radosgw_nrpe_external_master",
          "ceph_radosgw_object_store",
          "ceph_radosgw_primary",
          "ceph_radosgw_radosgw_user",
          "ceph_radosgw_s3",
        ],
        key,
      )
    ])
    error_message = "expose_endpoints entries must be valid <charm>_<endpoint_alias> keys from this module's provides output."
  }
}

variable "model_name" {
  description = "Name of an existing Juju model where this component is deployed. Used when model_uuid is not set."
  type        = string
  default     = null
}

variable "model_owner" {
  description = "Owner of model_name used for Juju model lookup when model_uuid is not set."
  type        = string
  default     = "admin"
}

variable "model_uuid" {
  description = "UUID of an existing Juju model where this component is deployed. Provide this or model_name/model_owner."
  type        = string
  default     = null
}

# Mirrors bootstrap_source semantics so optional relations are configured in a
# consistent way across charms.
variable "secrets_storage" {
  description = "Optional external integration to satisfy ceph-osd:secrets-storage. Use kind=\"endpoint\" for in-model integrations or kind=\"offer\" for cross-model integrations."
  type = object({
    endpoint = optional(string, null)
    kind     = string
    name     = optional(string, null)
    url      = optional(string, null)
  })
  default = null

  validation {
    condition     = var.secrets_storage == null || contains(["endpoint", "offer"], var.secrets_storage.kind)
    error_message = "secrets_storage.kind must be either \"endpoint\" or \"offer\"."
  }

  validation {
    condition = var.secrets_storage == null || (
      var.secrets_storage.kind == "offer"
      ? var.secrets_storage.url != null && var.secrets_storage.url != ""
      : var.secrets_storage.name != null && var.secrets_storage.name != "" && var.secrets_storage.endpoint != null && var.secrets_storage.endpoint != ""
    )
    error_message = "secrets_storage requires url when kind=offer, or both name and endpoint when kind=endpoint."
  }
}
