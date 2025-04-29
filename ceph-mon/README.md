# Overview

[Ceph][ceph-upstream] is a unified, distributed storage system designed for
excellent performance, reliability, and scalability.

The ceph-mon charm deploys Ceph monitor nodes, allowing one to create a monitor
cluster. It is used in conjunction with the [ceph-osd][ceph-osd-charm] charm.
Together, these charms can scale out the amount of storage available in a Ceph
cluster.

# Usage

## Configuration

This section covers common and/or important configuration options. See file
`config.yaml` for the full list of options, along with their descriptions and
default values. See the [Juju documentation][juju-docs-config-apps] for details
on configuring applications.

#### `customize-failure-domain`

The `customize-failure-domain` option determines how a Ceph CRUSH map is
configured.

A value of 'false' (the default) will lead to a map that will replicate data
across hosts (implemented as [Ceph bucket type][upstream-ceph-buckets] 'host').
With a value of 'true' all MAAS-defined zones will be used to generate a map
that will replicate data across Ceph availability zones (implemented as bucket
type 'rack').

This option is also supported by the ceph-osd charm. Its value must be the same
for both charms.

#### `monitor-count`

The `monitor-count` option gives the number of ceph-mon units in the monitor
sub-cluster (where one ceph-mon unit represents one MON). The default value is
'3' and is generally a good choice, but it is good practice to set this
explicitly to avoid a possible race condition during the formation of the
sub-cluster. To establish quorum and enable partition tolerance an odd number
of ceph-mon units is required.

> **Important**: A monitor count of less than three is not recommended for
  production environments. Test environments can use a single ceph-mon unit by
  setting this option to '1'.

#### `expected-osd-count`

The `expected-osd-count` option states the number of OSDs expected to be
deployed in the cluster. This value can influence the number of placement
groups (PGs) to use per pool. The PG calculation is based either on the actual
number of OSDs or this option's value, whichever is greater. The default value
is '0', which tells the charm to only consider the actual number of OSDs. If
the actual number of OSDs is less than three then this option must explicitly
state that number. Only until a sufficient (or prescribed) number of OSDs has
been attained will the charm be able to create Ceph pools.

> **Note**: The inability to create a pool due to an insufficient number of
  OSDs will cause any consuming application (characterised by a relation
  involving the `ceph-mon:client` endpoint) to remain in the 'waiting' state.

#### `source`

The `source` option states the software sources. A common value is an OpenStack
UCA release (e.g. 'cloud:xenial-queens' or 'cloud:bionic-ussuri'). See [Ceph
and the UCA][cloud-archive-ceph]. The underlying host's existing apt sources
will be used if this option is not specified (this behaviour can be explicitly
chosen by using the value of 'distro').

## Deployment

A cloud with three MON nodes is a typical design whereas three OSDs are
considered the minimum. For example, to deploy a Ceph cluster consisting of
three OSDs (one per ceph-osd unit) and three MONs:

    juju deploy -n 3 --config ceph-osd.yaml ceph-osd
    juju deploy -n 3 --to lxd:0,lxd:1,lxd:2 ceph-mon
    juju add-relation ceph-osd:mon ceph-mon:osd

Here, a containerised MON is running alongside each storage node. We've assumed
that the machines spawned in the first command are assigned IDs of 0, 1, and 2.

By default, the monitor cluster will not be complete until three ceph-mon units
have been deployed. This is to ensure that a quorum is achieved prior to the
addition of storage devices.

See the [Ceph documentation][ceph-docs-monitors] for notes on monitor cluster
deployment strategies.

> **Note**: Refer to the [Install OpenStack][cdg-install-openstack] page in the
  OpenStack Charms Deployment Guide for instructions on installing a monitor
  cluster for use with OpenStack.

## Network spaces

This charm supports the use of Juju [network spaces][juju-docs-spaces] (Juju
`v.2.0`). This feature optionally allows specific types of the application's
network traffic to be bound to subnets that the underlying hardware is
connected to.

> **Note**: Spaces must be configured in the backing cloud prior to deployment.

The ceph-mon charm exposes the following Ceph traffic types (bindings):

