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

import os
import json
import unittest
import sys
from pathlib import Path

sys.path.append('lib')  # noqa
sys.path.append('src')  # noqa

from unittest.mock import call, patch, MagicMock, ANY, Mock

from ops.testing import Harness, _TestingModelBackend
from ops.model import (
    BlockedStatus,
)
from ops import framework, model

with patch('charmhelpers.core.host_factory.ubuntu.cmp_pkgrevno',
           Mock(return_value=1)):
    import charm

TEST_CA = '''-----BEGIN CERTIFICATE-----
MIIC8TCCAdmgAwIBAgIUIchLT42Gy3QexrQbppgWb+xF2SgwDQYJKoZIhvcNAQEL
BQAwGjEYMBYGA1UEAwwPRGl2aW5lQXV0aG9yaXR5MB4XDTIwMDUwNTA5NDIzMVoX
DTIwMDYwNDA5NDIzMlowGjEYMBYGA1UEAwwPRGl2aW5lQXV0aG9yaXR5MIIBIjAN
BgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA54oZkgz+xpaM8AKfHTT19lwqvVSr
W3uZiyyiNAWBX+Ru5/5RqQONKmjPqU3Bh966IBxo8hGYsk7MJ3LobvuG6j497SUc
nn4JECm/mOKGeQvSSGnor93ropyWAQDQ3U1JVxV/K4sw2EpwwxfaJAM4L5rVi9EK
TsN23cPI81DKLuDxeXGGDPXMgQuTqfGD74jk6oTpfEHNmQB1Lcj+t+HxQqyoHyo5
RPNRpntgPAvrF8i1ktJ/EH4GJxSBwm7098JcMgQSif9PHzL0UKehC2mlNX7ljGQ+
eOLo6XNHYnq6DfxO6c3TbOIYt7VSc8K3IG500/4IzIT3+mtZ3rrM3mQWDwIDAQAB
oy8wLTAaBgNVHREEEzARgg9EaXZpbmVBdXRob3JpdHkwDwYDVR0TAQH/BAUwAwEB
/zANBgkqhkiG9w0BAQsFAAOCAQEAfzQSUzfaUv5Q4Eqz2YiWFx2zRYi0mUjYrGf9
1qcprgpAq7F72+ed3uLGEmMr53+wgL4XdzLnSZwpYRFNBI7/t6hU3kxw9fJC5wMg
LHLdNlNqXAfoGVVTjcWPiQDF6tguccqyE3UWksl+2fncgkkcUpH4IP0AZVYlCsrz
mzs5P3ATpdTE1BZiw4WEiE4+N8ZC7Rcz0icfCEbKJduMkkxpJlvp5LwSsmtrpS3v
IZvomDHx8ypr+byzUTsfbAExdXVpctkG/zLMAi6/ZApO8GlD8ga8BUn2NGfBO5Q8
28kEjS5DV835Re4hHE6pTC4HEjq0D2r1/4OG7ijt8emO5XPoMg==
-----END CERTIFICATE-----'''

