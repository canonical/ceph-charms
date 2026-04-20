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
