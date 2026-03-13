# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

locals {
  # Resolve model UUID either directly from input or via name/owner lookup.
  use_model_lookup = var.model_uuid == null && var.model_name != null
}

data "juju_model" "target" {
  count = local.use_model_lookup ? 1 : 0
  name  = var.model_name
  owner = var.model_owner
}

locals {
  resolved_model_uuid = coalesce(
    var.model_uuid,
    try(data.juju_model.target[0].uuid, null),
  )
}
