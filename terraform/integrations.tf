# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

resource "juju_integration" "ceph_mon" {
  model = var.model
  application {
    name     = module.ceph_mon.app_name
    endpoint = module.ceph_mon.provides.osd
  }
  application {
    name     = module.ceph_osd.app_name
    endpoint = module.ceph_osd.requires.mon
  }
}
