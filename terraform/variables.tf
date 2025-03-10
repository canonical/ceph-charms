# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

variable "manifest_yaml" {
  description = "Absolute path to the manifest yaml file for the charm configurations."
  type        = string
}

variable "model" {
  description = "Name of the Juju model to deploy to."
  type        = string
}
