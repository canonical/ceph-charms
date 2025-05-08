#!/usr/bin/env python3
# Copyright 2021 Canonical
# See LICENSE file for licensing details.
#
# Learn more at: https://juju.is/docs/sdk

"""Charm for the Ceph Dashboard."""

import json
import logging
import tempfile

from ops.framework import StoredState
from ops.main import main
from ops.model import ActiveStatus, BlockedStatus, StatusBase
from ops.charm import ActionEvent
from typing import List, Union, Tuple

import base64
import interface_tls_certificates.ca_client as ca_client
import interface_openstack_loadbalancer.loadbalancer as ops_lb_interface
import re
import secrets
import socket
import string
import subprocess
import tenacity
import ops_openstack.plugins.classes
import interface_ceph_iscsi_admin_access.admin_access as admin_access
import interface_dashboard
import interface_grafana_dashboard
import interface_http
import interface_radosgw_user
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
    TLS_CA_CERT_DIR = Path('/usr/local/share/ca-certificates')
    TLS_VAULT_CA_CERT_PATH = TLS_CA_CERT_DIR / 'vault_juju_ca_cert.crt'
    TLS_CHARM_CA_CERT_PATH = TLS_CA_CERT_DIR / 'charm_config_juju_ca_cert.crt'
    TLS_PORT = 8443
    DASH_DIR = Path('src/dashboards')
    LB_SERVICE_NAME = "ceph-dashboard"

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
            return ch_host.cmp_pkgrevno('ceph-common', supported_version) >= 0

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
        self.radosgw_user = interface_radosgw_user.RadosGWUserRequires(
            self,
            'radosgw-dashboard',
            request_system_role=True)
        self.iscsi_user = admin_access.CephISCSIAdminAccessRequires(
            self,
            'iscsi-dashboard')
        self.framework.observe(
            self.mon.on.mon_ready,
            self._configure_dashboard)
        self.framework.observe(
            self.ca_client.on.ca_available,
            self._configure_dashboard)
        self.framework.observe(
            self.ca_client.on.tls_server_config_ready,
            self._configure_dashboard)
        self.framework.observe(
            self.radosgw_user.on.gw_user_ready,
            self._configure_dashboard)
        self.framework.observe(
            self.iscsi_user.on.admin_access_ready,
            self._configure_dashboard)
        self.framework.observe(self.on.add_user_action, self._add_user_action)
        self.framework.observe(
            self.on.delete_user_action,
            self._delete_user_action)
        self.ingress = ops_lb_interface.OSLoadbalancerRequires(
            self,
            'loadbalancer')
        self.grafana_dashboard = \
            interface_grafana_dashboard.GrafanaDashboardProvides(
                self,
                'grafana-dashboard')
        self.alertmanager = interface_http.HTTPRequires(
            self,
            'alertmanager-service')
        self.prometheus = interface_http.HTTPRequires(
            self,
            'prometheus')
        self.framework.observe(
            self.grafana_dashboard.on.dash_ready,
            self._configure_dashboard)
        self.framework.observe(
            self.alertmanager.on.http_ready,
            self._configure_dashboard)
        self.framework.observe(
            self.prometheus.on.http_ready,
            self._configure_dashboard)
        self.framework.observe(
            self.ingress.on.lb_relation_ready,
            self._request_loadbalancer)
        self.framework.observe(
            self.ingress.on.lb_configured,
            self._configure_dashboard)
        self._stored.set_default(is_started=False)

    def _request_loadbalancer(self, _) -> None:
        """Send request to create loadbalancer"""
        self.ingress.request_loadbalancer(
            self.LB_SERVICE_NAME,
            self.TLS_PORT,
            self.TLS_PORT,
            self._get_bind_ip(),
            'httpd')

    def _register_dashboards(self) -> None:
        """Register all dashboards with grafana"""
        for dash_file in self.DASH_DIR.glob("*.json"):
            self.grafana_dashboard.register_dashboard(
                dash_file.stem,
                json.loads(dash_file.read_text()))
            logging.info(
                "register_grafana_dashboard: {}".format(dash_file))

    def _update_legacy_radosgw_creds(self, access_key: str,
                                     secret_key: str) -> None:
        """Update dashboard db with access & secret key for rados gateways.

        This method uses the legacy format which only supports one gateway.
        """
        self._apply_file_setting('set-rgw-api-access-key', access_key)
        self._apply_file_setting('set-rgw-api-secret-key', secret_key)

    def _update_multi_radosgw_creds(self, creds: str) -> None:
        """Update dashboard db with access & secret key for rados gateway."""
        access_keys = {c['daemon_id']: c['access_key'] for c in creds}
        secret_keys = {c['daemon_id']: c['secret_key'] for c in creds}
        self._apply_file_setting(
            'set-rgw-api-access-key',
            json.dumps(access_keys))
        self._apply_file_setting(
            'set-rgw-api-secret-key',
            json.dumps(secret_keys))

    def _support_multiple_gateways(self) -> bool:
        """Check if version of dashboard supports multiple rados gateways"""
        return ch_host.cmp_pkgrevno('ceph-common', '16.0') > 0

    def _manage_radosgw(self) -> None:
        """Register rados gateways in dashboard db"""
        if self.unit.is_leader():
            creds = self.radosgw_user.get_user_creds()
            cred_count = len(set([
                (c['access_key'], c['secret_key'])
                for c in creds]))
            if cred_count < 1:
                logging.info("No object gateway creds found")
                return
            if self._support_multiple_gateways():
                self._update_multi_radosgw_creds(creds)
            else:
                if cred_count > 1:
                    logging.error(
                        "Cannot enable object gateway support. Ceph release "
                        "does not support multiple object gateways in the "
                        "dashboard")
                else:
                    self._update_legacy_radosgw_creds(
                        creds[0]['access_key'],
                        creds[0]['secret_key'])

    def request_certificates(self) -> None:
        """Request TLS certificates."""
        if not self.ca_client.is_joined:
            logging.debug(
                "Cannot request certificates, relation not present.")
            return
        addresses = set()
        if self.ingress.relations:
            lb_response = self.ingress.get_frontend_data()
            if lb_response:
                lb_config = lb_response[self.LB_SERVICE_NAME]
                addresses.update(
                    [i for d in lb_config.values() for i in d['ip']])
            else:
                logging.debug(
                    ("Defering certificate request until loadbalancer has "
                     "responded."))
                return
        for binding_name in ['public']:
            binding = self.model.get_binding(binding_name)
            addresses.add(binding.network.ingress_address)
            addresses.add(binding.network.bind_address)
        sans = [str(s) for s in addresses]
        sans.append(socket.gethostname())
        if self.config.get('public-hostname'):
            sans.append(self.config.get('public-hostname'))
        self.ca_client.request_server_certificate(socket.getfqdn(), sans)

    def _check_for_certs(self) -> bool:
        """Check that charm has TLS data it needs"""
        # Check charm config for TLS data
        key, cert, _ = self._get_tls_from_config()
        if key and cert:
            return True
        # Check relation for TLS data
        try:
            self.ca_client.server_key
            return True
        except ca_client.CAClientError:
            return False

    def _check_dashboard_responding(self) -> bool:
        """Check the dashboard port is open"""

        @tenacity.retry(wait=tenacity.wait_fixed(2),
                        stop=tenacity.stop_after_attempt(30), reraise=True)
        def _check_port(ip, port):
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            result = sock.connect_ex((ip, port))
            assert result == 0

        try:
            _check_port(self._get_bind_ip(), self.TLS_PORT)
            return True
        except AssertionError:
            return False

    def _check_grafana_config(self) -> bool:
        """Check that garfana-api is set if the grafana is in use."""
        if self.grafana_dashboard.dashboard_relation:
            return bool(self.config.get('grafana-api-url'))
        else:
            return True

    def check_dashboard(self) -> StatusBase:
        """Check status of dashboard"""
        checks = [
            (ceph_utils.is_dashboard_enabled, 'Dashboard is not enabled'),
            (self._check_for_certs, ('No certificates found. Please add a '
                                     'certifcates relation or provide via '
                                     'charm config')),
            (self._check_grafana_config, 'Charm config option grafana-api-url '
                                         'not set'),
            (self._check_dashboard_responding, 'Dashboard not responding')]
        for check_f, msg in checks:
            if not check_f():
                return BlockedStatus(msg)
        return ActiveStatus()

    def kick_dashboard(self) -> None:
        """Disable and re-enable dashboard"""
        ceph_utils.mgr_disable_dashboard()
        ceph_utils.mgr_enable_dashboard()

    def _run_cmd(self, cmd: List[str]) -> str:
        """Run command in subprocess

        `cmd` The command to run
        """
        try:
            output = subprocess.check_output(cmd, stderr=subprocess.STDOUT)
            return output.decode('UTF-8')
        except subprocess.CalledProcessError as exc:
            logging.exception("Command failed: {}".format(exc.output))

    def _apply_setting(self, ceph_setting: str, value: List[str]) -> str:
        """Apply a dashboard setting"""
        cmd = ['ceph', 'dashboard', ceph_setting]
        cmd.extend(value)
        return self._run_cmd(cmd)

    def _apply_file_setting(self, ceph_setting: str,
                            file_contents: str,
                            extra_args: List[str] = None) -> None:
        """Apply a setting via a file"""
        with tempfile.NamedTemporaryFile(mode='w', delete=True) as _file:
            _file.write(file_contents)
            _file.flush()
            settings = ['-i', _file.name]
            if extra_args:
                settings.extend(extra_args)
            self._apply_setting(ceph_setting, settings)

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
        self.request_certificates()
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
        if self.unit.is_leader():
            grafana_ep = self.config.get('grafana-api-url')
            if grafana_ep:
                self._run_cmd([
                    'ceph', 'dashboard', 'set-grafana-api-url', grafana_ep])
            alertmanager_conn = self.alertmanager.get_service_ep_data()
            if alertmanager_conn:
                alertmanager_ep = 'http://{}:{}'.format(
                    alertmanager_conn['hostname'],
                    alertmanager_conn['port'])
                self._run_cmd([
                    'ceph', 'dashboard', 'set-alertmanager-api-host',
                    alertmanager_ep])
            prometheus_conn = self.prometheus.get_service_ep_data()
            if prometheus_conn:
                prometheus_ep = 'http://{}:{}'.format(
                    prometheus_conn['hostname'],
                    prometheus_conn['port'])
                self._run_cmd([
                    'ceph', 'dashboard', 'set-prometheus-api-host',
                    prometheus_ep])
        self._register_dashboards()
        self._manage_radosgw()
        self._manage_iscsigw()
        self._stored.is_started = True
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
        try:
            root_ca_chain = self.ca_client.root_ca_chain.public_bytes(
                encoding=serialization.Encoding.PEM
            )
        except ca_client.CAClientError:
            # A root ca chain is not always available. If configured to just
            # use vault with self-signed certificates, you will not get a ca
            # chain. Instead, you will get a CAClientError being raised. For
            # now, use a bytes() object for the root_ca_chain as it shouldn't
            # cause problems and if a ca_cert_chain comes later, then it will
            # get updated.
            root_ca_chain = bytes()
        ca_cert = (
            self.ca_client.ca_certificate.public_bytes(
                encoding=serialization.Encoding.PEM) +
            root_ca_chain)
        return key, cert, ca_cert

    def _update_iscsigw_creds(self, creds):
        self._apply_file_setting(
            'iscsi-gateway-add',
            '{}://{}:{}@{}:{}'.format(
                creds['scheme'],
                creds['username'],
                creds['password'],
                creds['host'],
                creds['port']),
            [creds['name']])

    def _manage_iscsigw(self) -> None:
        """Register rados gateways in dashboard db"""
        if self.unit.is_leader():
            creds = self.iscsi_user.get_user_creds()
            if len(creds) < 1:
                logging.info("No iscsi gateway creds found")
                return
            else:
                for c in creds:
                    self._update_iscsigw_creds(c)

    def _configure_tls(self) -> None:
        """Configure TLS."""
        logging.debug("Attempting to collect TLS config from relation")
        key, cert, ca_cert = self._get_tls_from_relation()
        ca_cert_path = self.TLS_VAULT_CA_CERT_PATH
        if not (key and cert):
            logging.debug("Attempting to collect TLS config from charm "
                          "config")
            key, cert, ca_cert = self._get_tls_from_config()
            ca_cert_path = self.TLS_CHARM_CA_CERT_PATH
        if not (key and cert):
            logging.warn(
                "Not configuring TLS, not all data present")
            return
        self.TLS_KEY_PATH.write_bytes(key)
        self.TLS_CERT_PATH.write_bytes(cert)
        if ca_cert:
            ca_cert_path.write_bytes(ca_cert)
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

    def _gen_user_password(self, length: int = 12) -> str:
        """Generate a password"""
        alphabet = (
            string.ascii_lowercase + string.ascii_uppercase + string.digits)

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

    def _delete_user_action(self, event: ActionEvent) -> None:
        """Delete a user"""
        username = event.params["username"]
        try:
            self._run_cmd(['ceph', 'dashboard', 'ac-user-delete', username])
            event.set_results({"message": "User {} deleted".format(username)})
        except subprocess.CalledProcessError as exc:
            event.fail(exc.output)


if __name__ == "__main__":
    main(CephDashboardCharm)
