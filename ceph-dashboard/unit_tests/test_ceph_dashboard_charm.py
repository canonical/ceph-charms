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

import base64
import json
import unittest
import sys

sys.path.append('lib')  # noqa
sys.path.append('src')  # noqa

from unittest.mock import ANY, call, patch, MagicMock

from ops.testing import Harness, _TestingModelBackend
from ops.model import (
    ActiveStatus,
    BlockedStatus,
)
from ops import framework, model
import charm

TEST_CA = '''-----BEGIN CERTIFICATE-----
MIIC8TCCAdmgAwIBAgIUAK1dgpjTc850TgQx6y3W1brByOwwDQYJKoZIhvcNAQEL
BQAwGjEYMBYGA1UEAwwPRGl2aW5lQXV0aG9yaXR5MB4XDTIxMDYyMTExNTg1OFoX
DTIxMDcyMTExNTg1OVowGjEYMBYGA1UEAwwPRGl2aW5lQXV0aG9yaXR5MIIBIjAN
BgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA08gO8TDPARVhfVLOkYYRvCU1Rviv
RYmy+ptA82XIHO1HvAuLQ8x/4bGxE+IMKSNIl+DIF9TMdmOCvKOBgRKsoOibZNfW
MJIeQwff/8LMFWReAjOxcf9Bu2EqOqkLmUV72FU+Weta8r2kuFhgryqvz1rZeZzQ
jP6OsscoY2FVt/TnvUL5cCOSTpKuQLSr8pDms3OuFIyhFkUinpGbgJQ83xQO1tRh
MGiA87lahsLECTKXsLPyFMMPZ/QQuoDmuUHNkR2deOLcYRSWIBy23PctuV893gbM
2sFTprWo1PKXSmFUd3lg6G5wSM2XRQAP81CTA3Hp8Fj5XCpOHa4HFQLxDwIDAQAB
oy8wLTAaBgNVHREEEzARgg9EaXZpbmVBdXRob3JpdHkwDwYDVR0TAQH/BAUwAwEB
/zANBgkqhkiG9w0BAQsFAAOCAQEAKsrUnYBJyyEIPXkWaemR5vmp0G+V6Xz3KvPB
hLYKRONMba8xFwrjRv7b0DNAws8TcXXOKtRtJWbnSIMGhfVESF6ohqEdn+J1crXs
2RpJgyF2u+l6gg9Sg2ngYMQYBkzjAHYTroO/itI4AWLPLHpgygzz8ho6ykWpDoxJ
QfrrtHCl90zweYDhl4g2joIOJSZdd36+Nx9f2guItRMN87EZy1mOrKs94HlW9jwj
mAfiGaYhgFn4JH2jVcZu4wVJErh4Z0A3UNNyOq4zlAq8pHa/54jerHTDB49UQbaI
vZ5PsZhTZLy3FImSbe25xMUZNTt/2MMjsQwSjwiQuxLSuicJAA==
-----END CERTIFICATE-----'''

