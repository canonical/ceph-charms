# ceph-mon Terraform module

CC008-style charm module for deploying the `ceph-mon` charm with the Juju Terraform provider.

## Requirements

- Terraform `>= 1.6`
- Juju provider `>= 1.0.0`

## Inputs

### Mandatory

| Name | Type | Default | Description |
|---|---|---|---|
| `app_name` | `string` | `"ceph-mon"` | Name of the deployed application. |
| `channel` | `string` | `"squid/stable"` | Charm channel to deploy. |
| `config` | `map(string)` | `{}` | Charm config options. |
| `constraints` | `string` | `null` | Juju constraints for the application. |
| `model_uuid` | `string` | n/a | UUID of an existing Juju model. Not nullable. |
| `revision` | `number` | `null` | Charm revision (null deploys latest in channel). |
| `units` | `number` | `1` | Number of units to deploy. |

### Optional

| Name | Type | Default | Description |
|---|---|---|---|
| `base` | `string` | `"ubuntu@24.04"` | Base used for deployment, e.g. `ubuntu@24.04`. |
| `resources` | `map(string)` | `{}` | Resources to use with the charm. |
| `offered_endpoints` | `list(string)` | `[]` | List of provides endpoint aliases to publish as Juju offers. |

> **Warning**: The `requires.bootstrap_source` endpoint exposed by this module
> maps to ceph-mon's deprecated `bootstrap-source` relation and will be
> removed in the Umbrella release.

## Outputs

| Name | Type | Description |
|---|---|---|
| `application` | `object` | Object representing the deployed application. |
| `provides` | `map(string)` | Map of provided integration endpoints. |
| `requires` | `map(string)` | Map of required integration endpoints. |
| `offers` | `map(string)` | Exposed offer URLs keyed by endpoint alias. |
