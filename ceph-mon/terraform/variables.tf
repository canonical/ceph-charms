# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

variable "app_name" {
  description = "Name of the application in the Juju model."
  type        = string
  default     = "ceph-mon"
}

variable "base" {
  description = "Ubuntu bases to deploy the charm onto"
  type        = string
  default     = "ubuntu@24.04"
}

variable "channel" {
  description = "The channel to use when deploying a charm."
  type        = string
  default     = "squid/beta"
}

variable "resources" {
  description = "Resources to use with the application."
  type        = map(string)
  default     = {}
}

variable "revision" {
  description = "Revision number of the charm"
  type        = number
  default     = null
}

variable "units" {
  description = "Number of units to deploy"
  type        = number
  default     = 1
}

variable "config" {
  description = "Application config. Details about available options can be found at https://charmhub.io/ceph-mon/configurations."
  type        = map(string)
  default     = {}
}

variable "constraints" {
  description = "Juju constraints to apply for this application."
  type        = string
  default     = "arch=amd64"
}

variable "model" {
  description = "Reference to a `juju_model`."
  type        = string
}