TEST_CERT = '''-----BEGIN CERTIFICATE-----
MIIEdjCCA16gAwIBAgIUPmsr+BnLb6Yy22Zg6hkXn1B6KZcwDQYJKoZIhvcNAQEL
BQAwRTFDMEEGA1UEAxM6VmF1bHQgSW50ZXJtZWRpYXRlIENlcnRpZmljYXRlIEF1
dGhvcml0eSAoY2hhcm0tcGtpLWxvY2FsKTAeFw0yMTA2MjExMTU4MzNaFw0yMjA2
MjExMDU5MDJaMD4xPDA6BgNVBAMTM2p1anUtOGMzOTI5LXphemEtZWZjMDU2ZjE2
NmNkLTAucHJvamVjdC5zZXJ2ZXJzdGFjazCCASIwDQYJKoZIhvcNAQEBBQADggEP
ADCCAQoCggEBANW0NkSLH53M2Aok6lxN4qSSUDTnIWeuKsemLp7FwZn6zN7fRa4V
utuGWbeYahdSIY6AG3w5opCyijM/+L4+HWoY5BWGFPj/U5V4CDF9jOerNDcoxKDy
+h+CbJ324xJrCBOjMyW8wqK/lzCadQzy6DymOtK0RBJNHXsXiGWta7UMFo2AZcqM
8OkOd0HkBeDM90dzTRSuy3pvqNBKmpwG4Hmg/ESh7VuobuHTtkD2/sGEVMGoXm7Q
qk6Yf8POzNqdPoHzvY40uZWqL3OwedGWDrnNbH4sTYb1xB7fwBthvs+LNPUDzRXA
NOYlKsfRrsiH9ELyMWUfarKXxg+7JelBIdECAwEAAaOCAWMwggFfMA4GA1UdDwEB
/wQEAwIDqDAdBgNVHSUEFjAUBggrBgEFBQcDAQYIKwYBBQUHAwIwHQYDVR0OBBYE
FEpYZVtgGevbnUrzWsjAXZix5zgzMEoGCCsGAQUFBwEBBD4wPDA6BggrBgEFBQcw
AoYuaHR0cDovLzE3Mi4yMC4wLjExOTo4MjAwL3YxL2NoYXJtLXBraS1sb2NhbC9j
YTCBgAYDVR0RBHkwd4IZY2VwaC1kYXNoYm9hcmQuemF6YS5sb2NhbIIfanVqdS04
YzM5MjktemF6YS1lZmMwNTZmMTY2Y2QtMIIzanVqdS04YzM5MjktemF6YS1lZmMw
NTZmMTY2Y2QtMC5wcm9qZWN0LnNlcnZlcnN0YWNrhwSsFAD9MEAGA1UdHwQ5MDcw
NaAzoDGGL2h0dHA6Ly8xNzIuMjAuMC4xMTk6ODIwMC92MS9jaGFybS1wa2ktbG9j
YWwvY3JsMA0GCSqGSIb3DQEBCwUAA4IBAQBRUsmnc5fnNh1TSO1hVdpYBo6SRqdN
VPuG3EV6QYPGnqadzGTr3uREUyZdkOUu4nhqDONMTdlfCwg744AIlY+eo2tpiNEp
GOeFV0qZOiGRq7q2kllCTYCnh7hKCTCSN17o9QDTCL6w46cmH5OXo84BHkozdBiO
cHPQ+uJ/VZaRCuOIlVS4Y4vTDB0LpNX2nHC/tMYL0zA5+pu+N6e8OWcCgKwObdh5
38iuimYbbwv2QWBD+4eQUbxY0+TXlhdg42Um41N8BVdPapNAQRXIHrZJC5P6fXqX
uoZ6TvbI2U0GSfpjScPP5D2F6tWK7/3nbA8bPLUJ1MKDofBVtrlA4PIH
-----END CERTIFICATE-----'''