* 'public' (front-side)
* 'cluster' (back-side)

For example, providing that spaces 'data-space' and 'cluster-space' exist, the
deploy command above could look like this:

    juju deploy -n 3 --config ceph-mon.yaml ceph-mon \
       --bind "public=data-space cluster=cluster-space"

Alternatively, configuration can be provided as part of a bundle:

```yaml
    ceph-mon:
      charm: cs:ceph-mon
      num_units: 1
      bindings:
        public: data-space
        cluster: cluster-space
```

Refer to the [Ceph Network Reference][ceph-docs-network-ref] to learn about the
implications of segregating Ceph network traffic.

> **Note**: Existing ceph-mon units configured with the `ceph-public-network`
  or `ceph-cluster-network` options will continue to honour them. Furthermore,
  these options override any space bindings, if set.

## Monitoring

The charm supports Ceph metric monitoring with Prometheus. Add relations to the
[prometheus][prometheus-charm] application in this way:

    juju deploy prometheus2
    juju add-relation ceph-mon prometheus2

> **Note**: Prometheus support is available starting with Ceph Luminous
  (xenial-queens UCA pocket).

## Actions

This section lists Juju [actions][juju-docs-actions] supported by the charm.
Actions allow specific operations to be performed on a per-unit basis. To
display action descriptions run `juju actions ceph-mon`. If the charm is not
deployed then see file `actions.yaml`.

* `change-osd-weight`
* `copy-pool`
* `create-cache-tier`
* `create-crush-rule`
* `create-erasure-profile`
* `create-pool`
* `crushmap-update`
* `delete-erasure-profile`
* `delete-pool`
* `get-erasure-profile`
* `get-health`
* `list-erasure-profiles`
* `list-inconsistent-objs`
* `list-pools`
* `pause-health`
* `pool-get`
* `pool-set`
* `pool-statistics`
* `purge-osd`
* `remove-cache-tier`
* `remove-pool-snapshot`
* `rename-pool`
* `resume-health`
* `security-checklist`
* `set-noout`
* `set-pool-max-bytes`
* `show-disk-free`
* `snapshot-pool`
* `unset-noout`

## Presenting the list of Ceph pools with details

The following example returns the list of pools with details: `id`, `name`,
`size` and `min_size`.
The [jq][jq] utility has been used to parse the action output in json format.

    juju run-action --wait ceph-mon/leader list-pools detail=true \
      --format json | jq '.[].results.pools | fromjson | .[]
      | {pool:.pool, name:.pool_name, size:.size, min_size:.min_size}'

Sample output:

    {
      "pool": 1,
      "name": "test",
      "size": 3,
      "min_size": 2
    }
    {
      "pool": 2,
      "name": "test2",
      "size": 3,
      "min_size": 2
    }

# Bugs

Please report bugs on [Launchpad][lp-bugs-charm-ceph-mon].

For general charm questions refer to the OpenStack [Charm Guide][cg].

<!-- LINKS -->

[ceph-upstream]: https://ceph.io
[cg]: https://docs.openstack.org/charm-guide
[ceph-osd-charm]: https://jaas.ai/ceph-osd
[juju-docs-actions]: https://jaas.ai/docs/actions
[juju-docs-spaces]: https://jaas.ai/docs/spaces
[juju-docs-config-apps]: https://juju.is/docs/configuring-applications
[ceph-docs-network-ref]: http://docs.ceph.com/docs/master/rados/configuration/network-config-ref
[ceph-docs-monitors]: https://docs.ceph.com/docs/master/dev/mon-bootstrap
[lp-bugs-charm-ceph-mon]: https://bugs.launchpad.net/charm-ceph-mon/+filebug
[cdg-install-openstack]: https://docs.openstack.org/project-deploy-guide/charm-deployment-guide/latest/install-openstack.html
[prometheus-charm]: https://jaas.ai/prometheus2
[cloud-archive-ceph]: https://wiki.ubuntu.com/OpenStack/CloudArchive#Ceph_and_the_UCA
[upstream-ceph-buckets]: https://docs.ceph.com/docs/master/rados/operations/crush-map/#types-and-buckets
[jq]: https://stedolan.github.io/jq/
