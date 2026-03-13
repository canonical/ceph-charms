# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

locals {
  # Endpoint aliases are exported for composition by higher-level modules.
  offers = {
    for alias, offer in juju_offer.offers :
    alias => offer.url
  }

  provides = {
    admin                = "admin"
    client               = "client"
    cos_agent            = "cos-agent"
    dashboard            = "dashboard"
    mds                  = "mds"
    metrics_endpoint     = "metrics-endpoint"
    nrpe_external_master = "nrpe-external-master"
    osd                  = "osd"
    prometheus           = "prometheus"
    radosgw              = "radosgw"
    rbd_mirror           = "rbd-mirror"
  }

  requires = {
    bootstrap_source = "bootstrap-source"
  }
}
