# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

variable "app_name" {
  description = "Name of the application in the Juju model."
  type        = string
  default     = "ceph-mon"
}

variable "base" {
  description = "Operating system base used for deployment, e.g. ubuntu@24.04."
  type        = string
  default     = null
}

variable "channel" {
  description = "Channel of the charm to deploy."
  type        = string
  default     = "squid/stable"
}

variable "config" {
  description = "Application config options for ceph-mon."
  type        = map(string)
  default     = {}
}

variable "constraints" {
  description = "Juju constraints to apply for this application."
  type        = string
  default     = null
}

variable "model_uuid" {
  description = "Reference to an existing Juju model UUID."
  type        = string
  nullable    = false
}

variable "offered_endpoints" {
  description = "List of provided endpoint aliases to expose as Juju offers."
  type        = list(string)
  default     = []

  validation {
    condition = alltrue([
      for alias in var.offered_endpoints :
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
    error_message = "offered_endpoints must only contain ceph-mon provides aliases."
  }
}

variable "resources" {
  description = "Resources to use with the application."
  type        = map(string)
  default     = {}
}

variable "revision" {
  description = "Revision number of the charm to deploy."
  type        = number
  default     = null
}

variable "units" {
  description = "Number of units to deploy."
  type        = number
  default     = 1
}
