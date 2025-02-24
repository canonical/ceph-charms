#!/usr/bin/env python3
# Copyright 2021 Canonical
# See LICENSE file for licensing details.
#
# Learn more at: https://juju.is/docs/sdk

"""Charm for the Ceph Dashboard."""

import json
import base64
import logging
import re
import secrets
import socket
import string
import subprocess
import tempfile
from pathlib import Path
from typing import List, Tuple, Union

import charms_ceph.utils as ceph_utils
import cryptography.hazmat.primitives.serialization as serialization
import interface_ceph_iscsi_admin_access.admin_access as admin_access
import interface_dashboard
import interface_grafana_dashboard as grafana_interface
import interface_http
import interface_openstack_loadbalancer.loadbalancer as ops_lb_interface
import interface_radosgw_user
import interface_tls_certificates.ca_client as ca_client
import ops_openstack.plugins.classes
import tenacity

from ops.charm import ActionEvent, CharmEvents
from ops.framework import EventBase, EventSource, StoredState
from ops.main import main
from ops.model import ActiveStatus, BlockedStatus, StatusBase

# Charm Src
import ceph_dashboard_commands as cmds
from charm_option import CharmCephOptionList

logger = logging.getLogger(__name__)

TLS_Config = Tuple[Union[bytes, None], Union[bytes, None], Union[bytes, None]]


# Maintenance Events
class DisableSSL(EventBase):
    """Charm Event to disable SSL and clean certificates."""


class EnableSSLFromConfig(EventBase):
    """Charm Event to configure SSL using Charm config values."""


class CephCharmEvents(CharmEvents):
    """Custom charm events."""

    disable_ssl = EventSource(DisableSSL)
    enable_ssl_from_config = EventSource(EnableSSLFromConfig)


