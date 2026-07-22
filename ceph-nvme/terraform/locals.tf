# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

locals {
  # Endpoint aliases are exported for composition by higher-level modules.
  offers = {
    for alias, offer in juju_offer.offers :
    alias => offer.url
  }

  provides = {
    admin_access = "admin-access"
  }

  requires = {
    ceph_client = "ceph-client"
  }
}
