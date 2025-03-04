# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

variable "manifest" {
  description = "Absolute path to a yaml file with config for a Juju application."
  type        = string
}

variable "app" {
  description = "Name of the application to load config for."
  type        = string
}
