# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

locals {
  optional_charm_exposure_enabled = {
    ceph_dashboard  = var.ceph_dashboard != null
    ceph_fs         = var.ceph_fs != null
    ceph_nvme       = var.ceph_nvme != null
    ceph_proxy      = var.ceph_proxy != null
    ceph_rbd_mirror = var.ceph_rbd_mirror != null
  }

  disabled_charm_exposures = flatten([
    for charm, enabled in local.optional_charm_exposure_enabled : [
      for key in var.expose_endpoints : key
      if startswith(key, "${charm}_") && !enabled
    ]
  ])
}

resource "terraform_data" "validate_optional_charm_exposures" {
  count = length(local.disabled_charm_exposures) == 0 ? 0 : 1
  input = true

  lifecycle {
    precondition {
      condition     = length(local.disabled_charm_exposures) == 0
      error_message = "expose_endpoints cannot reference an optional charm unless that charm is enabled."
    }
  }
}