TEST_APP_CERT = '''-----BEGIN CERTIFICATE-----
MIID9jCCAt6gAwIBAgIUX5lsqmlS3aFLw7+IqSqadI7W1yswDQYJKoZIhvcNAQEL
BQAwRTFDMEEGA1UEAxM6VmF1bHQgSW50ZXJtZWRpYXRlIENlcnRpZmljYXRlIEF1
dGhvcml0eSAoY2hhcm0tcGtpLWxvY2FsKTAeFw0yMDA1MDUwOTQyMTdaFw0yMTA1
MDUwODQyNDdaMA4xDDAKBgNVBAMTA2FwcDCCASIwDQYJKoZIhvcNAQEBBQADggEP
ADCCAQoCggEBALfmMzGbbShmQGduZImaGsJWd6vGriVwgYlIV60Kb1MLxuLvMyzV
tBseRH1izKgPDEmMRafU9N4DC0jRb+04APBM8QBWEDrrYgRQQSNxlCDVMn4Q4iHO
72FwCqI1HuW0R5J3yik4FkW3Kb8Uq5KDsKWqTLtaBW5X40toi1bkyFTnRZ6/3vmt
9arAfqmZyXlZK3rN+uiznLx8/rYU5umkicNGfDcWI37wjdYvK/tIE79vPom5VhGb
R+rz+hri7JmiaYkzrTWWibyjPNK0aGHa5OUIiFJfAtfyjoT1d/pxwS301BWLicw1
vSzCJcTwpkzh2EWvuquK2sUjgHNR1qAkGIECAwEAAaOCARMwggEPMA4GA1UdDwEB
/wQEAwIDqDAdBgNVHSUEFjAUBggrBgEFBQcDAQYIKwYBBQUHAwIwHQYDVR0OBBYE
FL0B0hMaFwG0I0WR4CiOZnrqRHoLMEkGCCsGAQUFBwEBBD0wOzA5BggrBgEFBQcw
AoYtaHR0cDovLzE3Mi4yMC4wLjE5OjgyMDAvdjEvY2hhcm0tcGtpLWxvY2FsL2Nh
MDMGA1UdEQQsMCqCA2FwcIIDYXBwgghhcHB1bml0MYIIYXBwdW5pdDKHBKwAAAGH
BKwAAAIwPwYDVR0fBDgwNjA0oDKgMIYuaHR0cDovLzE3Mi4yMC4wLjE5OjgyMDAv
djEvY2hhcm0tcGtpLWxvY2FsL2NybDANBgkqhkiG9w0BAQsFAAOCAQEAbf6kIurd
pBs/84YD59bgeytlo8RatUzquwCRgRSv6N81+dYFBHtEVOoLwy/4wJAH2uMSKK+/
C13vTBj/cx+SxWSIccPS0rglwEKhRF/u3n9hrFAL3QMLQPEXAJ5rJtapZ7a8uIWy
bChTMhoL4bApCXG+SH4mbhkD6SWQ1zPgfXD4ZiVtjEVIdyn63/fbNFUfhFKba8BE
wQUYw0yWq0/8ILq/WPyjKBvhSinIauy+ybdzaDMEg0Grq1n0K5l/WyK+t9tQd+UG
cLjamd6EKZ2OvOxZN6/cJlHDY2NKfjGF6KhQ5D2cseYK7dhOQ9AFjUCB/NgIAH9D
8vVp8VJOx6plOw==
-----END CERTIFICATE-----'''

TEST_APP_KEY = '''-----BEGIN RSA PRIVATE KEY-----
MIIEpAIBAAKCAQEAt+YzMZttKGZAZ25kiZoawlZ3q8auJXCBiUhXrQpvUwvG4u8z
LNW0Gx5EfWLMqA8MSYxFp9T03gMLSNFv7TgA8EzxAFYQOutiBFBBI3GUINUyfhDi
Ic7vYXAKojUe5bRHknfKKTgWRbcpvxSrkoOwpapMu1oFblfjS2iLVuTIVOdFnr/e
+a31qsB+qZnJeVkres366LOcvHz+thTm6aSJw0Z8NxYjfvCN1i8r+0gTv28+iblW
EZtH6vP6GuLsmaJpiTOtNZaJvKM80rRoYdrk5QiIUl8C1/KOhPV3+nHBLfTUFYuJ
zDW9LMIlxPCmTOHYRa+6q4raxSOAc1HWoCQYgQIDAQABAoIBAD92GUSNNmYyoxcO
aXNy0rktza5hqccRxCHz7Q2yBCjMb53wneBi/vw8vbXnWmjEiKD43zDDtJzIwCQo
4k8ifHBwnNpY2ND8WZ7TcycgEtYhvIL0oJS6LLGbUJAZdMggJnLNE96VlFoKk0V1
hJ/TAiqpUkF1F1q0yaNEOJGL8fYaI5Mz1pU+rspxS2uURFYGcD78Ouda5Pruwcp3
A0Sbo+5P0FZRy79zpZbIzlvcS9R7wKuDJExCXXCsoZ+G0BWwTJPsDhkmcuXdS7f3
3k3VO4Y8rcsOIHtI0Gj38yhO6giDjPeZWmXF6h7+zSWPaZydswTqtyS2BbvUmE3N
t/HYCOECgYEA2AYQZqAeFk5i7Qnb80pG9q1THZOM4V/FQsyfb9Bzw+nANP6LMd3D
tnY7BUNj0vTJVy/wnwFSmryQn3OqsxHYbOaor9xjuCauAGzp/4cj0anTySz0pZiQ
TzVepB35bj8ghRsQ1TO+7FQtMMZQGrNf1i6e3p9+hpKUA6ZwP0OEbpMCgYEA2e5E
Uqqj1u0pnUAeXp/2VbQS4rmxUrRsbdbiyoypNJOp+Olfi2DjQNgji0XDBdTLhDNv
nFtHY7TW4HJrwVAAqBlYKkunf6zGlP3iEGhk7RF1LSyGZXjfLACe7kzqlAx34Ue9
9ynkesNKeT8kOOCC08llHuInMjfgfN0c7jWYNRsCgYEAgzBrlWd33iQMf9eU89MP
9Y6dA0EwNU5sBX0u9kCpjTjPuV88OTRsPsreXPvoC50NCR3cCzRKbh5F1g/wgn87
6CbMGsDE7njPAwMhuEThw9pW+72JdWeJfBD1QMXTTNiZbzxYpKGgOPWF3DETRKPa
d8AoSxqhRCiQKwdQ85qVOnECgYAu6dfTY+B5N/ypWVAwVocU0/rsy8ScZTKiQov3
xmf2ZYNFjhd/TZAeOWkNZishajmVb+0q34tyr09Cad9AchRyG2KbWEXqeisVj8HG
fnKbhhKPcvJLjcWdF1UfP3eP/08fM+508pO4yamSiEEn7Uy8grI9/7koWlb9Cixc
KzVk2QKBgQCdA3eoJHu4nTHRNgcvU3pxbRU4HQV8e+Hiw1tcxjprkACrNVvd7wZS
wULKjMb8z0RZyTBXLdNw3YKYOk/B7e/e9D+Zve4PTEL23Fcdt532x/7hBQ+7o6/4
7RxsGx5/PXZI0/YKMKk9hsrdMl4/UAd0izvwPCQbB3eisuZYU/i8Jw==
-----END RSA PRIVATE KEY-----'''


