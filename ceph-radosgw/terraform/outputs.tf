# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

output "application" {
  description = "Object representing the deployed ceph-radosgw application."
  value       = juju_application.ceph_radosgw
}

output "offers" {
  description = "Map of exposed offer URLs keyed by endpoint key."
  value       = local.offers
}

output "provides" {
  description = "Map of provides endpoints keyed by relation alias."
  value       = local.provides
}

output "requires" {
  description = "Map of requires endpoints keyed by relation alias."
  value       = local.requires
}
