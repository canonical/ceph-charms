# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

# Optional external relation for ceph-mon bootstrap-source.
# DEPRECATED: bootstrap_source is deprecated and will be removed in the
# Umbrella release.
# The input object supports:
# - kind = "endpoint": in-model integration via name/endpoint
# - kind = "offer": cross-model integration via offer URL
resource "juju_integration" "ceph_mon_bootstrap_source" {
  count      = var.bootstrap_source == null ? 0 : 1
  model_uuid = local.resolved_model_uuid

  application {
    endpoint = "bootstrap-source"
    name     = module.ceph_mon.application.name
  }

  application {
    endpoint  = var.bootstrap_source.kind == "endpoint" ? var.bootstrap_source.endpoint : null
    name      = var.bootstrap_source.kind == "endpoint" ? var.bootstrap_source.name : null
    offer_url = var.bootstrap_source.kind == "offer" ? var.bootstrap_source.url : null
  }
}

# Core Ceph relation required for MON quorum + OSD provisioning.
resource "juju_integration" "ceph_mon_to_ceph_osd" {
  model_uuid = local.resolved_model_uuid

  application {
    endpoint = "osd"
    name     = module.ceph_mon.application.name
  }

  application {
    endpoint = "mon"
    name     = module.ceph_osd.application.name
  }
}

# Connect RGW to the monitor cluster so gateways can register and serve buckets.
resource "juju_integration" "ceph_mon_to_ceph_radosgw" {
  model_uuid = local.resolved_model_uuid

  application {
    endpoint = "radosgw"
    name     = module.ceph_mon.application.name
  }

  application {
    endpoint = "mon"
    name     = module.ceph_radosgw.application.name
  }
}

# Optional external relation for ceph-osd secrets-storage, with the same
# endpoint/offer descriptor shape as bootstrap_source.
resource "juju_integration" "ceph_osd_secrets_storage" {
  count      = var.secrets_storage == null ? 0 : 1
  model_uuid = local.resolved_model_uuid

  application {
    endpoint = "secrets-storage"
    name     = module.ceph_osd.application.name
  }

  application {
    endpoint  = var.secrets_storage.kind == "endpoint" ? var.secrets_storage.endpoint : null
    name      = var.secrets_storage.kind == "endpoint" ? var.secrets_storage.name : null
    offer_url = var.secrets_storage.kind == "offer" ? var.secrets_storage.url : null
  }
}

# Opt-in integrations wiring the additional charms to the monitor cluster.
# Each is created only when the corresponding charm object is non-null.

# ceph-fs consumes the ceph-mds endpoint provided by ceph-mon:mds.
resource "juju_integration" "ceph_mon_to_ceph_fs" {
  count      = var.ceph_fs == null ? 0 : 1
  model_uuid = local.resolved_model_uuid

  application {
    endpoint = "mds"
    name     = module.ceph_mon.application.name
  }

  application {
    endpoint = "ceph-mds"
    name     = module.ceph_fs[0].application.name
  }
}

# ceph-nfs consumes the ceph-client endpoint provided by ceph-mon:client.
resource "juju_integration" "ceph_mon_to_ceph_nfs" {
  count      = var.ceph_nfs == null ? 0 : 1
  model_uuid = local.resolved_model_uuid

  application {
    endpoint = "client"
    name     = module.ceph_mon.application.name
  }

  application {
    endpoint = "ceph-client"
    name     = module.ceph_nfs[0].application.name
  }
}

# ceph-nvme consumes the ceph-client endpoint provided by ceph-mon:client.
resource "juju_integration" "ceph_mon_to_ceph_nvme" {
  count      = var.ceph_nvme == null ? 0 : 1
  model_uuid = local.resolved_model_uuid

  application {
    endpoint = "client"
    name     = module.ceph_mon.application.name
  }

  application {
    endpoint = "ceph-client"
    name     = module.ceph_nvme[0].application.name
  }
}

# ceph-rbd-mirror consumes the local cluster via ceph-mon:rbd-mirror.
resource "juju_integration" "ceph_mon_to_ceph_rbd_mirror" {
  count      = var.ceph_rbd_mirror == null ? 0 : 1
  model_uuid = local.resolved_model_uuid

  application {
    endpoint = "rbd-mirror"
    name     = module.ceph_mon.application.name
  }

  application {
    endpoint = "ceph-local"
    name     = module.ceph_rbd_mirror[0].application.name
  }
}

# ceph-dashboard is a subordinate colocated with ceph-mon via the dashboard
# relation (ceph-mon:dashboard <-> ceph-dashboard:dashboard).
resource "juju_integration" "ceph_mon_to_ceph_dashboard" {
  count      = var.ceph_dashboard == null ? 0 : 1
  model_uuid = local.resolved_model_uuid

  application {
    endpoint = "dashboard"
    name     = module.ceph_mon.application.name
  }

  application {
    endpoint = "dashboard"
    name     = module.ceph_dashboard[0].application.name
  }
}
