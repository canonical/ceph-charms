# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

locals {
  # Normalize top-level offer publication keys into per-charm alias lists.
  ceph_mon_expose_aliases = toset([
    for key in var.expose_endpoints :
    trimprefix(key, "ceph_mon_")
    if startswith(key, "ceph_mon_")
  ])

  ceph_osd_expose_aliases = toset([
    for key in var.expose_endpoints :
    trimprefix(key, "ceph_osd_")
    if startswith(key, "ceph_osd_")
  ])

  ceph_radosgw_expose_aliases = toset([
    for key in var.expose_endpoints :
    trimprefix(key, "ceph_radosgw_")
    if startswith(key, "ceph_radosgw_")
  ])

  ceph_fs_expose_aliases = toset([
    for key in var.expose_endpoints :
    trimprefix(key, "ceph_fs_")
    if startswith(key, "ceph_fs_")
  ])

  ceph_nfs_expose_aliases = toset([
    for key in var.expose_endpoints :
    trimprefix(key, "ceph_nfs_")
    if startswith(key, "ceph_nfs_")
  ])

  ceph_nvme_expose_aliases = toset([
    for key in var.expose_endpoints :
    trimprefix(key, "ceph_nvme_")
    if startswith(key, "ceph_nvme_")
  ])

  ceph_rbd_mirror_expose_aliases = toset([
    for key in var.expose_endpoints :
    trimprefix(key, "ceph_rbd_mirror_")
    if startswith(key, "ceph_rbd_mirror_")
  ])

  ceph_dashboard_expose_aliases = toset([
    for key in var.expose_endpoints :
    trimprefix(key, "ceph_dashboard_")
    if startswith(key, "ceph_dashboard_")
  ])

  ceph_proxy_expose_aliases = toset([
    for key in var.expose_endpoints :
    trimprefix(key, "ceph_proxy_")
    if startswith(key, "ceph_proxy_")
  ])

  ceph_mon_offered_endpoints = sort(tolist(setunion(
    toset(coalesce(var.ceph_mon.offered_endpoints, [])),
    local.ceph_mon_expose_aliases,
  )))

  ceph_osd_offered_endpoints = sort(tolist(setunion(
    toset(coalesce(var.ceph_osd.offered_endpoints, [])),
    local.ceph_osd_expose_aliases,
  )))

  ceph_radosgw_offered_endpoints = sort(tolist(setunion(
    toset(coalesce(var.ceph_radosgw.offered_endpoints, [])),
    local.ceph_radosgw_expose_aliases,
  )))

  ceph_fs_offered_endpoints = sort(tolist(setunion(
    toset(var.ceph_fs == null ? [] : var.ceph_fs.offered_endpoints),
    local.ceph_fs_expose_aliases,
  )))

  ceph_nfs_offered_endpoints = sort(tolist(setunion(
    toset(var.ceph_nfs == null ? [] : var.ceph_nfs.offered_endpoints),
    local.ceph_nfs_expose_aliases,
  )))

  ceph_nvme_offered_endpoints = sort(tolist(setunion(
    toset(var.ceph_nvme == null ? [] : var.ceph_nvme.offered_endpoints),
    local.ceph_nvme_expose_aliases,
  )))

  ceph_rbd_mirror_offered_endpoints = sort(tolist(setunion(
    toset(var.ceph_rbd_mirror == null ? [] : var.ceph_rbd_mirror.offered_endpoints),
    local.ceph_rbd_mirror_expose_aliases,
  )))

  ceph_dashboard_offered_endpoints = sort(tolist(setunion(
    toset(var.ceph_dashboard == null ? [] : var.ceph_dashboard.offered_endpoints),
    local.ceph_dashboard_expose_aliases,
  )))

  ceph_proxy_offered_endpoints = sort(tolist(setunion(
    toset(var.ceph_proxy == null ? [] : var.ceph_proxy.offered_endpoints),
    local.ceph_proxy_expose_aliases,
  )))

  # Keep a stable output contract: relation keys are exposed as
  # <application>_<endpoint_alias> regardless of internal resource names.
  offers = merge(
    {
      for alias, offer_url in module.ceph_mon.offers :
      "ceph_mon_${alias}" => offer_url
    },
    {
      for alias, offer_url in module.ceph_osd.offers :
      "ceph_osd_${alias}" => offer_url
    },
    {
      for alias, offer_url in module.ceph_radosgw.offers :
      "ceph_radosgw_${alias}" => offer_url
    },
    var.ceph_fs == null ? {} : {
      for alias, offer_url in module.ceph_fs[0].offers :
      "ceph_fs_${alias}" => offer_url
    },
    var.ceph_nfs == null ? {} : {
      for alias, offer_url in module.ceph_nfs[0].offers :
      "ceph_nfs_${alias}" => offer_url
    },
    var.ceph_nvme == null ? {} : {
      for alias, offer_url in module.ceph_nvme[0].offers :
      "ceph_nvme_${alias}" => offer_url
    },
    var.ceph_rbd_mirror == null ? {} : {
      for alias, offer_url in module.ceph_rbd_mirror[0].offers :
      "ceph_rbd_mirror_${alias}" => offer_url
    },
    var.ceph_dashboard == null ? {} : {
      for alias, offer_url in module.ceph_dashboard[0].offers :
      "ceph_dashboard_${alias}" => offer_url
    },
    var.ceph_proxy == null ? {} : {
      for alias, offer_url in module.ceph_proxy[0].offers :
      "ceph_proxy_${alias}" => offer_url
    },
  )

  provides = merge(
    {
      for alias, endpoint in module.ceph_mon.provides :
      "ceph_mon_${alias}" => {
        endpoint = endpoint
        name     = module.ceph_mon.application.name
      }
    },
    {
      for alias, endpoint in module.ceph_osd.provides :
      "ceph_osd_${alias}" => {
        endpoint = endpoint
        name     = module.ceph_osd.application.name
      }
    },
    {
      for alias, endpoint in module.ceph_radosgw.provides :
      "ceph_radosgw_${alias}" => {
        endpoint = endpoint
        name     = module.ceph_radosgw.application.name
      }
    },
    var.ceph_fs == null ? {} : {
      for alias, endpoint in module.ceph_fs[0].provides :
      "ceph_fs_${alias}" => {
        endpoint = endpoint
        name     = module.ceph_fs[0].application.name
      }
    },
    var.ceph_nfs == null ? {} : {
      for alias, endpoint in module.ceph_nfs[0].provides :
      "ceph_nfs_${alias}" => {
        endpoint = endpoint
        name     = module.ceph_nfs[0].application.name
      }
    },
    var.ceph_nvme == null ? {} : {
      for alias, endpoint in module.ceph_nvme[0].provides :
      "ceph_nvme_${alias}" => {
        endpoint = endpoint
        name     = module.ceph_nvme[0].application.name
      }
    },
    var.ceph_rbd_mirror == null ? {} : {
      for alias, endpoint in module.ceph_rbd_mirror[0].provides :
      "ceph_rbd_mirror_${alias}" => {
        endpoint = endpoint
        name     = module.ceph_rbd_mirror[0].application.name
      }
    },
    var.ceph_dashboard == null ? {} : {
      for alias, endpoint in module.ceph_dashboard[0].provides :
      "ceph_dashboard_${alias}" => {
        endpoint = endpoint
        name     = module.ceph_dashboard[0].application.name
      }
    },
    var.ceph_proxy == null ? {} : {
      for alias, endpoint in module.ceph_proxy[0].provides :
      "ceph_proxy_${alias}" => {
        endpoint = endpoint
        name     = module.ceph_proxy[0].application.name
      }
    },
  )

  requires = merge(
    {
      for alias, endpoint in module.ceph_mon.requires :
      "ceph_mon_${alias}" => {
        endpoint = endpoint
        name     = module.ceph_mon.application.name
      }
    },
    {
      for alias, endpoint in module.ceph_osd.requires :
      "ceph_osd_${alias}" => {
        endpoint = endpoint
        name     = module.ceph_osd.application.name
      }
    },
    {
      for alias, endpoint in module.ceph_radosgw.requires :
      "ceph_radosgw_${alias}" => {
        endpoint = endpoint
        name     = module.ceph_radosgw.application.name
      }
    },
    var.ceph_fs == null ? {} : {
      for alias, endpoint in module.ceph_fs[0].requires :
      "ceph_fs_${alias}" => {
        endpoint = endpoint
        name     = module.ceph_fs[0].application.name
      }
    },
    var.ceph_nfs == null ? {} : {
      for alias, endpoint in module.ceph_nfs[0].requires :
      "ceph_nfs_${alias}" => {
        endpoint = endpoint
        name     = module.ceph_nfs[0].application.name
      }
    },
    var.ceph_nvme == null ? {} : {
      for alias, endpoint in module.ceph_nvme[0].requires :
      "ceph_nvme_${alias}" => {
        endpoint = endpoint
        name     = module.ceph_nvme[0].application.name
      }
    },
    var.ceph_rbd_mirror == null ? {} : {
      for alias, endpoint in module.ceph_rbd_mirror[0].requires :
      "ceph_rbd_mirror_${alias}" => {
        endpoint = endpoint
        name     = module.ceph_rbd_mirror[0].application.name
      }
    },
    var.ceph_dashboard == null ? {} : {
      for alias, endpoint in module.ceph_dashboard[0].requires :
      "ceph_dashboard_${alias}" => {
        endpoint = endpoint
        name     = module.ceph_dashboard[0].application.name
      }
    },
    var.ceph_proxy == null ? {} : {
      for alias, endpoint in module.ceph_proxy[0].requires :
      "ceph_proxy_${alias}" => {
        endpoint = endpoint
        name     = module.ceph_proxy[0].application.name
      }
    },
  )
}
