# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

locals {
  # Endpoint aliases are exported for composition by higher-level modules.
  offers = {
    for alias, offer in juju_offer.offers :
    alias => offer.url
  }

  provides = {
    grafana_dashboard = "grafana-dashboard"
  }

  requires = {
    alertmanager_service = "alertmanager-service"
    certificates         = "certificates"
    dashboard            = "dashboard"
    iscsi_dashboard      = "iscsi-dashboard"
    loadbalancer         = "loadbalancer"
    prometheus           = "prometheus"
    radosgw_dashboard    = "radosgw-dashboard"
  }
}
