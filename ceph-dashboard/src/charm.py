#!/usr/bin/env python3
# Copyright 2021 Canonical
# See LICENSE file for licensing details.
#
# Learn more at: https://juju.is/docs/sdk

"""Charm for the Ceph Dashboard."""

import logging
import tempfile

from ops.framework import StoredState
from ops.main import main
from ops.model import ActiveStatus, BlockedStatus, StatusBase
from ops.charm import ActionEvent
from typing import List, Union, Tuple

import base64
import interface_tls_certificates.ca_client as ca_client
import re
import secrets
import socket
import string
import subprocess
import ops_openstack.plugins.classes
import interface_dashboard
import interface_api_endpoints
import cryptography.hazmat.primitives.serialization as serialization
import charms_ceph.utils as ceph_utils
import charmhelpers.core.host as ch_host

from pathlib import Path

logger = logging.getLogger(__name__)

TLS_Config = Tuple[Union[bytes, None], Union[bytes, None], Union[bytes, None]]


class CephDashboardCharm(ops_openstack.core.OSBaseCharm):
    """Ceph Dashboard charm."""

    _stored = StoredState()
    PACKAGES = ['ceph-mgr-dashboard']
    CEPH_CONFIG_PATH = Path('/etc/ceph')
    TLS_KEY_PATH = CEPH_CONFIG_PATH / 'ceph-dashboard.key'
    TLS_PUB_KEY_PATH = CEPH_CONFIG_PATH / 'ceph-dashboard-pub.key'
    TLS_CERT_PATH = CEPH_CONFIG_PATH / 'ceph-dashboard.crt'
    TLS_KEY_AND_CERT_PATH = CEPH_CONFIG_PATH / 'ceph-dashboard.pem'
    TLS_CA_CERT_PATH = Path(
        '/usr/local/share/ca-certificates/vault_ca_cert_dashboard.crt')
    TLS_PORT = 8443

    class CharmCephOption():
        """Manage a charm option to ceph command to manage that option"""

        def __init__(self, charm_option_name, ceph_option_name,
                     min_version=None):
            self.charm_option_name = charm_option_name
            self.ceph_option_name = ceph_option_name
            self.min_version = min_version

        def is_supported(self) -> bool:
            """Is the option supported on this unit"""
            if self.min_version:
                return self.minimum_supported(self.min_version)
            return True

        def minimum_supported(self, supported_version: str) -> bool:
            """Check if installed Ceph release is >= to supported_version"""
            return ch_host.cmp_pkgrevno('ceph-common', supported_version) < 1

        def convert_option(self, value: Union[bool, str, int]) -> List[str]:
            """Convert a value to the corresponding value part of the ceph
               dashboard command"""
            return [str(value)]

        def ceph_command(self, value: List[str]) -> List[str]:
            """Shell command to set option to desired value"""
            cmd = ['ceph', 'dashboard', self.ceph_option_name]
            cmd.extend(self.convert_option(value))
            return cmd

    class DebugOption(CharmCephOption):

        def convert_option(self, value):
            """Convert charm True/False to enable/disable"""
            if value:
                return ['enable']
            else:
                return ['disable']

    class MOTDOption(CharmCephOption):

        def convert_option(self, value):
            """Split motd charm option into ['severity', 'time', 'message']"""
            if value:
                return value.split('|')
            else:
                return ['clear']

    CHARM_TO_CEPH_OPTIONS = [
        DebugOption('debug', 'debug'),
        CharmCephOption(
            'enable-password-policy',
            'set-pwd-policy-enabled'),
        CharmCephOption(
            'password-policy-check-length',
            'set-pwd-policy-check-length-enabled'),
        CharmCephOption(
            'password-policy-check-oldpwd',
            'set-pwd-policy-check-oldpwd-enabled'),
        CharmCephOption(
            'password-policy-check-username',
            'set-pwd-policy-check-username-enabled'),
        CharmCephOption(
            'password-policy-check-exclusion-list',
            'set-pwd-policy-check-exclusion-list-enabled'),
        CharmCephOption(
            'password-policy-check-complexity',
            'set-pwd-policy-check-complexity-enabled'),
        CharmCephOption(
            'password-policy-check-sequential-chars',
            'set-pwd-policy-check-sequential-chars-enabled'),
        CharmCephOption(
            'password-policy-check-repetitive-chars',
            'set-pwd-policy-check-repetitive-chars-enabled'),
        CharmCephOption(
            'password-policy-min-length',
            'set-pwd-policy-min-length'),
        CharmCephOption(
            'password-policy-min-complexity',
            'set-pwd-policy-min-complexity'),
        CharmCephOption(
            'audit-api-enabled',
            'set-audit-api-enabled'),
        CharmCephOption(
            'audit-api-log-payload',
            'set-audit-api-log-payload'),
        MOTDOption(
            'motd',
            'motd',
            min_version='15.2.14')
    ]

    def __init__(self, *args) -> None:
        """Setup adapters and observers."""
        super().__init__(*args)
        super().register_status_check(self.check_dashboard)
        self.framework.observe(
            self.on.config_changed,
            self._configure_dashboard)
        self.mon = interface_dashboard.CephDashboardRequires(
            self,
            'dashboard')
        self.ca_client = ca_client.CAClient(
            self,
            'certificates')
        self.framework.observe(
            self.mon.on.mon_ready,
            self._configure_dashboard)
        self.framework.observe(
            self.ca_client.on.ca_available,
            self._on_ca_available)
        self.framework.observe(
            self.ca_client.on.tls_server_config_ready,
            self._configure_dashboard)
        self.framework.observe(self.on.add_user_action, self._add_user_action)
        self.ingress = interface_api_endpoints.APIEndpointsRequires(
            self,
            'loadbalancer',
            {
                'endpoints': [{
                    'service-type': 'ceph-dashboard',
                    'frontend-port': self.TLS_PORT,
                    'backend-port': self.TLS_PORT,
                    'backend-ip': self._get_bind_ip(),
                    'check-type': 'httpd'}]})
        self._stored.set_default(is_started=False)

    def _on_ca_available(self, _) -> None:
        """Request TLS certificates."""
        addresses = set()
        for binding_name in ['public']:
            binding = self.model.get_binding(binding_name)
            addresses.add(binding.network.ingress_address)
            addresses.add(binding.network.bind_address)
        sans = [str(s) for s in addresses]
        sans.append(socket.gethostname())
        if self.config.get('public-hostname'):
            sans.append(self.config.get('public-hostname'))
        self.ca_client.request_server_certificate(socket.getfqdn(), sans)

    def check_dashboard(self) -> StatusBase:
        """Check status of dashboard"""
        self._stored.is_started = ceph_utils.is_dashboard_enabled()
        if self._stored.is_started:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            result = sock.connect_ex((self._get_bind_ip(), self.TLS_PORT))
            if result == 0:
                return ActiveStatus()
            else:
                return BlockedStatus(
                    'Dashboard not responding')
        else:
            return BlockedStatus(
                'Dashboard is not enabled')
        return ActiveStatus()

    def kick_dashboard(self) -> None:
        """Disable and re-enable dashboard"""
        ceph_utils.mgr_disable_dashboard()
        ceph_utils.mgr_enable_dashboard()

    def _run_cmd(self, cmd: List[str]) -> None:
        """Run command in subprocess

        `cmd` The command to run
        """
        try:
            subprocess.check_output(cmd, stderr=subprocess.STDOUT)
        except subprocess.CalledProcessError as exc:
            logging.exception("Command failed: {}".format(exc.output))

    def _apply_ceph_config_from_charm_config(self) -> None:
        """Read charm config and apply settings to dashboard config"""
        for option in self.CHARM_TO_CEPH_OPTIONS:
            try:
                value = self.config[option.charm_option_name]
            except KeyError:
                logging.error(
                    "Unknown charm option {}, skipping".format(
                        option.charm_option_name))
                continue
            if option.is_supported():
                self._run_cmd(option.ceph_command(value))
            else:
                logging.warning(
                    "Skipping charm option {}, not supported".format(
                        option.charm_option_name))

    def _configure_dashboard(self, _) -> None:
        """Configure dashboard"""
        if not self.mon.mons_ready:
            logging.info("Not configuring dashboard, mons not ready")
            return
        if self.unit.is_leader() and not ceph_utils.is_dashboard_enabled():
            ceph_utils.mgr_enable_dashboard()
        self._apply_ceph_config_from_charm_config()
        self._configure_tls()
        ceph_utils.mgr_config_set(
            'mgr/dashboard/{hostname}/server_addr'.format(
                hostname=socket.gethostname()),
            str(self._get_bind_ip()))
        self.update_status()

    def _get_bind_ip(self) -> str:
        """Return the IP to bind the dashboard to"""
        binding = self.model.get_binding('public')
        return str(binding.network.ingress_address)

    def _get_tls_from_config(self) -> TLS_Config:
        """Extract TLS config from charm config."""
        raw_key = self.config.get("ssl_key")
        raw_cert = self.config.get("ssl_cert")
        raw_ca_cert = self.config.get("ssl_ca")
        if not (raw_key and raw_key):
            return None, None, None
        key = base64.b64decode(raw_key)
        cert = base64.b64decode(raw_cert)
        if raw_ca_cert:
            ca_cert = base64.b64decode(raw_ca_cert)
        else:
            ca_cert = None
        return key, cert, ca_cert

    def _get_tls_from_relation(self) -> TLS_Config:
        """Extract TLS config from certificatees relation."""
        if not self.ca_client.is_server_cert_ready:
            return None, None, None
        key = self.ca_client.server_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption())
        cert = self.ca_client.server_certificate.public_bytes(
            encoding=serialization.Encoding.PEM)
        ca_cert = (
            self.ca_client.ca_certificate.public_bytes(
                encoding=serialization.Encoding.PEM) +
            self.ca_client.root_ca_chain.public_bytes(
                encoding=serialization.Encoding.PEM))
        return key, cert, ca_cert

    def _configure_tls(self) -> None:
        """Configure TLS."""
        logging.debug("Attempting to collect TLS config from relation")
        key, cert, ca_cert = self._get_tls_from_relation()
        if not (key and cert):
            logging.debug("Attempting to collect TLS config from charm "
                          "config")
            key, cert, ca_cert = self._get_tls_from_config()
        if not (key and cert):
            logging.warn(
                "Not configuring TLS, not all data present")
            return
        self.TLS_KEY_PATH.write_bytes(key)
        self.TLS_CERT_PATH.write_bytes(cert)
        if ca_cert:
            self.TLS_CA_CERT_PATH.write_bytes(ca_cert)
            subprocess.check_call(['update-ca-certificates'])

        hostname = socket.gethostname()
        ceph_utils.dashboard_set_ssl_certificate(
            self.TLS_CERT_PATH,
            hostname=hostname)
        ceph_utils.dashboard_set_ssl_certificate_key(
            self.TLS_KEY_PATH,
            hostname=hostname)
        if self.unit.is_leader():
            ceph_utils.mgr_config_set(
                'mgr/dashboard/standby_behaviour',
                'redirect')
            ceph_utils.mgr_config_set(
                'mgr/dashboard/ssl',
                'true')
            # Set the ssl artifacte without the hostname which appears to
            # be required even though they aren't used.
            ceph_utils.dashboard_set_ssl_certificate(
                self.TLS_CERT_PATH)
            ceph_utils.dashboard_set_ssl_certificate_key(
                self.TLS_KEY_PATH)
        self.kick_dashboard()

    def _gen_user_password(self, length: int = 8) -> str:
        """Generate a password"""
        alphabet = string.ascii_letters + string.digits
        return ''.join(secrets.choice(alphabet) for i in range(length))

    def _add_user_action(self, event: ActionEvent) -> None:
        """Create a user"""
        username = event.params["username"]
        role = event.params["role"]
        if not all([username, role]):
            event.fail("Config missing")
        else:
            password = self._gen_user_password()
            with tempfile.NamedTemporaryFile(mode='w', delete=True) as fp:
                fp.write(password)
                fp.flush()
                cmd_out = subprocess.check_output([
                    'ceph', 'dashboard', 'ac-user-create', '--enabled',
                    '-i', fp.name, username, role]).decode('UTF-8')
                if re.match('User.*already exists', cmd_out):
                    event.fail("User already exists")
                else:
                    event.set_results({"password": password})

if __name__ == "__main__":
    main(CephDashboardCharm)
