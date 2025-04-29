#!/usr/bin/env python3

# Copyright 2020 Canonical Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Charm for deploying and maintaining the Ceph iSCSI service."""

import copy
import logging
import os
import subprocess
import sys
import string
import socket
import secrets
from pathlib import Path

sys.path.append('lib')

from ops.framework import (
    StoredState,
)
from ops.main import main
import ops.model
import charmhelpers.core.host as ch_host
import charmhelpers.core.templating as ch_templating
import interface_ceph_client.ceph_client as ceph_client
import interface_ceph_iscsi_admin_access.admin_access as admin_access
import interface_ceph_iscsi_peer
import interface_tls_certificates.ca_client as ca_client

import ops_openstack.adapters
import ops_openstack.core
import ops_openstack.plugins.classes
import gwcli_client
import cryptography.hazmat.primitives.serialization as serialization
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


class GatewayClientPeerAdapter(
        ops_openstack.adapters.OpenStackOperRelationAdapter):
    """Adapter for Ceph iSCSI peer interface."""

    @property
    def gw_hosts(self):
        """List of peer addresses.

        :returns: Ceph iSCSI peer addresses.
        :rtype: str
        """
        hosts = self.relation.peer_addresses
        return ' '.join(sorted(hosts))

    @property
    def trusted_ips(self):
        """List of IP addresses permitted to use API.

        :returns: Ceph iSCSI trusted ips.
        :rtype: str
        """
        ips = copy.deepcopy(self.allowed_ips)
        ips.extend(self.relation.peer_addresses)
        return ','.join(sorted(ips))


class AdminAccessAdapter(
        ops_openstack.adapters.OpenStackOperRelationAdapter):

    @property
    def trusted_ips(self):
        """List of IP addresses permitted to use API.

        :returns: Ceph iSCSI clients
        :rtype: str
        """
        return ','.join(sorted(self.relation.client_addresses))


class TLSCertificatesAdapter(
        ops_openstack.adapters.OpenStackOperRelationAdapter):
    """Adapter for Ceph TLS Certificates interface."""

    @property
    def enable_tls(self):
        """Whether to enable TLS.

        :returns: Whether TLS should be enabled
        :rtype: bool
        """
        try:
            return bool(self.relation.application_certificate)
        except ca_client.CAClientError:
            return False


class CephISCSIGatewayAdapters(
        ops_openstack.adapters.OpenStackRelationAdapters):
    """Collection of relation adapters."""

    relation_adapters = {
        'ceph-client': CephClientAdapter,
        'cluster': GatewayClientPeerAdapter,
        'certificates': TLSCertificatesAdapter,
        'admin-access': AdminAccessAdapter,
    }


