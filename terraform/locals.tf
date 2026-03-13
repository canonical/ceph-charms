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
  )
}
