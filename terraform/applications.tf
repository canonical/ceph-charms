# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

# Compose the component from leaf charm modules, while keeping integration
# wiring in this top-level module.
module "ceph_mon" {
  source = "../ceph-mon/terraform"

  app_name          = var.ceph_mon.app_name
  base              = var.ceph_mon.base
  channel           = var.ceph_mon.channel
  config            = var.ceph_mon.config
  constraints       = var.ceph_mon.constraints
  model_uuid        = local.resolved_model_uuid
  offered_endpoints = local.ceph_mon_offered_endpoints
  resources         = var.ceph_mon.resources
  revision          = var.ceph_mon.revision
  units             = var.ceph_mon.units
}

module "ceph_osd" {
  source = "../ceph-osd/terraform"

  app_name           = var.ceph_osd.app_name
  base               = var.ceph_osd.base
  channel            = var.ceph_osd.channel
  config             = var.ceph_osd.config
  constraints        = var.ceph_osd.constraints
  model_uuid         = local.resolved_model_uuid
  offered_endpoints  = local.ceph_osd_offered_endpoints
  resources          = var.ceph_osd.resources
  revision           = var.ceph_osd.revision
  storage_directives = var.ceph_osd.storage_directives
  units              = var.ceph_osd.units
}

module "ceph_radosgw" {
  source = "../ceph-radosgw/terraform"

  app_name          = var.ceph_radosgw.app_name
  base              = var.ceph_radosgw.base
  channel           = var.ceph_radosgw.channel
  config            = var.ceph_radosgw.config
  constraints       = var.ceph_radosgw.constraints
  model_uuid        = local.resolved_model_uuid
  offered_endpoints = local.ceph_radosgw_offered_endpoints
  resources         = var.ceph_radosgw.resources
  revision          = var.ceph_radosgw.revision
  units             = var.ceph_radosgw.units
}
