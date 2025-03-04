# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

output "ceph_mon" {
  description = "Object of the ceph_mon application."
  value       = module.ceph_mon
}

output "ceph_osd" {
  description = "Object of the ceph_osd application."
  value       = module.ceph_osd
}