class CephDashboardCharm(ops_openstack.core.OSBaseCharm):
    """Ceph Dashboard charm."""

    _stored = StoredState()
    PACKAGES = ['ceph-mgr-dashboard', 'python3-onelogin-saml2']
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

    # Charm Events
    on = CephCharmEvents()

    CHARM_TO_CEPH_OPTIONS = CharmCephOptionList().get()

    def __init__(self, *args) -> None:
        """Setup adapters and observers."""
        super().__init__(*args)
        super().register_status_check(self.check_dashboard)
        self.framework.observe(
            self.on.config_changed, self._configure_dashboard
        )
        self.mon = interface_dashboard.CephDashboardRequires(self, "dashboard")
        self.radosgw_user = interface_radosgw_user.RadosGWUserRequires(
            self, "radosgw-dashboard", request_system_role=True
        )
        self.iscsi_user = admin_access.CephISCSIAdminAccessRequires(
            self, "iscsi-dashboard"
        )
        self.framework.observe(
            self.mon.on.mon_ready, self._configure_dashboard
        )
        self.framework.observe(
            self.radosgw_user.on.gw_user_ready, self._configure_dashboard
        )
        self.framework.observe(
            self.iscsi_user.on.admin_access_ready, self._configure_dashboard
        )
        self.framework.observe(self.on.add_user_action, self._add_user_action)
        self.framework.observe(
            self.on.delete_user_action, self._delete_user_action
        )
        self.ingress = ops_lb_interface.OSLoadbalancerRequires(
            self, "loadbalancer"
        )
        self.grafana_dashboard = grafana_interface.GrafanaDashboardProvides(
            self, "grafana-dashboard"
        )
        self.alertmanager = interface_http.HTTPRequires(
            self, "alertmanager-service"
        )
        self.prometheus = interface_http.HTTPRequires(self, "prometheus")
        self.framework.observe(
            self.grafana_dashboard.on.dash_ready, self._configure_dashboard
        )
        self.framework.observe(
            self.alertmanager.on.http_ready, self._configure_dashboard
        )
        self.framework.observe(
            self.prometheus.on.http_ready, self._configure_dashboard
        )
        self.framework.observe(
            self.ingress.on.lb_relation_ready, self._request_loadbalancer
        )
        self.framework.observe(
            self.ingress.on.lb_configured, self._configure_dashboard
        )

        # Certificates Relation
        self.ca_client = ca_client.CAClient(self, "certificates")
        self.framework.observe(
            self.ca_client.on.ca_available, self._request_certificates
        )
        self.framework.observe(
            self.ca_client.on.tls_server_config_ready,
            self._enable_ssl_from_relation
        )
        self.framework.observe(
            self.on["certificates"].relation_departed,
            self._certificates_relation_departed,
        )

        # Charm Custom Events
        self.framework.observe(self.on.disable_ssl, self._clean_ssl_conf)
        self.framework.observe(
            self.on.enable_ssl_from_config, self._enable_ssl_from_config
        )

        self._stored.set_default(is_started=False)

    def _request_loadbalancer(self, _event) -> None:
        """Send request to create loadbalancer"""
        self.ingress.request_loadbalancer(
            self.LB_SERVICE_NAME,
            self.TLS_PORT,
            self.TLS_PORT,
            self._get_bind_ip(),
            'http',
        )

    def _register_dashboards(self) -> None:
        """Register all dashboards with grafana"""
        if not self.unit.is_leader():
            return  # Do nothing on non leader units.

        for dash_file in self.DASH_DIR.glob("*.json"):
            self.grafana_dashboard.register_dashboard(
                dash_file.stem,
                json.loads(dash_file.read_text()))
            logging.debug(
                "register_grafana_dashboard: {}".format(dash_file))

    def _update_radosgw_creds(
        self, access_key: str, secret_key: str
    ) -> None:
        """Update dashboard db with access & secret key for rados gateways.

        This method uses the legacy format which only supports one gateway.
        """
        self._apply_file_setting('set-rgw-api-access-key', access_key)
        self._apply_file_setting('set-rgw-api-secret-key', secret_key)

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
            # Update the provided creds for radosgw.
            # NOTE(utkarshbhatthere): Having multiple credentials is not
            # required even where there are multiple radosgw applications
            # in the juju model. Therefore, first available creds are
            # populated in dashboard.
            self._update_radosgw_creds(
                creds[0]['access_key'],
                creds[0]['secret_key'])

    def _request_certificates(self, event) -> None:
        """Request TLS certificates."""
        if not self.ca_client.is_joined:
            logging.debug("Cannot request certificates, relation not present.")
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
                event.defer()
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
            (self._check_dashboard_responding, 'Dashboard not responding')
        ]
        for check_f, msg in checks:
            if not check_f():
                return BlockedStatus(msg)

        # Check if both relation based and config based certs are supplied.
        return self._status_check_conflicting_ssl_sources()

    def kick_dashboard(self) -> None:
        """Disable and re-enable dashboard"""
        ceph_utils.mgr_disable_dashboard()
        ceph_utils.mgr_enable_dashboard()

    def _apply_file_setting(
        self, ceph_setting: str, file_contents: str,
        extra_args: List[str] = None
    ) -> None:
        """Apply a setting via a file"""
        with tempfile.NamedTemporaryFile(mode="w", delete=True) as _file:
            _file.write(file_contents)
            _file.flush()
            settings = ["-i", _file.name]
            if extra_args:
                settings.extend(extra_args)
            cmds.apply_setting(ceph_setting, settings)

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
                cmds.exec_option_ceph_cmd(option, value)
            else:
                logging.warning(
                    "Skipping charm option {}, not supported".format(
                        option.charm_option_name))

    def _configure_service_apis(self) -> None:
        """Configure related service APIs in ceph dashboard"""
        if self.unit.is_leader():
            grafana_ep = self.config.get("grafana-api-url")
            if grafana_ep:
                cmds.dashboard_set("set-grafana-api-url", grafana_ep)

            alertmanager_conn = self.alertmanager.get_service_ep_data()
            if alertmanager_conn:
                cmds.dashboard_set(
                    "set-alertmanager-api-host",
                    "http://{}:{}".format(
                        alertmanager_conn["hostname"],
                        alertmanager_conn["port"]
                    ),
                )

            prometheus_conn = self.prometheus.get_service_ep_data()
            if prometheus_conn:
                cmds.dashboard_set(
                    "set-prometheus-api-host",
                    "http://{}:{}".format(
                        prometheus_conn["hostname"], prometheus_conn["port"]
                    ),
                )

    def _configure_dashboard(self, event) -> None:
        """Configure dashboard"""
        if not self.mon.mons_ready:
            logging.info("Not configuring dashboard, mons not ready")
            return

        if ceph_utils.is_dashboard_enabled():
            if not self.unit.is_leader():
                # leader already enabled the dashboard and also handles config,
                # we don't need to do anything except set ourselves as ready
                logging.debug("Dashboard already enabled, setting ready.")
                self._stored.is_started = True
                self.update_status()
                return
        else:
            if self.unit.is_leader():
                # we're the leader, enable dashboard and continue
                # configuration below
                logging.debug("Enabling dashboard as leader.")
                ceph_utils.mgr_enable_dashboard()
            else:
                # non-leader, defer event until leader has enabled and
                # configured the dashboard
                logging.info("Dashboard not enabled, deferring event on "
                             "non-leader")
                event.defer()
                return

        if self.unit.is_leader():
            # If charm config ssl is present.
            if self._is_charm_ssl_from_config():
                if not cmds.check_ceph_dashboard_ssl_configured():
                    # Configure SSL using charm config.
                    self.on.enable_ssl_from_config.emit()
            else:  # charm config is not present.
                # Since certificates relation can provide unique certs to each
                # unit, the below check should only be performed on leader as
                # the central key/cert pair matches leader unit.
                key, cert, _ = self._get_tls_from_relation()
                if not self.is_ceph_dashboard_ssl_key_cert_same(key, cert):
                    # clean SSL if not configured using relation
                    self.on.disable_ssl.emit()
            # apply charm config
            self._apply_ceph_config_from_charm_config()

        self._configure_saml()

        ceph_utils.mgr_config_set(
            "mgr/dashboard/{hostname}/server_addr".format(
                hostname=socket.gethostname()
            ),
            str(self._get_bind_ip()),
        )

        # configure grafana, prometheus and alertmanager API endpoints
        self._configure_service_apis()

        self._register_dashboards()
        self._manage_radosgw()
        self._manage_iscsigw()
        self._stored.is_started = True
        self.update_status()

    def _get_bind_ip(self) -> str:
        """Return the IP to bind the dashboard to"""
        binding = self.model.get_binding('public')
        return str(binding.network.ingress_address)

    def _clean_ssl_conf(self, _event) -> None:
        """Clean ssl conf for ceph-dashboard."""

        # NOTE: Clearing up of SSL key/cert is done centrally so that it can
        # be performed with consistency for all units at once.
        if self.unit.is_leader():
            cmds.clean_ssl_conf()

    def is_ceph_dashboard_ssl_key_cert_same(
            self, key: str, cert: str, check_host: bool = False
    ) -> Union[bool, None]:
        """Checks if provided ssl key/cert match with configured key/cert.

        Since this method can result in falsy values even if the provided pair
        is empty (None). It is advised to use this method for falsy checks
        carefully.

        :returns: None if ssl is not configured or provided key/cert are empty.
        """
        if not cmds.check_ceph_dashboard_ssl_configured():
            # Ceph Dashboard SSL not configured.
            return None

        # Provided key/crt from param
        if key is None or cert is None:
            logger.debug("Empty key/cert pair : \n"
                         "Key %s, \nCerts: %s", (key is None), (cert is None))
            return None

        # Decode to ascii strings if bytes.
        if isinstance(key, bytes):
            key = key.decode()
        if isinstance(cert, bytes):
            cert = cert.decode()

        # Configured key/crt from ceph-dashboard
        if not check_host:
            ssl_key = cmds.get_ceph_dashboard_ssl_key()
            ssl_crt = cmds.get_ceph_dashboard_ssl_crt()
        else:
            ssl_key = cmds.get_ceph_dashboard_host_ssl_key()
            ssl_crt = cmds.get_ceph_dashboard_host_ssl_crt()

        if ssl_key == key and ssl_crt == cert:
            return True
        else:
            return False

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

    def _is_relation_active(self, relation_name: str) -> bool:
        """Check if any instance of the relation is present."""
        return any(
            relation.id for relation in self.model.relations[relation_name]
        )

    def _get_tls_from_relation(self) -> TLS_Config:
        """Extract TLS config from certificates relation."""
        # If 'certificates' relation is not present return None.
        if not self._is_relation_active('certificates'):
            return None, None, None

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
                encoding=serialization.Encoding.PEM
            ) + root_ca_chain)
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

    def _certificates_relation_departed(self, event) -> None:
        """Certificates relation departed handle"""
        if self.unit.is_leader():
            # Clear SSL if not configured using charm config.
            # NOTE: Since certificates relation has departed, check has to be
            # done using the charm config key/certs.
            key, cert, _ = self._get_tls_from_config()
            if not self.is_ceph_dashboard_ssl_key_cert_same(key, cert):
                self._clean_ssl_conf(event)

            # Possible handover to charm-config SSL.
            if self._is_charm_ssl_from_config():
                self.on.enable_ssl_from_config.emit()

    def _configure_tls(self, key, cert, ca_cert, ca_cert_path) -> None:
        """Configure TLS using provided credentials"""
        is_valid, msg = cmds.validate_ssl_keypair(cert, key)
        if not is_valid:
            logging.error("Invalid SSL key/cert: %s", msg)
            return
        self.TLS_KEY_PATH.write_bytes(key)
        self.TLS_CERT_PATH.write_bytes(cert)

        if ca_cert:
            ca_cert_path.write_bytes(ca_cert)
            subprocess.check_call(['update-ca-certificates'])

        if not self.unit.is_leader():
            logging.debug("Unit not leader, skipping SSL config")
            return
        cmds.set_ssl_material(
            self.TLS_KEY_PATH,
            self.TLS_CERT_PATH,
        )

        self.kick_dashboard()

    def _configure_saml(self) -> None:
        if not self.unit.is_leader():
            logger.debug("Unit not leader, skipping saml config")
            return

        base_url = self.config.get('saml-base-url')
        idp_metadata = self.config.get('saml-idp-metadata')
        username_attr = self.config.get('saml-username-attribute')
        idp_entity_id = self.config.get('saml-idp-entity-id')
        if not base_url or not idp_metadata:
            return

        cmds.ceph_dashboard_config_saml(
            base_url, idp_metadata, username_attr, idp_entity_id
        )

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
            with tempfile.NamedTemporaryFile(mode="w", delete=True) as fp:
                fp.write(password)
                fp.flush()
                cmd_out = cmds.ceph_dashboard_add_user(username, fp.name, role)
                if re.match('User.*already exists', cmd_out):
                    event.fail("User already exists")
                else:
                    event.set_results({"password": password})

    def _delete_user_action(self, event: ActionEvent) -> None:
        """Delete a user"""
        username = event.params["username"]
        try:
            cmds.ceph_dashboard_delete_user(username)
            event.set_results({"message": "User {} deleted".format(username)})
        except subprocess.CalledProcessError as exc:
            event.fail(exc.output)

    def _is_charm_ssl_from_relation(self) -> bool:
        """Check if ssl cert/key are provided by certificates relation."""
        key, cert, _ = self._get_tls_from_relation()
        # True if both key and cert are present false otherwise.
        return key and cert

    def _is_charm_ssl_from_config(self) -> bool:
        """Check if ssl cert/key are configured in charm config."""
        key, cert, _ = self._get_tls_from_config()
        # True if both key and cert are present false otherwise.
        return key and cert

    def _is_charm_ssl_multiple_sources(self) -> bool:
        """Check if SSL key/cert are available from multiple sources."""
        return self._is_charm_ssl_from_config() \
            and self._is_charm_ssl_from_relation()

    def _status_check_conflicting_ssl_sources(self):
        """Generate status check message for multiple ssl key/cert scenario."""
        # If conflicting SSL source is not present
        if not self._is_charm_ssl_multiple_sources():
            return ActiveStatus()

        # If both are waiting.
        if not cmds.check_ceph_dashboard_ssl_configured():
            return BlockedStatus(
                "Conflict: SSL configuration available from 'certificates' "
                "relation and Charm config, refusing to guess. "
                "Remove conflicting source to proceed."
            )

        key, cert, _ = self._get_tls_from_config()
        if self.is_ceph_dashboard_ssl_key_cert_same(key, cert):
            # SSL currently configured from charm config.
            return BlockedStatus(
                "Conflict: Active SSL from Charm config, 'certificates' "
                "relation is ignored. Remove conflicting source to proceed."
            )

        key, cert, _ = self._get_tls_from_relation()
        # 'Certificates' relation provides unique key/cert to each host.
        # Hence cert check is performed for host.
        if self.is_ceph_dashboard_ssl_key_cert_same(
            key, cert, check_host=True
        ):
            # SSL currently configured from relation.
            return BlockedStatus(
                "Conflict: Active SSL from 'certificates' relation, Charm "
                "config is ignored. Remove conflicting source to proceed."
            )

        # Check for ssl material validity.
        is_valid, msg = cmds.validate_ssl_keypair(cert, key)
        if not is_valid:
            return BlockedStatus(
                "Invalid SSL key/cert: {}".format(msg)
            )
        return BlockedStatus("Unknown SSL source.")

    def _configure_tls_from_charm_config(self) -> None:
        """Configure TLS using charm config values."""
        logging.debug("Attempting to collect TLS config from charm config")
        key, cert, ca_cert = self._get_tls_from_config()
        if not (key and cert):
            logging.error("Not configuring, not all config data present")
            return

        # Configure TLS
        self._configure_tls(key, cert, ca_cert, self.TLS_CHARM_CA_CERT_PATH)

    def _configure_tls_from_relation(self) -> None:
        """Configure TLS from certificates relation"""
        logging.debug("Attempting to collect TLS config from relation")
        key, cert, ca_cert = self._get_tls_from_relation()
        if not (key and cert):
            logging.error("Not configuring TLS, not all relation data present")
            return

        # Configure TLS
        self._configure_tls(key, cert, ca_cert, self.TLS_VAULT_CA_CERT_PATH)

    # Custom SSL Event Handles
    def _enable_ssl_from_config(self, event) -> None:
        """Configure Ceph Dashboard SSL with available key/cert from charm."""
        if not ceph_utils.is_dashboard_enabled():
            if self.unit.is_leader():
                ceph_utils.mgr_enable_dashboard()
            else:
                event.defer()
                return

        if all([
            cmds.check_ceph_dashboard_ssl_configured(),
            cmds.check_ceph_dashboard_ssl_configured(is_check_host_key=True)
        ]):
            # SSL is already configured for both central and host key/cert.
            return

        self._configure_tls_from_charm_config()
        self.update_status()

    # Certificates relation handle.
    def _enable_ssl_from_relation(self, event) -> None:
        """Configure Ceph Dashboard SSL using key/cert from relation."""
        if not ceph_utils.is_dashboard_enabled():
            if self.unit.is_leader():
                ceph_utils.mgr_enable_dashboard()
            else:
                event.defer()
                return

        if cmds.check_ceph_dashboard_ssl_configured():
            key, cert, _ = self._get_tls_from_config()
            if self.is_ceph_dashboard_ssl_key_cert_same(key, cert):
                # Charm relation event deferred until conflicting charm config
                # ssl is removed. Operator is informed through unit status.
                event.defer()
                return  # SSL is already configured.

        self._configure_tls_from_relation()
        self.update_status()


if __name__ == "__main__":
    main(CephDashboardCharm)