TEST_KEY = '''-----BEGIN RSA PRIVATE KEY-----
MIIEowIBAAKCAQEA1bQ2RIsfnczYCiTqXE3ipJJQNOchZ64qx6YunsXBmfrM3t9F
rhW624ZZt5hqF1IhjoAbfDmikLKKMz/4vj4dahjkFYYU+P9TlXgIMX2M56s0NyjE
oPL6H4JsnfbjEmsIE6MzJbzCor+XMJp1DPLoPKY60rREEk0dexeIZa1rtQwWjYBl
yozw6Q53QeQF4Mz3R3NNFK7Lem+o0EqanAbgeaD8RKHtW6hu4dO2QPb+wYRUwahe
btCqTph/w87M2p0+gfO9jjS5laovc7B50ZYOuc1sfixNhvXEHt/AG2G+z4s09QPN
FcA05iUqx9GuyIf0QvIxZR9qspfGD7sl6UEh0QIDAQABAoIBAAHqAk5s3JSiQBEf
MYYwIGaO9O70XwU5tyJgp6w+YzSI3Yrlfw9HHIxY0LbnQ5P/5VMMbLKZJY6cOsao
vQafMc5AeNKEh+2PA+Wj1Jb04+0zSF1yHQjABGOB3I0xp+kDUmgynwOohCnHA4io
6YF7L39TkdVPTgjH7gqrNEqM2hkeBWg1LY5QARDtz6Nj10LRtpQXjx/zwfGfzV2c
TGpO8ArfPLS+a7LAJ+E+iSgDUX272Fd7DYAv7xRcRe8991umpqFzbY8FDigLWEdd
3muWnRsJjricYM+2OO0QO8fyKhWCE31Dvc0xMLgrSTWoZAl8t7/WxyowevuVAm5o
oclYFU0CgYEA4M6seEB/neaqAWMIshwIcwZWaLy7oQAQagjXbKohSAXNlYqgTuv7
glk0P6uzeQOu0ejipwga6mQIc093WSzpG1sdT4bBysHS0b44Gx/6Cv0Jf6hmJGcU
wNo3XV8b0rHZ+KWDCfr1dUjxCA9rR2fOTJniCh9Ng28cyhrFyZ6HaUcCgYEA81sj
Z3ATs2uMxZePmGMWxOJqbQ+bHaoE+UG1dTQIVO//MmanJm3+o4ciH46D2QRWkYha
4Eqb5wnPKCQjun8JDpwgkLkd0EGGG4uJ6E6YqL3I0+cs5lwMWJ9M3oOaFGGoFAoP
V9lgz5f3yVdSChoubklS4KLeCiAojW/qX1rrKCcCgYEAuALz0YqZ6xm/1lrF52Ri
1iQ93oV934854FFUZDHuBBIb8WgDSBaJTGzQA737rfaBxngl7isIPQucjyZgvrGw
LSArocjgH6L/eYeGTU2jUhNFDyU8Vle5+RGld9w93fyOOqTf2e99s379LGfSnCQw
DSt4hmiQ/iCZJCU9+Ia2uEkCgYAGsPjWPUStaEWkoTg3jnHv0/HtMcKoHCaq292r
bVTVUQwJTL1H1zprMKoFiBuj+fSPZ9pn1GVZAvIJPoUk+Z08I5rZn91r/oE7fKi8
FH0qFp3RBcg8RUepoCey7pdr/AttEaG+XqHE037isF33HSUtryJyPsgwKxYyXWNq
X8ubfQKBgBwIpk7N754lN0i6V08Dadz0BlpfFYGO/ZfTmvVrPUxwehogtvpGnjhO
xPs1epK65/vHbBtaUDExayOEIvVhVWcnaXdx3z1aw/Hr29NlOi62x4g/RRSloLZH
08UCW9F5C8Ian6kglB5bPrZiJxcmssj7vSA+O6k9BjsO+ebaSRgk
-----END RSA PRIVATE KEY-----'''

TEST_CHAIN = '''-----BEGIN CERTIFICATE-----
MIIDADCCAeigAwIBAgIUN93XI0mOu3wkX5YureWnMImedUMwDQYJKoZIhvcNAQEL
BQAwGjEYMBYGA1UEAwwPRGl2aW5lQXV0aG9yaXR5MB4XDTIxMDYyMzEwMzcwMFoX
DTMyMDYwNjEwMzcwMFowRTFDMEEGA1UEAxM6VmF1bHQgSW50ZXJtZWRpYXRlIENl
cnRpZmljYXRlIEF1dGhvcml0eSAoY2hhcm0tcGtpLWxvY2FsKTCCASIwDQYJKoZI
hvcNAQEBBQADggEPADCCAQoCggEBAL1t5WYd7IVsfT5d4uztBhOPBA0EtrKw81Fe
Rp2TNdPUkkKSQxOYKV6F1ndyD88Nxx1mcxwi8U28b1azTNVaPRjSLxyDCOD0L5qk
LaFqppTWv8vLcjjlp6Ed3BLXoVMThWwMxJm/VSPuEXnWN5GrMR97Ae8vmnlrYDTF
re67j0zjDPhkyevVQ5+pLeZ/saQtNNeal1qzfWMPDQK0COfXolXmlmZGzhap742e
x4gE6alyYYrpTPA6CL9NbGhNovuz/LJvHN8fIdfw3jX+GW+yy312xDG+67PCW342
VDrPcG+Vq/BhEPwL3blYgbmtNPDQ1plWJqoPqoJzbCxLesXZHP8CAwEAAaMTMBEw
DwYDVR0TAQH/BAUwAwEB/zANBgkqhkiG9w0BAQsFAAOCAQEARv1bBEgwlDG3PuhF
Zt5kIeDLEnjFH2STz4LLERZXdKhTzuaV08QvYr+cL8XHi4Sop5BDkAuQq8mVC/xj
7DoW/Lb9SnxfsCIu6ugwKLfJ2El6r23kDzTauIaovDYNSEo21yBYALsFZjzMJotJ
XLpLklASTAdMmLP703hcgKgY8yxzS3WEXA9jekmn6z0y3+UZjIF5W9dW9gaQk0Eg
vsLN7xzG9TmQfk1OHUj7y+cEbYr0M3Jdif/gG8Kl2SuaYUmvU6leA5+oZVF/Inle
jdSckxCCd1rbvGd60AY5azD1pAuazijwW9Y9Icv2tS5oZI/4MN7YJEssj/ZLjEA7
Alm0ZQ==
-----END CERTIFICATE-----'''


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


