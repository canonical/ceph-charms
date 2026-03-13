# Ceph Mon+OSD+RADOSGW Component Terraform Module

This module deploys the `ceph-mon`, `ceph-osd`, and `ceph-radosgw` charms into an existing Juju model and wires their required integrations:
- `ceph-mon:osd` ↔ `ceph-osd:mon`
- `ceph-mon:radosgw` ↔ `ceph-radosgw:mon`

Implementation model:
- charm deployments are delegated to leaf modules (`../ceph-mon/terraform`, `../ceph-osd/terraform`, `../ceph-radosgw/terraform`)
- this top-level module is responsible for cross-charm integrations and aggregated outputs

It follows the CC008 component-module style with:
- object inputs per charm
- model targeting by `model_uuid` or `model_name`/`model_owner`
- standardized `components`, `provides`, and `requires` outputs

## Requirements

- Terraform `>= 1.6`
- Juju Terraform provider `>= 1.0.0`

## Testing

Run the Terraform integration test suite locally with pytest/jubilant via tox:

```bash
tox -e terraform-integration
```

Useful options:

- keep the temporary Juju model (and Terraform workspace) for inspection:

  ```bash
  tox -e terraform-integration -- --keep-models
  ```

- keep only the generated Terraform workspace files:

  ```bash
  tox -e terraform-integration -- --keep-terraform-workspace
  ```

Prerequisites:
- a bootstrapped Juju controller accessible from your shell
- Terraform installed and available in `PATH`
- a Juju cloud/provider capable of launching virtual machines for OSD storage tests

Notes:
- tests set model-wide constraints to `virt-type=virtual-machine mem=4G root-disk=16G`
- tests use Juju client defaults for provider auth and set `JUJU_MODEL` for model targeting
- default integration topology is `3x ceph-mon`, `3x ceph-osd` (with `osd-devices=1G,1`), and `1x ceph-radosgw`
- full integration runs can take several minutes depending on image/package caching

## Inputs

### Mandatory

| Name | Type | Description |
|---|---|---|
| `ceph_mon` | `object` | `ceph-mon` deployment configuration object. |
| `ceph_osd` | `object` | `ceph-osd` deployment configuration object. |
| `ceph_radosgw` | `object` | `ceph-radosgw` deployment configuration object. |

Charm objects include at least: `channel`, `base`, and `revision` (plus standard optional fields such as `app_name`, `config`, `constraints`, `resources`, `units`, and `offered_endpoints`; and `storage_directives` for `ceph_osd`).

### Model targeting (one option required)

| Name | Type | Description |
|---|---|---|
| `model_uuid` | `string` | UUID of the Juju model where applications are deployed. |
| `model_name` | `string` | Name of the Juju model to resolve when `model_uuid` is not set. |
| `model_owner` | `string` | Model owner used with `model_name` lookup (default: `admin`). |

### Optional

| Name | Type | Description |
|---|---|---|
| `bootstrap_source` | `object` | External integration descriptor for `ceph-mon:bootstrap-source` (`kind = endpoint|offer`). |
| `secrets_storage` | `object` | External integration descriptor for `ceph-osd:secrets-storage` (`kind = endpoint|offer`). |
| `expose_endpoints` | `list(string)` | List of `<charm>_<endpoint_alias>` keys from `provides` to publish as Juju offers. |

Offer publication is enabled when either a charm's `offered_endpoints` list or this top-level `expose_endpoints` list includes a provided endpoint.

## Outputs

| Name | Type | Description |
|---|---|---|
| `components` | `list(object)` | Deployed application objects (`ceph-mon`, `ceph-osd`, `ceph-radosgw`). |
| `components_map` | `map(object)` | Same applications keyed by name. |
| `provides` | `map(object)` | Provided endpoints keyed by `<charm_name>_<endpoint>`. |
| `requires` | `map(object)` | Required endpoints keyed by `<charm_name>_<endpoint>`. |
| `offers` | `map(string)` | Offer URLs keyed by `<charm_name>_<endpoint>`. |

## Breaking changes

This module no longer accepts `manifest_yaml` and `model`.
Use explicit `ceph_mon`, `ceph_osd`, and `ceph_radosgw` objects plus either
`model_uuid` or `model_name`/`model_owner`.

## Example (3 MON + 3 OSD + 1 RADOSGW)

The example below is a practical baseline that converges a small Ceph cluster:
- 3 `ceph-mon` units
- 3 `ceph-osd` units
- 1 OSD device per OSD unit
- 1 `ceph-radosgw` unit

```hcl
module "ceph_component" {
  source = "../terraform"

  model_uuid = "00000000-0000-0000-0000-000000000000"

  expose_endpoints = ["ceph_radosgw_s3"]

  ceph_mon = {
    channel = "squid/stable"
    base    = "ubuntu@24.04"
    units   = 3
    config = {
      "monitor-count"      = "3"
      "expected-osd-count" = "3"
    }
  }

  ceph_osd = {
    channel = "squid/stable"
    base    = "ubuntu@24.04"
    units   = 3
    storage_directives = {
      "osd-devices" = "1G,1"
    }
  }

  ceph_radosgw = {
    channel = "squid/stable"
    base    = "ubuntu@24.04"
    units   = 1
  }
}
```
