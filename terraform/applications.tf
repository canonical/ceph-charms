# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

module "ceph_mon" {
  source = "../ceph-mon/terraform"

  model       = var.model
  base        = module.ceph_mon_config.config.base
  constraints = module.ceph_mon_config.config.constraints
  channel     = module.ceph_mon_config.config.channel

  config    = module.ceph_mon_config.config.config
  resources = module.ceph_mon_config.config.resources
  revision  = module.ceph_mon_config.config.revision
  units     = module.ceph_mon_config.config.units
}

module "ceph_osd" {
  source = "../ceph-osd/terraform"

  model       = var.model
  base        = module.ceph_osd_config.config.base
  constraints = module.ceph_osd_config.config.constraints
  channel     = module.ceph_osd_config.config.channel

  config    = module.ceph_osd_config.config.config
  resources = module.ceph_osd_config.config.resources
  storage   = module.ceph_osd_config.config.storage
  revision  = module.ceph_osd_config.config.revision
  units     = module.ceph_osd_config.config.units
}
