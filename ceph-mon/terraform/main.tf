# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

resource "juju_application" "ceph_mon" {
  name  = var.app_name
  model = var.model

  charm {
    name     = "ceph-mon"
    channel  = var.channel
    revision = var.revision
    base     = var.base
  }

  config      = var.config
  constraints = var.constraints
  units       = var.units
  resources   = var.resources
}