class _CephDashboardCharm(charm.CephDashboardCharm):

    def _get_bind_ip(self):
        return '10.0.0.10'


class TestCephDashboardCharmBase(CharmTestCase):

    PATCHES = [
        'ceph_utils',
        'ch_host',
        'socket',
        'subprocess',
        'ch_host',
    ]

    def setUp(self):
        super().setUp(charm, self.PATCHES)
        self.harness = self.get_harness()

        self.socket.gethostname.return_value = 'server1'
        self.socket.getfqdn.return_value = 'server1.local'

    def get_harness(self):
        initial_config = {'grafana-api-url': None}
        _harness = Harness(
            _CephDashboardCharm,
        )

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

        _harness._backend = _TestingOPSModelBackend(
            _harness._unit_name, _harness._meta)
        _harness._model = model.Model(
            _harness._meta,
            _harness._backend)
        _harness._framework = framework.Framework(
            ":memory:",
            _harness._charm_dir,
            _harness._meta,
            _harness._model)
        # END Workaround
        _harness.update_config(initial_config)
        return _harness

    def test_init(self):
        self.harness.begin()
        self.assertFalse(self.harness.charm._stored.is_started)

    def test_charm_config(self):
        self.ceph_utils.is_dashboard_enabled.return_value = True
        self.ch_host.cmp_pkgrevno.return_value = 0
        basic_boolean = [
            ('enable-password-policy', 'set-pwd-policy-enabled'),
            ('password-policy-check-length',
             'set-pwd-policy-check-length-enabled'),
            ('password-policy-check-oldpwd',
             'set-pwd-policy-check-oldpwd-enabled'),
            ('password-policy-check-username',
             'set-pwd-policy-check-username-enabled'),
            ('password-policy-check-exclusion-list',
             'set-pwd-policy-check-exclusion-list-enabled'),
            ('password-policy-check-complexity',
             'set-pwd-policy-check-complexity-enabled'),
            ('password-policy-check-sequential-chars',
             'set-pwd-policy-check-sequential-chars-enabled'),
            ('password-policy-check-repetitive-chars',
             'set-pwd-policy-check-repetitive-chars-enabled'),
            ('audit-api-enabled',
             'set-audit-api-enabled'),
            ('audit-api-log-payload',
             'set-audit-api-log-payload')]
        expect = []
        for charm_option, ceph_option in basic_boolean:
            expect.append((charm_option, True, [ceph_option, 'True']))
            expect.append((charm_option, False, [ceph_option, 'False']))
        expect.extend([
            ('debug', True, ['debug', 'enable']),
            ('debug', False, ['debug', 'disable'])])
        expect.extend([
            ('motd', 'warning|5w|enough is enough', ['motd', 'warning', '5w',
                                                     'enough is enough']),
            ('motd', '', ['motd', 'clear'])])
        base_cmd = ['ceph', 'dashboard']
        for charm_option, charm_value, expected_options in expect:
            _harness = self.get_harness()
            rel_id = _harness.add_relation('dashboard', 'ceph-mon')
            _harness.add_relation_unit(
                rel_id,
                'ceph-mon/0')
            _harness.update_relation_data(
                rel_id,
                'ceph-mon/0',
                {
                    'mon-ready': 'True'})
            _harness.begin()
            expected_cmd = base_cmd + expected_options
            self.subprocess.check_output.reset_mock()
            _harness.update_config(
                key_values={charm_option: charm_value})
            self.subprocess.check_output.assert_called_once_with(
                expected_cmd,
                stderr=self.subprocess.STDOUT)

    def test__on_ca_available(self):
        rel_id = self.harness.add_relation('certificates', 'vault')
        self.harness.begin()
        self.harness.add_relation_unit(
            rel_id,
            'vault/0')
        self.harness.update_relation_data(
            rel_id,
            'vault/0',
            {'ingress-address': '10.0.0.3'})
        rel_data = self.harness.get_relation_data(rel_id, 'ceph-dashboard/0')
        self.assertEqual(
            rel_data['cert_requests'],
            '{"server1.local": {"sans": ["10.0.0.10", "server1"]}}')

    def test_check_dashboard(self):
        socket_mock = MagicMock()
        self.socket.socket.return_value = socket_mock
        socket_mock.connect_ex.return_value = 0
        self.ceph_utils.is_dashboard_enabled.return_value = True
        self.harness.begin()
        self.assertEqual(
            self.harness.charm.check_dashboard(),
            BlockedStatus('No certificates found. Please add a certifcates '
                          'relation or provide via charm config'))
        self.harness.update_config(
            key_values={
                'ssl_key': base64.b64encode(TEST_KEY.encode("utf-8")),
                'ssl_cert': base64.b64encode(TEST_CERT.encode("utf-8")),
                'ssl_ca': base64.b64encode(TEST_CA.encode("utf-8"))})
        self.assertEqual(
            self.harness.charm.check_dashboard(),
            ActiveStatus())

        socket_mock.connect_ex.return_value = 1
        self.assertEqual(
            self.harness.charm.check_dashboard(),
            BlockedStatus('Dashboard not responding'))

        socket_mock.connect_ex.return_value = 0
        self.ceph_utils.is_dashboard_enabled.return_value = False
        self.assertEqual(
            self.harness.charm.check_dashboard(),
            BlockedStatus('Dashboard is not enabled'))

    def test_check_dashboard_grafana(self):
        socket_mock = MagicMock()
        self.socket.socket.return_value = socket_mock
        socket_mock.connect_ex.return_value = 0
        self.ceph_utils.is_dashboard_enabled.return_value = True
        rel_id = self.harness.add_relation('grafana-dashboard', 'grafana')
        self.harness.begin()
        self.harness.add_relation_unit(
            rel_id,
            'grafana/0')
        self.harness.update_config(
            key_values={
                'ssl_key': base64.b64encode(TEST_KEY.encode("utf-8")),
                'ssl_cert': base64.b64encode(TEST_CERT.encode("utf-8")),
                'ssl_ca': base64.b64encode(TEST_CA.encode("utf-8"))})
        self.assertEqual(
            self.harness.charm.check_dashboard(),
            BlockedStatus('Charm config option grafana-api-url not set'))

    def test_kick_dashboard(self):
        self.harness.begin()
        self.harness.charm.kick_dashboard()
        self.ceph_utils.mgr_disable_dashboard.assert_called_once_with()
        self.ceph_utils.mgr_enable_dashboard.assert_called_once_with()

    def test__configure_dashboard(self):
        self.ceph_utils.is_dashboard_enabled.return_value = True
        rel_id = self.harness.add_relation('dashboard', 'ceph-mon')
        self.harness.begin()
        self.harness.add_relation_unit(
            rel_id,
            'ceph-mon/0')
        self.harness.update_relation_data(
            rel_id,
            'ceph-mon/0',
            {
                'mon-ready': 'True'})

        self.ceph_utils.mgr_config_set.reset_mock()
        self.harness.set_leader(False)
        self.harness.charm._configure_dashboard(None)
        self.assertFalse(self.ceph_utils.mgr_enable_dashboard.called)
        self.ceph_utils.mgr_config_set.assert_called_once_with(
            'mgr/dashboard/server1/server_addr',
            '10.0.0.10')

        self.ceph_utils.mgr_config_set.reset_mock()
        self.ceph_utils.is_dashboard_enabled.return_value = True
        self.harness.set_leader()
        self.harness.charm._configure_dashboard(None)
        self.assertFalse(self.ceph_utils.mgr_enable_dashboard.called)
        self.ceph_utils.mgr_config_set.assert_called_once_with(
            'mgr/dashboard/server1/server_addr',
            '10.0.0.10')

        self.ceph_utils.mgr_config_set.reset_mock()
        self.ceph_utils.is_dashboard_enabled.return_value = False
        self.harness.set_leader()
        self.harness.charm._configure_dashboard(None)
        self.ceph_utils.mgr_enable_dashboard.assert_called_once_with()
        self.ceph_utils.mgr_config_set.assert_called_once_with(
            'mgr/dashboard/server1/server_addr',
            '10.0.0.10')

    def test__get_bind_ip(self):
        self.harness.begin()
        self.assertEqual(
            self.harness.charm._get_bind_ip(),
            '10.0.0.10')

    @patch('socket.gethostname')
    def test_certificates_relation(self, _gethostname):
        self.ceph_utils.is_dashboard_enabled.return_value = True
        mock_TLS_KEY_PATH = MagicMock()
        mock_TLS_CERT_PATH = MagicMock()
        mock_TLS_VAULT_CA_CERT_PATH = MagicMock()
        _gethostname.return_value = 'server1'
        cert_rel_id = self.harness.add_relation('certificates', 'vault')
        dash_rel_id = self.harness.add_relation('dashboard', 'ceph-mon')
        lb_rel_id = self.harness.add_relation(
            'loadbalancer',
            'openstack-loadbalancer')
        self.harness.begin()
        self.harness.set_leader()
        self.harness.charm.TLS_CERT_PATH = mock_TLS_CERT_PATH
        self.harness.charm.TLS_VAULT_CA_CERT_PATH = mock_TLS_VAULT_CA_CERT_PATH
        self.harness.charm.TLS_KEY_PATH = mock_TLS_KEY_PATH
        self.harness.add_relation_unit(
            dash_rel_id,
            'ceph-mon/0')
        self.harness.update_relation_data(
            dash_rel_id,
            'ceph-mon/0',
            {
                'mon-ready': 'True'})
        self.harness.add_relation_unit(
            cert_rel_id,
            'vault/0')
        self.harness.add_relation_unit(
            lb_rel_id,
            'openstack-loadbalancer/0')
        # If lb relation is present but has not responded then certs should
        # not have been requested yet.
        self.assertEqual(
            self.harness.get_relation_data(
                cert_rel_id,
                'ceph-dashboard/0'),
            {})
        self.harness.update_relation_data(
            lb_rel_id,
            'openstack-loadbalancer',
            {
                'frontends': json.dumps(
                    {
                        'ceph-dashboard': {
                            'admin': {
                                'ip': ['10.20.0.101'],
                                'port': 8443,
                                'protocol': 'http'},
                            'internal': {
                                'ip': ['10.30.0.101'],
                                'port': 8443,
                                'protocol': 'http'},
                            'public': {
                                'ip': ['10.10.0.101'],
                                'port': 8443,
                                'protocol': 'http'}}})})
        self.assertNotEqual(
            self.harness.get_relation_data(
                cert_rel_id,
                'ceph-dashboard/0'),
            {})
        self.harness.update_relation_data(
            cert_rel_id,
            'vault/0',
            {
                'ceph-dashboard_0.server.cert': TEST_CERT,
                'ceph-dashboard_0.server.key': TEST_KEY,
                'chain': TEST_CHAIN,
                'ca': TEST_CA})
        mock_TLS_CERT_PATH.write_bytes.assert_called_once()
        mock_TLS_VAULT_CA_CERT_PATH.write_bytes.assert_called_once()
        mock_TLS_KEY_PATH.write_bytes.assert_called_once()
        self.subprocess.check_call.assert_called_once_with(
            ['update-ca-certificates'])
        self.ceph_utils.dashboard_set_ssl_certificate.assert_has_calls([
            call(mock_TLS_CERT_PATH, hostname='server1'),
            call(mock_TLS_CERT_PATH)])
        self.ceph_utils.dashboard_set_ssl_certificate_key.assert_has_calls([
            call(mock_TLS_KEY_PATH, hostname='server1'),
            call(mock_TLS_KEY_PATH)])
        self.ceph_utils.mgr_config_set.assert_has_calls([
            call('mgr/dashboard/standby_behaviour', 'redirect'),
            call('mgr/dashboard/ssl', 'true')])
        self.ceph_utils.mgr_disable_dashboard.assert_called_once_with()
        self.ceph_utils.mgr_enable_dashboard.assert_called_once_with()

    def test_certificates_from_config(self):
        self.ceph_utils.is_dashboard_enabled.return_value = True
        mock_TLS_KEY_PATH = MagicMock()
        mock_TLS_CERT_PATH = MagicMock()
        mock_TLS_CHARM_CA_CERT_PATH = MagicMock()
        dash_rel_id = self.harness.add_relation('dashboard', 'ceph-mon')
        self.harness.begin()
        self.harness.set_leader()
        self.harness.add_relation_unit(
            dash_rel_id,
            'ceph-mon/0')
        self.harness.update_relation_data(
            dash_rel_id,
            'ceph-mon/0',
            {
                'mon-ready': 'True'})
        self.harness.charm.TLS_CERT_PATH = mock_TLS_CERT_PATH
        self.harness.charm.TLS_CHARM_CA_CERT_PATH = mock_TLS_CHARM_CA_CERT_PATH
        self.harness.charm.TLS_KEY_PATH = mock_TLS_KEY_PATH
        self.subprocess.check_call.reset_mock()
        self.harness.update_config(
            key_values={
                'ssl_key': base64.b64encode(TEST_KEY.encode("utf-8")),
                'ssl_cert': base64.b64encode(TEST_CERT.encode("utf-8")),
                'ssl_ca': base64.b64encode(TEST_CA.encode("utf-8"))})
        self.subprocess.check_call.assert_called_once_with(
            ['update-ca-certificates'])
        self.ceph_utils.dashboard_set_ssl_certificate.assert_has_calls([
            call(mock_TLS_CERT_PATH, hostname='server1'),
            call(mock_TLS_CERT_PATH)])
        self.ceph_utils.dashboard_set_ssl_certificate_key.assert_has_calls([
            call(mock_TLS_KEY_PATH, hostname='server1'),
            call(mock_TLS_KEY_PATH)])
        self.ceph_utils.mgr_config_set.assert_has_calls([
            call('mgr/dashboard/standby_behaviour', 'redirect'),
            call('mgr/dashboard/ssl', 'true')])
        self.ceph_utils.mgr_disable_dashboard.assert_called_once_with()
        self.ceph_utils.mgr_enable_dashboard.assert_called_once_with()

    def test_rados_gateway(self):
        self.ceph_utils.is_dashboard_enabled.return_value = True
        self.ch_host.cmp_pkgrevno.return_value = 1
        mon_rel_id = self.harness.add_relation('dashboard', 'ceph-mon')
        rel_id = self.harness.add_relation('radosgw-dashboard', 'ceph-radosgw')
        self.harness.begin()
        self.harness.set_leader()
        self.harness.add_relation_unit(
            mon_rel_id,
            'ceph-mon/0')
        self.harness.update_relation_data(
            mon_rel_id,
            'ceph-mon/0',
            {
                'mon-ready': 'True'})
        self.harness.add_relation_unit(
            rel_id,
            'ceph-radosgw/0')
        self.harness.add_relation_unit(
            rel_id,
            'ceph-radosgw/1')
        self.harness.update_relation_data(
            rel_id,
            'ceph-radosgw/0',
            {
                'daemon-id': 'juju-80416c-zaza-7af97ef8a776-3'})
        self.harness.update_relation_data(
            rel_id,
            'ceph-radosgw/1',
            {
                'daemon-id': 'juju-80416c-zaza-7af97ef8a776-4'})
        self.harness.update_relation_data(
            rel_id,
            'ceph-radosgw',
            {
                'access-key': 'XNUZVPL364U0BL1OXWJZ',
                'secret-key': 'SgBo115xJcW90nkQ5EaNQ6fPeyeUUT0GxhwQbLFo',
                'uid': 'radosgw-user-9'})
        self.subprocess.check_output.assert_has_calls([
            call(['ceph', 'dashboard', 'set-rgw-api-access-key', '-i', ANY],
                 stderr=self.subprocess.STDOUT),
            call().decode('UTF-8'),
            call(['ceph', 'dashboard', 'set-rgw-api-secret-key', '-i', ANY],
                 stderr=self.subprocess.STDOUT),
            call().decode('UTF-8'),
        ])

    def test_rados_gateway_multi_relations_pacific(self):
        self.ceph_utils.is_dashboard_enabled.return_value = True
        self.ch_host.cmp_pkgrevno.return_value = 1
        rel_id1 = self.harness.add_relation('radosgw-dashboard', 'ceph-eu')
        rel_id2 = self.harness.add_relation('radosgw-dashboard', 'ceph-us')
        mon_rel_id = self.harness.add_relation('dashboard', 'ceph-mon')
        self.harness.begin()
        self.harness.set_leader()
        self.harness.add_relation_unit(
            mon_rel_id,
            'ceph-mon/0')
        self.harness.update_relation_data(
            mon_rel_id,
            'ceph-mon/0',
            {
                'mon-ready': 'True'})
        self.harness.add_relation_unit(
            rel_id1,
            'ceph-eu/0')
        self.harness.add_relation_unit(
            rel_id2,
            'ceph-us/0')
        self.harness.update_relation_data(
            rel_id1,
            'ceph-eu/0',
            {
                'daemon-id': 'juju-80416c-zaza-7af97ef8a776-3'})
        self.harness.update_relation_data(
            rel_id2,
            'ceph-us/0',
            {
                'daemon-id': 'juju-dddddd-zaza-sdfsfsfs-4'})
        self.harness.update_relation_data(
            rel_id1,
            'ceph-eu',
            {
                'access-key': 'XNUZVPL364U0BL1OXWJZ',
                'secret-key': 'SgBo115xJcW90nkQ5EaNQ6fPeyeUUT0GxhwQbLFo',
                'uid': 'radosgw-user-9'})
        self.subprocess.check_output.reset_mock()
        self.harness.update_relation_data(
            rel_id2,
            'ceph-us',
            {
                'access-key': 'JGHKJGDKJGJGJHGYYYYM',
                'secret-key': 'iljkdfhHKHKd88LKxNLSKDiijfjfjfldjfjlf44',
                'uid': 'radosgw-user-10'})
        self.subprocess.check_output.assert_has_calls([
            call(['ceph', 'dashboard', 'set-rgw-api-access-key', '-i', ANY],
                 stderr=self.subprocess.STDOUT),
            call().decode('UTF-8'),
            call(['ceph', 'dashboard', 'set-rgw-api-secret-key', '-i', ANY],
                 stderr=self.subprocess.STDOUT),
            call().decode('UTF-8'),
        ])

    def test_rados_gateway_multi_relations_octopus(self):
        self.ch_host.cmp_pkgrevno.return_value = -1
        rel_id1 = self.harness.add_relation('radosgw-dashboard', 'ceph-eu')
        rel_id2 = self.harness.add_relation('radosgw-dashboard', 'ceph-us')
        self.harness.begin()
        self.harness.set_leader()
        self.harness.add_relation_unit(
            rel_id1,
            'ceph-eu/0')
        self.harness.add_relation_unit(
            rel_id2,
            'ceph-us/0')
        self.harness.update_relation_data(
            rel_id1,
            'ceph-eu/0',
            {
                'daemon-id': 'juju-80416c-zaza-7af97ef8a776-3'})
        self.harness.update_relation_data(
            rel_id2,
            'ceph-us/0',
            {
                'daemon-id': 'juju-dddddd-zaza-sdfsfsfs-4'})
        self.harness.update_relation_data(
            rel_id1,
            'ceph-eu',
            {
                'access-key': 'XNUZVPL364U0BL1OXWJZ',
                'secret-key': 'SgBo115xJcW90nkQ5EaNQ6fPeyeUUT0GxhwQbLFo',
                'uid': 'radosgw-user-9'})
        self.subprocess.check_output.reset_mock()
        self.harness.update_relation_data(
            rel_id2,
            'ceph-us',
            {
                'access-key': 'JGHKJGDKJGJGJHGYYYYM',
                'secret-key': 'iljkdfhHKHKd88LKxNLSKDiijfjfjfldjfjlf44',
                'uid': 'radosgw-user-10'})
        self.assertFalse(self.subprocess.check_output.called)

    @patch.object(charm.secrets, 'choice')
    def test__gen_user_password(self, _choice):
        self.harness.begin()
        _choice.return_value = 'r'
        self.assertEqual(
            self.harness.charm._gen_user_password(),
            'rrrrrrrrrrrr')

    @patch.object(charm.tempfile, 'NamedTemporaryFile')
    @patch.object(charm.secrets, 'choice')
    def test__add_user_action(self, _choice, _NTFile):
        self.subprocess.check_output.return_value = b''
        _NTFile.return_value.__enter__.return_value.name = 'tempfilename'
        _choice.return_value = 'r'
        self.harness.begin()
        action_event = MagicMock()
        action_event.params = {
            'username': 'auser',
            'role': 'administrator'}
        self.harness.charm._add_user_action(action_event)
        self.subprocess.check_output.assert_called_once_with(
            ['ceph', 'dashboard', 'ac-user-create', '--enabled',
             '-i', 'tempfilename', 'auser', 'administrator'])

    def test__delete_user_action(self):
        self.subprocess.check_output.return_value = b''
        self.harness.begin()
        action_event = MagicMock()
        action_event.params = {
            'username': 'auser'}
        self.harness.charm._delete_user_action(action_event)
        self.subprocess.check_output.assert_called_once_with(
            ['ceph', 'dashboard', 'ac-user-delete', 'auser'],
            stderr=self.subprocess.STDOUT)