class CephISCSIGatewayCharmBase(
        ops_openstack.plugins.classes.BaseCephClientCharm):
    """Ceph iSCSI Base Charm."""

    _stored = StoredState()
    PACKAGES = ['ceph-iscsi', 'tcmu-runner', 'ceph-common']
    CEPH_CAPABILITIES = [
        "osd", "allow *",
        "mon", "allow *",
        "mgr", "allow r"]

    DEFAULT_TARGET = "iqn.2003-01.com.ubuntu.iscsi-gw:iscsi-igw"
    REQUIRED_RELATIONS = ['ceph-client', 'cluster']

    ALLOWED_UNIT_COUNTS = [2, 4]

    CEPH_CONFIG_PATH = Path('/etc/ceph')
    CEPH_ISCSI_CONFIG_PATH = CEPH_CONFIG_PATH / 'iscsi'
    GW_CONF = CEPH_CONFIG_PATH / 'iscsi-gateway.cfg'
    CEPH_CONF = CEPH_ISCSI_CONFIG_PATH / 'ceph.conf'
    GW_KEYRING = CEPH_ISCSI_CONFIG_PATH / 'ceph.client.ceph-iscsi.keyring'
    TLS_KEY_PATH = CEPH_CONFIG_PATH / 'iscsi-gateway.key'
    TLS_PUB_KEY_PATH = CEPH_CONFIG_PATH / 'iscsi-gateway-pub.key'
    TLS_CERT_PATH = CEPH_CONFIG_PATH / 'iscsi-gateway.crt'
    TLS_KEY_AND_CERT_PATH = CEPH_CONFIG_PATH / 'iscsi-gateway.pem'
    TLS_CA_CERT_PATH = Path(
        '/usr/local/share/ca-certificates/vault_ca_cert.crt')

    GW_SERVICES = ['rbd-target-api', 'rbd-target-gw']

    RESTART_MAP = {
        str(GW_CONF): GW_SERVICES,
        str(CEPH_CONF): GW_SERVICES,
        str(GW_KEYRING): GW_SERVICES}

    release = 'default'

    def __init__(self, framework):
        """Setup adapters and observers."""
        super().__init__(framework)
        super().register_status_check(self.custom_status_check)
        logging.info("Using %s class", self.release)
        self._stored.set_default(
            target_created=False,
            enable_tls=False)
        self.ceph_client = ceph_client.CephClientRequires(
            self,
            'ceph-client')
        self.peers = interface_ceph_iscsi_peer.CephISCSIGatewayPeers(
            self,
            'cluster')
        self.admin_access = \
            admin_access.CephISCSIAdminAccessProvides(
                self,
                'admin-access')
        self.ca_client = ca_client.CAClient(
            self,
            'certificates')
        self.adapters = CephISCSIGatewayAdapters(
            (self.ceph_client, self.peers, self.ca_client, self.admin_access),
            self)
        self.framework.observe(
            self.admin_access.on.admin_access_request,
            self.publish_admin_access_info)
        self.framework.observe(
            self.ceph_client.on.broker_available,
            self.request_ceph_pool)
        self.framework.observe(
            self.ceph_client.on.pools_available,
            self.render_config)
        self.framework.observe(
            self.peers.on.has_peers,
            self.on_has_peers)
        self.framework.observe(
            self.peers.on.allowed_ips_changed,
            self.render_config)
        self.framework.observe(
            self.ca_client.on.tls_app_config_ready,
            self.on_tls_app_config_ready)
        self.framework.observe(
            self.ca_client.on.ca_available,
            self.on_ca_available)
        self.framework.observe(
            self.on.config_changed,
            self.render_config)
        self.framework.observe(
            self.on.config_changed,
            self.request_ceph_pool)
        self.framework.observe(
            self.on.upgrade_charm,
            self.render_config)
        self.framework.observe(
            self.on.create_target_action,
            self.on_create_target_action)
        self.framework.observe(
            self.on.add_trusted_ip_action,
            self.on_add_trusted_ip_action)

    def on_install(self, event):
        """Install packages and check substrate is supported."""
        if ch_host.is_container():
            logging.info("Installing into a container is not supported")
            self.update_status()
        else:
            self.install_pkgs()

    def on_has_peers(self, event):
        """Setup and share admin password."""
        logging.info("Unit has peers")
        if self.unit.is_leader() and not self.peers.admin_password:
            logging.info("Setting admin password")
            alphabet = string.ascii_letters + string.digits
            password = ''.join(secrets.choice(alphabet) for i in range(8))
            self.peers.set_admin_password(password)
        self.publish_admin_access_info(event)

    def config_get(self, key):
        """Retrieve config option.

        :returns: Value of the corresponding config option or None.
        :rtype: Any
        """
        return self.model.config.get(key)

    @property
    def data_pool_name(self):
        """The name of the default rbd data pool to be used by targets.

        :returns: Data pool name.
        :rtype: str
        """
        if self.config_get('rbd-pool-name'):
            pool_name = self.config_get('rbd-pool-name')
        else:
            pool_name = self.app.name
        return pool_name

    @property
    def metadata_pool_name(self):
        """The name of the default rbd metadata pool to be used by targets.

        :returns: Metadata pool name.
        :rtype: str
        """
        return (self.config_get('ec-rbd-metadata-pool') or
                "{}-metadata".format(self.app.name))

    def request_ceph_pool(self, event):
        """Request pools from Ceph cluster."""
        if not self.ceph_client.broker_available:
            logging.info("Cannot request ceph setup at this time")
            return
        logging.info("Requesting replicated pool")
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
        self.ceph_client.create_replicated_pool(
            self.config_get('gateway-metadata-pool'))
        weight = self.config_get('ceph-pool-weight')
        replicas = self.config_get('ceph-osd-replication-count')
        if self.config_get('pool-type') == 'erasure-coded':
            # General EC plugin config
            plugin = self.config_get('ec-profile-plugin')
            technique = self.config_get('ec-profile-technique')
            device_class = self.config_get('ec-profile-device-class')
            bdm_k = self.config_get('ec-profile-k')
            bdm_m = self.config_get('ec-profile-m')
            # LRC plugin config
            bdm_l = self.config_get('ec-profile-locality')
            crush_locality = self.config_get('ec-profile-crush-locality')
            # SHEC plugin config
            bdm_c = self.config_get('ec-profile-durability-estimator')
            # CLAY plugin config
            bdm_d = self.config_get('ec-profile-helper-chunks')
            scalar_mds = self.config_get('ec-profile-scalar-mds')
            # Profile name
            profile_name = (
                self.config_get('ec-profile-name') or
                "{}-profile".format(self.app.name)
            )
            # Metadata sizing is approximately 1% of overall data weight
            # but is in effect driven by the number of rbd's rather than
            # their size - so it can be very lightweight.
            metadata_weight = weight * 0.01
            # Resize data pool weight to accomodate metadata weight
            weight = weight - metadata_weight
            # Create erasure profile
            self.ceph_client.create_erasure_profile(
                name=profile_name,
                k=bdm_k, m=bdm_m,
                lrc_locality=bdm_l,
                lrc_crush_locality=crush_locality,
                shec_durability_estimator=bdm_c,
                clay_helper_chunks=bdm_d,
                clay_scalar_mds=scalar_mds,
                device_class=device_class,
                erasure_type=plugin,
                erasure_technique=technique
            )

            # Create EC data pool
            self.ceph_client.create_erasure_pool(
                name=self.data_pool_name,
                erasure_profile=profile_name,
                weight=weight,
                allow_ec_overwrites=True,
                **bcomp_kwargs
            )
            self.ceph_client.create_replicated_pool(
                name=self.metadata_pool_name,
                weight=metadata_weight
            )
        else:
            self.ceph_client.create_replicated_pool(
                name=self.data_pool_name,
                replicas=replicas,
                weight=weight,
                **bcomp_kwargs)
        logging.info("Requesting permissions")
        self.ceph_client.request_ceph_permissions(
            'ceph-iscsi',
            self.CEPH_CAPABILITIES)
        self.ceph_client.request_osd_settings({
            'osd heartbeat grace': 20,
            'osd heartbeat interval': 5})

    def refresh_request(self, event):
        """Re-request Ceph pools and render config."""
        self.render_config(event)
        self.request_ceph_pool(event)

    def render_config(self, event):
        """Render config and restart services if config files change."""
        if not self.peers.admin_password:
            logging.info("Defering setup")
            event.defer()
            return
        if not self.ceph_client.pools_available:
            logging.info("Defering setup")
            event.defer()
            return

        self.CEPH_ISCSI_CONFIG_PATH.mkdir(
            exist_ok=True,
            mode=0o750)

        def daemon_reload_and_restart(service_name):
            subprocess.check_call(['systemctl', 'daemon-reload'])
            subprocess.check_call(['systemctl', 'restart', service_name])

        rfuncs = {
            'rbd-target-api': daemon_reload_and_restart}

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
        self.peers.announce_ready()
        self._stored.is_started = True
        self.update_status()
        logging.info("on_pools_available: status updated")

    def on_ca_available(self, event):
        """Request TLS certificates."""
        addresses = set()
        for binding_name in ['public', 'cluster']:
            binding = self.model.get_binding(binding_name)
            addresses.add(binding.network.ingress_address)
            addresses.add(binding.network.bind_address)
        sans = [str(s) for s in addresses]
        sans.append(socket.gethostname())
        self.ca_client.request_application_certificate(socket.getfqdn(), sans)

    def on_tls_app_config_ready(self, event):
        """Configure TLS."""
        self.TLS_KEY_PATH.write_bytes(
            self.ca_client.application_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.TraditionalOpenSSL,
                encryption_algorithm=serialization.NoEncryption()))
        self.TLS_CERT_PATH.write_bytes(
            self.ca_client.application_certificate.public_bytes(
                encoding=serialization.Encoding.PEM))
        self.TLS_CA_CERT_PATH.write_bytes(
            self.ca_client.ca_certificate.public_bytes(
                encoding=serialization.Encoding.PEM))
        self.TLS_KEY_AND_CERT_PATH.write_bytes(
            self.ca_client.application_certificate.public_bytes(
                encoding=serialization.Encoding.PEM) +
            b'\n' +
            self.ca_client.application_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.TraditionalOpenSSL,
                encryption_algorithm=serialization.NoEncryption())
        )
        self.TLS_PUB_KEY_PATH.write_bytes(
            self.ca_client.application_key.public_key().public_bytes(
                format=serialization.PublicFormat.SubjectPublicKeyInfo,
                encoding=serialization.Encoding.PEM))
        subprocess.check_call(['update-ca-certificates'])
        self._stored.enable_tls = True
        # Endpoint has switch to TLS, need to inform users.
        self.publish_admin_access_info(event)
        self.render_config(event)

    def publish_admin_access_info(self, event):
        """Publish creds and endpoint to related charms"""
        if not self.peers.admin_password:
            logging.info("Defering setup")
            event.defer()
            return
        if self._stored.enable_tls:
            scheme = 'https'
        else:
            scheme = 'http'
        self.admin_access.publish_gateway(
            socket.getfqdn(),
            'admin',
            self.peers.admin_password,
            scheme)

    def custom_status_check(self):
        """Custom update status checks."""
        if ch_host.is_container():
            return ops.model.BlockedStatus(
                'Charm cannot be deployed into a container')
        if self.peers.unit_count not in self.ALLOWED_UNIT_COUNTS:
            return ops.model.BlockedStatus(
                '{} is an invalid unit count'.format(self.peers.unit_count))
        return ops.model.ActiveStatus()

    # Actions

    def on_add_trusted_ip_action(self, event):
        """Add an IP to the allowed list for API access."""
        if self.unit.is_leader():
            ips = event.params.get('ips').split()
            self.peers.set_allowed_ips(
                ips,
                append=not event.params['overwrite'])
            self.render_config(event)
        else:
            event.fail("Action must be run on leader")

    def calculate_target_pools(self, event):
        if event.params['ec-rbd-metadata-pool']:
            ec_rbd_metadata_pool = event.params['ec-rbd-metadata-pool']
            rbd_pool_name = event.params['rbd-pool-name']
        elif event.params['rbd-pool-name']:
            ec_rbd_metadata_pool = None
            rbd_pool_name = event.params['rbd-pool-name']
        # Action did not specify pools to derive them from charm config.
        elif self.model.config['pool-type'] == 'erasure-coded':
            ec_rbd_metadata_pool = self.metadata_pool_name
            rbd_pool_name = self.data_pool_name
        else:
            ec_rbd_metadata_pool = None
            rbd_pool_name = self.data_pool_name
        return rbd_pool_name, ec_rbd_metadata_pool

    def on_create_target_action(self, event):
        """Create an iSCSI target."""
        gw_client = gwcli_client.GatewayClient()
        target = event.params.get('iqn', self.DEFAULT_TARGET)
        gateway_units = event.params.get(
            'gateway-units',
            [u for u in self.peers.ready_peer_details.keys()])
        rbd_pool_name, ec_rbd_metadata_pool = self.calculate_target_pools(
            event)
        if ec_rbd_metadata_pool:
            # When using erasure-coded pools the image needs to be pre-created
            # as the gwcli does not currently handle the creation.
            cmd = [
                'rbd',
                '--user', 'ceph-iscsi',
                '--conf', str(self.CEPH_CONF),
                'create',
                '--size', event.params['image-size'],
                '{}/{}'.format(
                    ec_rbd_metadata_pool,
                    event.params['image-name']),
                '--data-pool', rbd_pool_name]
            logging.info(cmd)
            subprocess.check_call(cmd)
            target_pool = ec_rbd_metadata_pool
        else:
            target_pool = rbd_pool_name
        gw_client.create_target(target)
        for gw_unit, gw_config in self.peers.ready_peer_details.items():
            added_gateways = []
            if gw_unit in gateway_units:
                gw_client.add_gateway_to_target(
                    target,
                    gw_config['ip'],
                    gw_config['fqdn'])
                added_gateways.append(gw_unit)
        gw_client.create_pool(
            target_pool,
            event.params['image-name'],
            event.params['image-size'])
        gw_client.add_client_to_target(
            target,
            event.params['client-initiatorname'])
        gw_client.add_client_auth(
            target,
            event.params['client-initiatorname'],
            event.params['client-username'],
            event.params['client-password'])
        gw_client.add_disk_to_client(
            target,
            event.params['client-initiatorname'],
            target_pool,
            event.params['image-name'])
        event.set_results({'iqn': target})


@ops_openstack.core.charm_class
class CephISCSIGatewayCharmOcto(CephISCSIGatewayCharmBase):
    """Ceph iSCSI Charm for Octopus."""

    _stored = StoredState()
    release = 'octopus'


if __name__ == '__main__':
    main(ops_openstack.core.get_charm_class_for_release())
