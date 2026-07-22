# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

resource "juju_application" "ceph_rbd_mirror" {
  # Juju provider v1 uses model UUIDs rather than model names for targeting.
  name       = var.app_name
  model_uuid = var.model_uuid

  charm {
    name     = "ceph-rbd-mirror"
    channel  = var.channel
    revision = var.revision
    base     = var.base
  }

  config      = var.config
  constraints = var.constraints
  resources   = var.resources
  units       = var.units
}
