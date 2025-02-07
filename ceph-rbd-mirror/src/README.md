# Overview

[Ceph][ceph-upstream] is a unified, distributed storage system designed for
excellent performance, reliability, and scalability.

The ceph-rbd-mirror charm deploys the Ceph `rbd-mirror` daemon and helps
automate remote creation and configuration of mirroring for Ceph pools used for
hosting RBD images.

> **Note**: RBD mirroring is only one aspect of datacentre redundancy. Refer to
  [Ceph RADOS Gateway Multisite Replication][ceph-multisite-replication] and
  other work to arrive at a complete solution.

## Functionality

The charm has the following major features:

* Support for a maximum of two Ceph clusters. The clusters may reside within a
  single model or be contained within two separate models.

* Specifically written for two-way replication. This provides the ability to
  fail over and fall back to/from a single secondary site. Ceph does have
  support for mirroring to any number of clusters but the charm does not
  support this.

* Automatically creates and configures (for mirroring) pools in the remote
  cluster based on any pools in the local cluster that are labelled with the
  'rbd' tag.

* Mirroring of whole pools only. Ceph itself has support for the mirroring of
  individual images but the charm does not support this.

* Network space aware. The mirror daemon can be informed about network
  configuration by binding the `public` and `cluster` endpoints. The daemon
  will use the network associated with the `cluster` endpoint for mirroring
  traffic.

Other notes on RBD mirroring:

* Supports multiple running instances of the mirror daemon in each cluster.
  Doing so allows for the dynamic re-distribution of the mirroring load amongst
  the daemons. This addresses both high availability and performance concerns.
  Leverage this feature by scaling out the ceph-rbd-mirror application (i.e.
  add more units).

* Requires that every RBD image within each pool is created with the
  `journaling` and `exclusive-lock` image features enabled. The charm enables
  these features by default and the ceph-mon charm will announce them over the
  `client` relation when it has units connected to its `rbd-mirror` endpoint.

* The feature first appeared in Ceph Luminous (OpenStack Queens).

# Usage

## Configuration

See file `config.yaml` of the built charm (or see the charm in the [Charm
Store][cs-ceph-rbd-mirror]) for the full list of configuration options, along
with their descriptions and default values. See the [Juju
documentation][juju-docs-config-apps] for details on configuring applications.

## Deployment

A standard topology consists of two Ceph clusters with each cluster residing in
a separate Juju model. The deployment steps are a fairly involved and are
therefore covered under [Ceph RBD Mirroring][cdg-rbd-mirroring] in the
[OpenStack Charms Deployment Guide][cdg].

## Actions

This section lists Juju [actions][juju-docs-actions] supported by the charm.
Actions allow specific operations to be performed on a per-unit basis. To
display action descriptions run `juju actions ceph-rbd-mirror`. If the charm is
not deployed then see file `actions.yaml`.

* `copy-pool`
* `demote`
* `promote`
* `refresh-pools`
* `resync-pools`
* `status`

## Operations

Operational procedures touch upon pool creation, failover & fallback, and
recovering from an abrupt shutdown. These topics are also covered under [Ceph
RBD Mirroring][cdg-rbd-mirroring] in the [OpenStack Charms Deployment
Guide][cdg].

# Bugs

Please report bugs on [Launchpad][lp-bugs-charm-ceph-rbd-mirror].

For general charm questions refer to the [OpenStack Charm Guide][cg].

<!-- LINKS -->

[cg]: https://docs.openstack.org/charm-guide
[cdg]: https://docs.openstack.org/project-deploy-guide/charm-deployment-guide/latest/index.html
[ceph-upstream]: https://ceph.io
[ceph-multisite-replication]: https://docs.openstack.org/project-deploy-guide/charm-deployment-guide/latest/app-rgw-multisite.html
[cdg-rbd-mirroring]: https://docs.openstack.org/project-deploy-guide/charm-deployment-guide/latest/app-ceph-rbd-mirror.html
[lp-bugs-charm-ceph-rbd-mirror]: https://bugs.launchpad.net/charm-ceph-rbd-mirror/+filebug
[juju-docs-actions]: https://jaas.ai/docs/actions
[juju-docs-config-apps]: https://juju.is/docs/configuring-applications
[cs-ceph-rbd-mirror]: https://jaas.ai/ceph-rbd-mirror
