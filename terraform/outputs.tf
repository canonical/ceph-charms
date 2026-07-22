# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

output "components" {
  description = "List of objects representing the deployed component applications."
  value = concat(
    [
      module.ceph_mon.application,
      module.ceph_osd.application,
      module.ceph_radosgw.application,
    ],
    var.ceph_fs == null ? [] : [module.ceph_fs[0].application],
    var.ceph_nfs == null ? [] : [module.ceph_nfs[0].application],
    var.ceph_nvme == null ? [] : [module.ceph_nvme[0].application],
    var.ceph_rbd_mirror == null ? [] : [module.ceph_rbd_mirror[0].application],
    var.ceph_dashboard == null ? [] : [module.ceph_dashboard[0].application],
    var.ceph_proxy == null ? [] : [module.ceph_proxy[0].application],
  )
}

output "components_map" {
  description = "Map of deployed component applications keyed by component name."
  value = merge(
    {
      ceph_mon     = module.ceph_mon.application
      ceph_osd     = module.ceph_osd.application
      ceph_radosgw = module.ceph_radosgw.application
    },
    var.ceph_fs == null ? {} : { ceph_fs = module.ceph_fs[0].application },
    var.ceph_nfs == null ? {} : { ceph_nfs = module.ceph_nfs[0].application },
    var.ceph_nvme == null ? {} : { ceph_nvme = module.ceph_nvme[0].application },
    var.ceph_rbd_mirror == null ? {} : { ceph_rbd_mirror = module.ceph_rbd_mirror[0].application },
    var.ceph_dashboard == null ? {} : { ceph_dashboard = module.ceph_dashboard[0].application },
    var.ceph_proxy == null ? {} : { ceph_proxy = module.ceph_proxy[0].application },
  )
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
