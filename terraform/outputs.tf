# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

output "components" {
  description = "List of objects representing the deployed component applications."
  value = [
    module.ceph_mon.application,
    module.ceph_osd.application,
    module.ceph_radosgw.application,
  ]
}

output "components_map" {
  description = "Map of deployed component applications keyed by component name."
  value = {
    ceph_mon     = module.ceph_mon.application
    ceph_osd     = module.ceph_osd.application
    ceph_radosgw = module.ceph_radosgw.application
  }
}

output "offers" {
  description = "Map of exposed offer URLs keyed by <charm_name>_<endpoint>."
  value       = local.offers
}

output "provides" {
  description = "Map of provided endpoints keyed by <charm_name>_<endpoint>."
  value       = local.provides
}

output "requires" {
  description = "Map of required endpoints keyed by <charm_name>_<endpoint>."
  value       = local.requires
}
