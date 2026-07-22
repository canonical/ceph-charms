# Terraform deployment smoke tests

These tests deploy published Ceph charms from Charmhub through the Terraform
modules in this repository. They validate Terraform planning, application and
integration creation, module outputs, convergence, and a small charm-specific
smoke operation. Full data-path, failover, upgrade, and lifecycle coverage
remains in each charm's functional test suite.

The suite uses one temporary Juju model per test module:

- `test_terraform.py`: core MON, OSD, and RadosGW component plus plan-time wiring
- `test_ceph_fs.py`: core component plus CephFS
- `test_ceph_nfs.py`: core component plus CephFS and NFS
- `test_ceph_dashboard.py`: core component plus dashboard with temporary TLS
- `test_ceph_nvme.py`: core component plus a two-unit NVMe gateway
- `test_ceph_rbd_mirror.py`: two complete Ceph sites with cross-site mirroring
- `test_ceph_proxy.py`: two-phase source-cluster and proxy deployment

Run the core CI subset with:

```bash
tox -e terraform-integration -- -m "not slow" tests/integration/terraform
```

Run the scheduled deployment scenarios with:

```bash
tox -e terraform-integration -- -m slow tests/integration/terraform
```

Run every Terraform test locally with `tox -e terraform-integration`.

Run a single scenario with:

```bash
tox -e terraform-integration -- tests/integration/terraform/test_ceph_fs.py
```

Use `--keep-models` and/or `--keep-terraform-workspace` for local debugging.
Proxy workspaces contain an ephemeral Ceph admin key and must not be published.
The Juju Terraform provider deploys Charmhub revisions; it cannot deploy local
`.charm` files.
