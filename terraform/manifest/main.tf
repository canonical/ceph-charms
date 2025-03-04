# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

locals {
  yaml_data = lookup(yamldecode(file("${var.manifest}")), var.app, {})
}
