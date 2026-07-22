# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

resource "juju_application" "ceph_dashboard" {
  # Juju provider v1 uses model UUIDs rather than model names for targeting.
  name       = var.app_name
  model_uuid = var.model_uuid

  charm {
    name     = "ceph-dashboard"
    channel  = var.channel
    revision = var.revision
    base     = var.base
  }

  # ceph-dashboard is a subordinate charm: it carries no units of its own and
  # is colocated with a principal application via the dashboard relation. The
  # units argument is intentionally omitted for subordinate applications.
  config      = var.config
  constraints = var.constraints
  resources   = var.resources
}
