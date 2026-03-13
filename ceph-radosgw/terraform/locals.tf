# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

locals {
  # Endpoint aliases are exported for composition by higher-level modules.
  offers = {
    for alias, offer in juju_offer.offers :
    alias => offer.url
  }

  provides = {
    gateway              = "gateway"
    master               = "master"
    nrpe_external_master = "nrpe-external-master"
    object_store         = "object-store"
    primary              = "primary"
    radosgw_user         = "radosgw-user"
    s3                   = "s3"
  }

  requires = {
    certificates     = "certificates"
    ha               = "ha"
    identity_service = "identity-service"
    mon              = "mon"
    secondary        = "secondary"
    slave            = "slave"
  }
}
