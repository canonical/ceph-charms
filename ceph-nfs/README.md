# ceph-nfs

## Description

CephNFS is a charm designed to enable management of NFS shares backed
by CephFS. It supports Ceph Pacific and above.

## Usage

CephNFS provides an additional service when deployed with Ceph and CephFS.
It should be related to CephMon:

    juju add-relation ceph-nfs:ceph-client ceph-mon:client

Once all relations have settled, it is possible to create a new export:

    juju run-action --wait ceph-nfs/0 create-share name=test-share size=10 allowed-ips=10.0.0.0/24

The above command has creates an NFS share that is 10GB in size, and is
accessible from any machine in the 10.0.0.0-10.0.0.255 network space. To
grant access to a new network address, the `grant-access` action should be
used:

    juju run-action --wait ceph-nfs/0 grant-access name=test-share allowed-ips=192.168.0.10

This command has granted access to the named share to a specific
address: `192.168.0.1`.

It is possible to delete the created share with:

    juju run-action --wait ceph-nfs/0 delete-share name=test-share

## High Availability

To gain high availability for NFS shares, it is necessary to scale ceph-nfs and relate it to a loadbalancer charm:

    juju add-unit ceph-nfs -n 2
    juju config vip=10.5.0.100
    juju deploy hacluster
    juju add-relation ceph-nfs hacluster

Once everything settles, your shares will be accessible over the loadbalancer's vip (`10.5.0.100` in this example), and connections will load-balance across backends.

## Relations

Ceph-NFS consumes the ceph-client relation from the ceph-mon charm.

# Bugs

Please report bugs on [Launchpad][lp-bugs-charm-ceph-fs].

For general charm questions refer to the OpenStack [Charm Guide][cg].

Note that starting with the squid track of the ceph-nfs charm, deployment of Ceph Pacific and older clusters is not supported anymore. 

<!-- LINKS -->

[lp-bugs-charm-ceph-fs]: https://bugs.launchpad.net/charm-ceph-fs/+filebug
[cg]: https://docs.openstack.org/charm-guide