class CharmTestCase(unittest.TestCase):

    def setUp(self, obj, patches):
        super().setUp()
        self.patches = patches
        self.obj = obj
        self.patch_all()

    def patch(self, method):
        _m = patch.object(self.obj, method)
        mock = _m.start()
        self.addCleanup(_m.stop)
        return mock

    def patch_all(self):
        for method in self.patches:
            setattr(self, method, self.patch(method))


class _CephISCSIGatewayCharmBase(charm.CephISCSIGatewayCharmBase):

    @staticmethod
    def get_bluestore_compression():
        return {}


class TestCephISCSIGatewayCharmBase(CharmTestCase):

    PATCHES = [
        'ch_templating',
        'gwcli_client',
        'os',
        'secrets',
        'socket',
        'subprocess',
    ]

    def setUp(self):
        super().setUp(charm, self.PATCHES)
        self.harness = Harness(
            _CephISCSIGatewayCharmBase,
        )
        self.test_hostname = 'server1'
        self.socket.gethostname.return_value = self.test_hostname
        self.test_fqdn = self.test_hostname + '.foo'
        self.socket.getfqdn.return_value = self.test_fqdn
        self.secrets.choice.return_value = 'r'
        self.test_admin_password = 'rrrrrrrr'
        self.gwc = MagicMock()
        self.gwcli_client.GatewayClient.return_value = self.gwc

        # BEGIN: Workaround until network_get is implemented
        class _TestingOPSModelBackend(_TestingModelBackend):

            def network_get(self, endpoint_name, relation_id=None):
                network_data = {
                    'bind-addresses': [{
                        'interface-name': 'eth0',
                        'addresses': [{
                            'cidr': '10.0.0.0/24',
                            'value': '10.0.0.10'}]}],
                    'ingress-addresses': ['10.0.0.10'],
                    'egress-subnets': ['10.0.0.0/24']}
                return network_data

        self.harness._backend = _TestingOPSModelBackend(
            self.harness._unit_name, self.harness._meta,
            {'options': {'gateway-metadata-pool': {'type': 'string'}}})
        self.harness._model = model.Model(
            self.harness._meta,
            self.harness._backend)
        self.harness._framework = framework.Framework(
            ":memory:",
            self.harness._charm_dir,
            self.harness._meta,
            self.harness._model)
        # END Workaround

    def test_init(self):
        self.harness.begin()
        self.assertFalse(self.harness.charm._stored.target_created)
        self.assertFalse(self.harness.charm._stored.enable_tls)

    def add_base_cluster_relation(self):
        rel_id = self.harness.add_relation('cluster', 'ceph-iscsi')
        self.harness.add_relation_unit(
            rel_id,
            'ceph-iscsi/1')
        return rel_id

    def complete_cluster_relation(self, rel_id):
        self.harness.update_relation_data(
            rel_id,
            'ceph-iscsi/1',
            {
                'ingress-address': '10.0.0.2',
                'gateway_ready': 'True',
                'gateway_fqdn': 'ceph-iscsi-1.example'
            })

    def add_admin_access_relation(self):
        rel_id = self.harness.add_relation('admin-access', 'ceph-dashboard')
        self.harness.add_relation_unit(
            rel_id,
            'ceph-dashboard/0')
        self.harness.update_relation_data(
            rel_id,
            'ceph-dashboard/0',
            {
                'ingress-address': '10.0.0.2',
            })
        return rel_id

    @patch('socket.getfqdn')
    def test_on_create_target_action(self, _getfqdn):
        _getfqdn.return_value = 'ceph-iscsi-0.example'
        cluster_rel_id = self.add_base_cluster_relation()
        self.complete_cluster_relation(cluster_rel_id)
        self.harness.begin()
        action_event = MagicMock()
        action_event.params = {
            'iqn': 'iqn.mock.iscsi-gw:iscsi-igw',
            'gateway-units': 'ceph-iscsi/0 ceph-iscsi/1',
            'rbd-pool-name': 'iscsi-pool',
            'ec-rbd-metadata-pool': '',
            'image-name': 'disk1',
            'image-size': '5G',
            'client-initiatorname': 'client-initiator',
            'client-username': 'myusername',
            'client-password': 'mypassword'}
        self.harness.charm.on_create_target_action(action_event)
        self.gwc.add_gateway_to_target.assert_has_calls([
            call(
                'iqn.mock.iscsi-gw:iscsi-igw',
                '10.0.0.10',
                'ceph-iscsi-0.example'),
            call(
                'iqn.mock.iscsi-gw:iscsi-igw',
                '10.0.0.2',
                'ceph-iscsi-1.example')])

        self.gwc.create_pool.assert_called_once_with(
            'iscsi-pool',
            'disk1',
            '5G')
        self.gwc.add_client_to_target.assert_called_once_with(
            'iqn.mock.iscsi-gw:iscsi-igw',
            'client-initiator')
        self.gwc.add_client_auth.assert_called_once_with(
            'iqn.mock.iscsi-gw:iscsi-igw',
            'client-initiator',
            'myusername',
            'mypassword')
        self.gwc.add_disk_to_client.assert_called_once_with(
            'iqn.mock.iscsi-gw:iscsi-igw',
            'client-initiator',
            'iscsi-pool',
            'disk1')

    @patch('socket.getfqdn')
    def test_on_create_target_action_ec(self, _getfqdn):
        _getfqdn.return_value = 'ceph-iscsi-0.example'
        cluster_rel_id = self.add_base_cluster_relation()
        self.complete_cluster_relation(cluster_rel_id)
        self.harness.begin()
        action_event = MagicMock()
        action_event.params = {
            'iqn': 'iqn.mock.iscsi-gw:iscsi-igw',
            'gateway-units': 'ceph-iscsi/0 ceph-iscsi/1',
            'rbd-pool-name': 'iscsi-pool',
            'ec-rbd-metadata-pool': 'iscsi-metapool',
            'image-name': 'disk1',
            'image-size': '5G',
            'client-initiatorname': 'client-initiator',
            'client-username': 'myusername',
            'client-password': 'mypassword'}
        self.harness.charm.on_create_target_action(action_event)
        self.subprocess.check_call.assert_called_once_with(
            [
                'rbd',
                '--user', 'ceph-iscsi',
                '--conf', '/etc/ceph/iscsi/ceph.conf',
                'create',
                '--size', '5G',
                'iscsi-metapool/disk1',
                '--data-pool', 'iscsi-pool'])
        self.gwc.add_gateway_to_target.assert_has_calls([
            call(
                'iqn.mock.iscsi-gw:iscsi-igw',
                '10.0.0.10',
                'ceph-iscsi-0.example'),
            call(
                'iqn.mock.iscsi-gw:iscsi-igw',
                '10.0.0.2',
                'ceph-iscsi-1.example')])

        self.gwc.create_pool.assert_called_once_with(
            'iscsi-metapool',
            'disk1',
            '5G')
        self.gwc.add_client_to_target.assert_called_once_with(
            'iqn.mock.iscsi-gw:iscsi-igw',
            'client-initiator')
        self.gwc.add_client_auth.assert_called_once_with(
            'iqn.mock.iscsi-gw:iscsi-igw',
            'client-initiator',
            'myusername',
            'mypassword')
        self.gwc.add_disk_to_client.assert_called_once_with(
            'iqn.mock.iscsi-gw:iscsi-igw',
            'client-initiator',
            'iscsi-metapool',
            'disk1')

    def test_on_has_peers(self):
        rel_id = self.harness.add_relation('cluster', 'ceph-iscsi')
        self.harness.begin()
        self.harness.add_relation_unit(
            rel_id,
            'ceph-iscsi/1')
        self.assertIsNone(
            self.harness.charm.peers.admin_password)
        self.harness.set_leader()
        self.harness.update_relation_data(
            rel_id,
            'ceph-iscsi/1',
            {
                'ingress-address': '10.0.0.2',
                'gateway_ready': 'True',
                'gateway_fqdn': 'ceph-iscsi-1.example'
            })
        self.assertEqual(
            self.harness.charm.peers.admin_password, self.test_admin_password)

    def test_on_has_peers_not_leader(self):
        self.add_base_cluster_relation()
        self.harness.begin()
        self.assertIsNone(
            self.harness.charm.peers.admin_password)
        self.harness.set_leader(False)
        self.harness.charm.peers.on.has_peers.emit()
        self.assertIsNone(
            self.harness.charm.peers.admin_password)

    def test_on_has_peers_existing_password(self):
        rel_id = self.add_base_cluster_relation()
        self.harness.update_relation_data(
            rel_id,
            'ceph-iscsi',
            {'admin_password': 'existing password'})
        self.harness.begin()
        self.harness.set_leader()
        self.harness.charm.peers.on.has_peers.emit()
        self.assertEqual(
            self.harness.charm.peers.admin_password,
            'existing password')

    def test_on_ceph_client_relation_joined(self):
        self.maxDiff = None
        rel_id = self.harness.add_relation('ceph-client', 'ceph-mon')
        self.harness.update_config(
            key_values={'gateway-metadata-pool': 'iscsi-pool'})
        self.harness.begin()
        self.harness.add_relation_unit(
            rel_id,
            'ceph-mon/0')
        self.harness.update_relation_data(
            rel_id,
            'ceph-mon/0',
            {'ingress-address': '10.0.0.3'})
        rel_data = self.harness.get_relation_data(rel_id, 'ceph-iscsi/0')
        actual_req_osd_settings = json.loads(rel_data['osd-settings'])
        self.assertEqual(
            actual_req_osd_settings,
            {'osd heartbeat grace': 20, 'osd heartbeat interval': 5})
        actual_req_pool_ops = json.loads(rel_data['broker_req'])['ops']
        self.assertEqual(actual_req_pool_ops[0]['op'], 'create-pool')
        self.assertEqual(actual_req_pool_ops[0]['name'], 'iscsi-pool')
        self.assertEqual(actual_req_pool_ops[1]['op'], 'create-pool')
        self.assertEqual(actual_req_pool_ops[1]['name'], 'ceph-iscsi')
        self.assertEqual(actual_req_pool_ops[2]['op'], 'set-key-permissions')
        self.assertEqual(actual_req_pool_ops[2]['client'], 'ceph-iscsi')

    def test_on_pools_available(self):
        self.os.path.exists.return_value = False
        self.os.path.basename = os.path.basename
        rel_id = self.add_base_cluster_relation()
        self.harness.update_relation_data(
            rel_id,
            'ceph-iscsi',
            {'admin_password': 'existing password',
             'gateway_ready': 'False'})
        self.harness.begin()
        self.harness.charm.ceph_client._stored.pools_available = True
        with patch.object(Path, 'mkdir') as mock_mkdir:
            self.harness.charm.ceph_client.on.pools_available.emit()
            mock_mkdir.assert_called_once_with(exist_ok=True, mode=488)
        self.ch_templating.render.assert_has_calls([
            call('ceph.conf', '/etc/ceph/iscsi/ceph.conf', ANY),
            call('iscsi-gateway.cfg', '/etc/ceph/iscsi-gateway.cfg', ANY),
            call(
                'ceph.client.ceph-iscsi.keyring',
                '/etc/ceph/iscsi/ceph.client.ceph-iscsi.keyring', ANY)],
            any_order=True)
        self.assertTrue(self.harness.charm._stored.is_started)
        rel_data = self.harness.get_relation_data(rel_id, 'ceph-iscsi/0')
        self.assertEqual(rel_data['gateway_ready'], 'True')

    def test_on_certificates_relation_joined(self):
        rel_id = self.harness.add_relation('certificates', 'vault')
        self.harness.begin()
        self.harness.add_relation_unit(
            rel_id,
            'vault/0')
        self.harness.update_relation_data(
            rel_id,
            'vault/0',
            {'ingress-address': '10.0.0.3'})
        rel_data = self.harness.get_relation_data(rel_id, 'ceph-iscsi/0')
        self.assertEqual(
            rel_data['application_cert_requests'],
            '{"server1.foo": {"sans": ["10.0.0.10", "server1"]}}')

    def test_on_certificates_relation_changed(self):
        mock_TLS_CERT_PATH = MagicMock()
        mock_TLS_CA_CERT_PATH = MagicMock()
        mock_TLS_KEY_PATH = MagicMock()
        mock_KEY_AND_CERT_PATH = MagicMock()
        mock_TLS_PUB_KEY_PATH = MagicMock()
        self.subprocess.check_output.return_value = b'pubkey'
        rel_id = self.harness.add_relation('certificates', 'vault')
        self.add_base_cluster_relation()
        self.harness.begin()
        self.harness.charm.TLS_CERT_PATH = mock_TLS_CERT_PATH
        self.harness.charm.TLS_CA_CERT_PATH = mock_TLS_CA_CERT_PATH
        self.harness.charm.TLS_KEY_PATH = mock_TLS_KEY_PATH
        self.harness.charm.TLS_KEY_AND_CERT_PATH = mock_KEY_AND_CERT_PATH
        self.harness.charm.TLS_PUB_KEY_PATH = mock_TLS_PUB_KEY_PATH
        self.harness.add_relation_unit(
            rel_id,
            'vault/0')
        rel_data = {
            'app_data': {
                'cert': TEST_APP_CERT,
                'key': TEST_APP_KEY}}
        self.harness.update_relation_data(
            rel_id,
            'vault/0',
            {
                'ceph-iscsi_0.processed_application_requests': json.dumps(
                    rel_data),
                'ca': TEST_CA})
        mock_TLS_CERT_PATH.write_bytes.assert_called_once()
        mock_TLS_CA_CERT_PATH.write_bytes.assert_called_once()
        mock_TLS_KEY_PATH.write_bytes.assert_called_once()
        mock_KEY_AND_CERT_PATH.write_bytes.assert_called_once()
        mock_TLS_PUB_KEY_PATH.write_bytes.assert_called_once()
        self.subprocess.check_call.assert_called_once_with(
            ['update-ca-certificates'])
        self.assertTrue(self.harness.charm._stored.enable_tls)

    def test_custom_status_check(self):
        self.harness.add_relation('ceph-client', 'ceph-mon')
        self.harness.add_relation('cluster', 'ceph-iscsi')
        self.harness.begin()
        self.harness.charm.on.update_status.emit()
        self.assertEqual(
            self.harness.charm.unit.status.message,
            '1 is an invalid unit count')
        self.assertIsInstance(
            self.harness.charm.unit.status,
            BlockedStatus)

    def test_publish_admin_access_info(self):
        cluster_rel_id = self.add_base_cluster_relation()
        admin_access_rel_id = self.add_admin_access_relation()
        self.harness.begin()
        self.harness.set_leader()
        self.complete_cluster_relation(cluster_rel_id)
        self.assertEqual(
            self.harness.get_relation_data(
                admin_access_rel_id,
                'ceph-iscsi/0'),
            {
                'host': '10.0.0.10',
                'name': self.test_fqdn,
                'port': '5000',
                'scheme': 'http'})
        self.assertEqual(
            self.harness.get_relation_data(
                admin_access_rel_id,
                'ceph-iscsi'),
            {
                'password': self.test_admin_password,
                'username': 'admin'})
