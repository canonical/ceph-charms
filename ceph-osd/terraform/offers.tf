# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

resource "juju_offer" "offers" {
  for_each = toset(var.offered_endpoints)

  application_name = juju_application.ceph_osd.name
  endpoints        = [local.provides[each.value]]
  model_uuid       = var.model_uuid
}
