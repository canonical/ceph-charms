#!/usr/bin/env python3
# Copyright 2021 OpenStack Charmers
# See LICENSE file for licensing details.
#
# Learn more at: https://juju.is/docs/sdk

"""Charm the service.

Refer to the following post for a quick-start guide that will help you
develop a new k8s charm using the Operator Framework:

    https://discourse.charmhub.io/t/4208
"""

import logging
import os
from pathlib import Path
import subprocess

from ops.charm import CharmBase
from ops.framework import StoredState
from ops.main import main
# from ops.model import ActiveStatus

import charmhelpers.core.host as ch_host
import charmhelpers.core.templating as ch_templating
import interface_ceph_client.ceph_client as ceph_client

import ops_openstack.adapters
import ops_openstack.core
import ops_openstack.plugins.classes

logger = logging.getLogger(__name__)


class CephClientAdapter(ops_openstack.adapters.OpenStackOperRelationAdapter):
    """Adapter for ceph client interface."""

    @property
    def mon_hosts(self):
        """Sorted list of ceph mon addresses.

        :returns: Ceph MON addresses.
        :rtype: str
        """
        hosts = self.relation.get_relation_data()['mon_hosts']
        return ' '.join(sorted(hosts))

    @property
    def auth_supported(self):
        """Authentication type.

        :returns: Authentication type
        :rtype: str
        """
        return self.relation.get_relation_data()['auth']

    @property
    def key(self):
        """Key client should use when communicating with Ceph cluster.

        :returns: Key
        :rtype: str
        """
        return self.relation.get_relation_data()['key']


class CephNFSAdapters(
        ops_openstack.adapters.OpenStackRelationAdapters):
    """Collection of relation adapters."""

    relation_adapters = {
        'ceph-client': CephClientAdapter,
    }


class CephNfsCharm(CharmBase):
    """Ceph NFS Base Charm."""

    _stored = StoredState()
    PACKAGES = ['nfs-ganesha', 'ceph-common']

    CEPH_CAPABILITIES = [
        "mds", "allow *",
        "osd", "allow rw",
        "mon", "allow r, "
        "allow command \"auth del\", "
        "allow command \"auth caps\", "
        "allow command \"auth get\", "
        "allow command \"auth get-or-create\""]

    REQUIRED_RELATIONS = ['ceph-client', 'cluster']

    CEPH_CONFIG_PATH = Path('/etc/ceph')
    GANESHA_CONFIG_PATH = Path('/etc/ganesha')

    CEPH_GANESHA_CONFIG_PATH = CEPH_CONFIG_PATH / 'ganesha'
    CEPH_CONF = CEPH_GANESHA_CONFIG_PATH / 'ceph.conf'
    GANESHA_KEYRING = CEPH_GANESHA_CONFIG_PATH / 'ceph.client.ceph-ganesha.keyring'
    GANESHA_CONF = GANESHA_CONFIG_PATH / 'ganesha.conf'

    SERVICES = ['nfs-ganesha']

    RESTART_MAP = {
        str(GANESHA_CONF): SERVICES,
        str(CEPH_CONF): SERVICES,
        str(GANESHA_KEYRING): SERVICES}

    release = 'default'

    def __init__(self, framework):
        super().__init__(framework)
        # super().register_status_check(self.custom_status_check)
        logging.info("Using %s class", self.release)
        self._stored.set_default(
            is_started=False,
        )
        self.ceph_client = ceph_client.CephClientRequires(
            self,
            'ceph-client')
        self.adapters = CephNFSAdapters(
            (self.ceph_client,),
            self)
        self.framework.observe(
            self.ceph_client.on.broker_available,
            self.request_ceph_pool)
        self.framework.observe(
            self.ceph_client.on.pools_available,
            self.render_config)
        self.framework.observe(
            self.on.config_changed,
            self.request_ceph_pool)
        self.framework.observe(
            self.on.upgrade_charm,
            self.render_config)

    def config_get(self, key):
        """Retrieve config option.

        :returns: Value of the corresponding config option or None.
        :rtype: Any
        """
        return self.model.config.get(key)

    @property
    def pool_name(self):
        """The name of the default rbd data pool to be used for shares.

        :returns: Data pool name.
        :rtype: str
        """
        if self.config_get('rbd-pool-name'):
            pool_name = self.config_get('rbd-pool-name')
        else:
            pool_name = self.app.name
        return pool_name

    @property
    def client_name(self):
        return self.app.name

    def request_ceph_pool(self, event):
        """Request pools from Ceph cluster."""
        if not self.ceph_client.broker_available:
            logging.info("Cannot request ceph setup at this time")
            return
        try:
            bcomp_kwargs = self.get_bluestore_compression()
        except ValueError as e:
            # The end user has most likely provided a invalid value for
            # a configuration option. Just log the traceback here, the
            # end user will be notified by assess_status() called at
            # the end of the hook execution.
            logging.warn('Caught ValueError, invalid value provided for '
                         'configuration?: "{}"'.format(str(e)))
            return
        weight = self.config_get('ceph-pool-weight')
        replicas = self.config_get('ceph-osd-replication-count')

        logging.info("Requesting replicated pool")
        self.ceph_client.create_replicated_pool(
            name=self.pool_name,
            replicas=replicas,
            weight=weight,
            **bcomp_kwargs)
        logging.info("Requesting permissions")
        self.ceph_client.request_ceph_permissions(
            self.client_name,
            self.CEPH_CAPABILITIES)

    def refresh_request(self, event):
        """Re-request Ceph pools and render config."""
        self.render_config(event)
        self.request_ceph_pool(event)

    def render_config(self, event):
        """Render config and restart services if config files change."""
        if not self.ceph_client.pools_available:
            logging.info("Defering setup")
            event.defer()
            return

        self.CEPH_GANESHA_PATH.mkdir(
            exist_ok=True,
            mode=0o750)

        def daemon_reload_and_restart(service_name):
            subprocess.check_call(['systemctl', 'daemon-reload'])
            subprocess.check_call(['systemctl', 'restart', service_name])

        rfuncs = {}

        @ch_host.restart_on_change(self.RESTART_MAP, restart_functions=rfuncs)
        def _render_configs():
            for config_file in self.RESTART_MAP.keys():
                ch_templating.render(
                    os.path.basename(config_file),
                    config_file,
                    self.adapters)
        logging.info("Rendering config")
        _render_configs()
        logging.info("Setting started state")
        self._stored.is_started = True
        self.update_status()
        logging.info("on_pools_available: status updated")

    # def custom_status_check(self):
    #     """Custom update status checks."""
    #     if ch_host.is_container():
    #         return ops.model.BlockedStatus(
    #             'Charm cannot be deployed into a container')
    #     if self.peers.unit_count not in self.ALLOWED_UNIT_COUNTS:
    #         return ops.model.BlockedStatus(
    #             '{} is an invalid unit count'.format(self.peers.unit_count))
    #     return ops.model.ActiveStatus()


@ops_openstack.core.charm_class
class CephNFSCharmOcto(CephNfsCharm):
    """Ceph iSCSI Charm for Octopus."""

    _stored = StoredState()
    release = 'octopus'


if __name__ == '__main__':
    main(ops_openstack.core.get_charm_class_for_release())
