# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

terraform {
  required_version = ">= 1.6"
  required_providers {
    juju = {
      source  = "juju/juju"
      version = "~> 0.14.0"
    }
  }
}

provider "juju" {}

variable "manifest_yaml" {
  description = "Path to the manifest YAML file"
  type        = string
}

variable "model" {
  description = "Name of the model to deploy to"
  type        = string
  default     = "ceph-model"
}

module "ceph" {
  source        = "../../terraform"
  model         = var.model
  manifest_yaml = var.manifest_yaml
}
