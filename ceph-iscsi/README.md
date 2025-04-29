# Overview

The ceph-iscsi charm deploys the [Ceph iSCSI gateway
service][ceph-iscsi-upstream]. The charm is intended to be used in conjunction
with the [ceph-osd][ceph-osd-charm] and [ceph-mon][ceph-mon-charm] charms.

# Usage

## Configuration

See file `config.yaml` for the full list of options, along with their
descriptions and default values.

## Ceph BlueStore compression

This charm supports [BlueStore inline compression][ceph-bluestore-compression]
for its associated Ceph storage pool(s). The feature is enabled by assigning a
compression mode via the `bluestore-compression-mode` configuration option. The
default behaviour is to disable compression.

The efficiency of compression depends heavily on what type of data is stored
in the pool and the charm provides a set of configuration options to fine tune
the compression behaviour.

> **Note**: BlueStore compression is supported starting with Ceph Mimic.

## Deployment

We are assuming a pre-existing Ceph cluster.

To provide multiple data paths to clients deploy exactly two ceph-iscsi units:

    juju deploy -n 2 ceph-iscsi

Then add a relation to the ceph-mon application:

    juju add-relation ceph-iscsi:ceph-client ceph-mon:client

**Notes**:

* Deploying four ceph-iscsi units is theoretically possible but it is not an
  officially supported configuration.

* The ceph-iscsi application cannot be containerised.

* Co-locating ceph-iscsi with another application is only supported with
  ceph-osd, although doing so with other applications may still work.

## Actions

This section covers Juju [actions][juju-docs-actions] supported by the charm.
Actions allow specific operations to be performed on a per-unit basis. To
display action descriptions run `juju actions ceph-iscsi`. If the charm is not
deployed then see file `actions.yaml`.

* `add-trusted-ip`
* `create-target`
* `pause`
* `resume`
* `security-checklist`

To display action descriptions run `juju actions ceph-iscsi`. If the charm is
not deployed then see file `actions.yaml`.

## iSCSI target management

### Create an iSCSI target

An iSCSI target can be created easily with the charm's `create-target` action:

    juju run-action --wait ceph-iscsi/0 create-target \
       client-initiatorname=iqn.1993-08.org.debian:01:aaa2299be916 \
       client-username=myiscsiusername \
       client-password=myiscsipassword \
       image-size=5G \
       image-name=small \
       pool-name=images

In the above, all option values are generally user-defined with the exception
of the initiator name (`client-initiatorname`). An iSCSI initiator is
essentially an iSCSI client and so its name is client-dependent. Some
initiators may impose policy on credentials (`client-username` and
`client-password`).

> **Important**: The underlying machines for the ceph-iscsi units must have
  internal name resolution working (i.e. the machines must be able to resolve
  each other's hostnames).

### The `gwcli` utility

The management of targets, beyond the target-creation action described above,
can be accomplished via the `gwcli` utility. This CLI tool has its own shell,
and is available from any ceph-iscsi unit:

    juju ssh ceph-iscsi/1
    sudo gwcli
    /> help

## VMware integration

Ceph can be used to back iSCSI targets for VMware initiators. This is
documented under [VMware integration][ceph-docs-vmware-integration] in the
[Charmed Ceph documentation][ceph-docs].

# Bugs

Please report bugs on [Launchpad][lp-bugs-charm-ceph-iscsi].

For general charm questions refer to the [OpenStack Charm Guide][cg].

<!-- LINKS -->

[ceph-mon-charm]: https://jaas.ai/ceph-mon
[ceph-osd-charm]: https://jaas.ai/ceph-osd
[cg]: https://docs.openstack.org/charm-guide
[cdg]: https://docs.openstack.org/project-deploy-guide/charm-deployment-guide
[ceph-docs-vmware-integration]: https://ubuntu.com/ceph/docs/integration-vmware
[ceph-docs]: https://ubuntu.com/ceph/docs
[juju-docs-actions]: https://jaas.ai/docs/actions
[ceph-iscsi-upstream]: https://docs.ceph.com/docs/master/rbd/iscsi-overview/
[lp-bugs-charm-ceph-iscsi]: https://bugs.launchpad.net/charm-ceph-iscsi/+filebug
