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

  depends_on = [terraform_data.validate_model_target]
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

  depends_on = [terraform_data.validate_model_target]
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

  depends_on = [terraform_data.validate_model_target]
}

# The remaining charms are opt-in: each deploys only when its object input is
# non-null, keeping the default MON+OSD+RADOSGW topology unchanged. Charms
# that consume the monitor cluster are wired to ceph-mon in integrations.tf.

module "ceph_fs" {
  count  = var.ceph_fs == null ? 0 : 1
  source = "../ceph-fs/terraform"

  app_name          = var.ceph_fs.app_name
  base              = var.ceph_fs.base
  channel           = var.ceph_fs.channel
  config            = var.ceph_fs.config
  constraints       = var.ceph_fs.constraints
  model_uuid        = local.resolved_model_uuid
  offered_endpoints = local.ceph_fs_offered_endpoints
  resources         = var.ceph_fs.resources
  revision          = var.ceph_fs.revision
  units             = var.ceph_fs.units

  depends_on = [terraform_data.validate_model_target]
}

module "ceph_nfs" {
  count  = var.ceph_nfs == null ? 0 : 1
  source = "../ceph-nfs/terraform"

  app_name          = var.ceph_nfs.app_name
  base              = var.ceph_nfs.base
  channel           = var.ceph_nfs.channel
  config            = var.ceph_nfs.config
  constraints       = var.ceph_nfs.constraints
  model_uuid        = local.resolved_model_uuid
  offered_endpoints = local.ceph_nfs_offered_endpoints
  resources         = var.ceph_nfs.resources
  revision          = var.ceph_nfs.revision
  units             = var.ceph_nfs.units

  depends_on = [terraform_data.validate_model_target]
}

module "ceph_nvme" {
  count  = var.ceph_nvme == null ? 0 : 1
  source = "../ceph-nvme/terraform"

  app_name          = var.ceph_nvme.app_name
  base              = var.ceph_nvme.base
  channel           = var.ceph_nvme.channel
  config            = var.ceph_nvme.config
  constraints       = var.ceph_nvme.constraints
  model_uuid        = local.resolved_model_uuid
  offered_endpoints = local.ceph_nvme_offered_endpoints
  resources         = var.ceph_nvme.resources
  revision          = var.ceph_nvme.revision
  units             = var.ceph_nvme.units

  depends_on = [terraform_data.validate_model_target]
}

module "ceph_rbd_mirror" {
  count  = var.ceph_rbd_mirror == null ? 0 : 1
  source = "../ceph-rbd-mirror/terraform"

  app_name          = var.ceph_rbd_mirror.app_name
  base              = var.ceph_rbd_mirror.base
  channel           = var.ceph_rbd_mirror.channel
  config            = var.ceph_rbd_mirror.config
  constraints       = var.ceph_rbd_mirror.constraints
  model_uuid        = local.resolved_model_uuid
  offered_endpoints = local.ceph_rbd_mirror_offered_endpoints
  resources         = var.ceph_rbd_mirror.resources
  revision          = var.ceph_rbd_mirror.revision
  units             = var.ceph_rbd_mirror.units

  depends_on = [terraform_data.validate_model_target]
}

# ceph-dashboard is a subordinate charm: it carries no units of its own and is
# colocated with ceph-mon via the dashboard relation defined in integrations.tf.
module "ceph_dashboard" {
  count  = var.ceph_dashboard == null ? 0 : 1
  source = "../ceph-dashboard/terraform"

  app_name          = var.ceph_dashboard.app_name
  base              = var.ceph_dashboard.base
  channel           = var.ceph_dashboard.channel
  config            = var.ceph_dashboard.config
  constraints       = var.ceph_dashboard.constraints
  model_uuid        = local.resolved_model_uuid
  offered_endpoints = local.ceph_dashboard_offered_endpoints
  resources         = var.ceph_dashboard.resources
  revision          = var.ceph_dashboard.revision

  depends_on = [terraform_data.validate_model_target]
}

# ceph-proxy is a ceph-mon replacement that fronts an external cluster; it is
# deployed standalone and intentionally not wired to ceph-mon.
module "ceph_proxy" {
  count  = var.ceph_proxy == null ? 0 : 1
  source = "../ceph-proxy/terraform"

  app_name          = var.ceph_proxy.app_name
  base              = var.ceph_proxy.base
  channel           = var.ceph_proxy.channel
  config            = var.ceph_proxy.config
  constraints       = var.ceph_proxy.constraints
  model_uuid        = local.resolved_model_uuid
  offered_endpoints = local.ceph_proxy_offered_endpoints
  resources         = var.ceph_proxy.resources
  revision          = var.ceph_proxy.revision
  units             = var.ceph_proxy.units

  depends_on = [terraform_data.validate_model_target]
}
