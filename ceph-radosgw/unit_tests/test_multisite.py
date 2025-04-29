# Copyright 2019 Canonical Ltd
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#  http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import inspect
import os
import mock

import multisite

from test_utils import CharmTestCase


def whoami():
    return inspect.stack()[1][3]


class TestMultisiteHelpers(CharmTestCase):

    TO_PATCH = [
        'subprocess',
        'socket',
        'hookenv',
        'utils',
    ]

    def setUp(self):
        super(TestMultisiteHelpers, self).setUp(multisite, self.TO_PATCH)
        self.socket.gethostname.return_value = 'testhost'
        self.utils.request_per_unit_key.return_value = True

    def _testdata(self, funcname):
        return os.path.join(os.path.dirname(__file__),
                            'testdata',
                            '{}.json'.format(funcname))

    def test___key_name(self):
        self.assertEqual(
            multisite._key_name(),
            'rgw.testhost')
        self.utils.request_per_unit_key.return_value = False
        self.assertEqual(
            multisite._key_name(),
            'radosgw.gateway')

    def test_create_realm(self):
        with open(self._testdata(whoami()), 'rb') as f:
            self.subprocess.check_output.return_value = f.read()
            result = multisite.create_realm('beedata', default=True)
            self.assertEqual(result['name'], 'beedata')
            self.subprocess.check_output.assert_called_with([
                'radosgw-admin', '--id=rgw.testhost',
                'realm', 'create',
                '--rgw-realm=beedata', '--default'
            ], stderr=mock.ANY)

    def test_list_realms(self):
        with open(self._testdata(whoami()), 'rb') as f:
            self.subprocess.check_output.return_value = f.read()
            result = multisite.list_realms()
            self.assertTrue('beedata' in result)

    def test_set_default_zone(self):
        multisite.set_default_realm('newrealm')
        self.subprocess.check_call.assert_called_with([
            'radosgw-admin', '--id=rgw.testhost',
            'realm', 'default',
            '--rgw-realm=newrealm'
        ])

    def test_create_zonegroup(self):
        with open(self._testdata(whoami()), 'rb') as f:
            self.subprocess.check_output.return_value = f.read()
            result = multisite.create_zonegroup(
                'brundall',
                endpoints=['http://localhost:80'],
                master=True,
                default=True,
                realm='beedata',
            )
            self.assertEqual(result['name'], 'brundall')
            self.subprocess.check_output.assert_called_with([
                'radosgw-admin', '--id=rgw.testhost',
                'zonegroup', 'create',
                '--rgw-zonegroup=brundall',
                '--endpoints=http://localhost:80',
                '--rgw-realm=beedata',
                '--default',
                '--master'
            ], stderr=mock.ANY)

    def test_list_zonegroups(self):
        with open(self._testdata(whoami()), 'rb') as f:
            self.subprocess.check_output.return_value = f.read()
            result = multisite.list_zonegroups()
            self.assertTrue('brundall' in result)

    def test_create_zone(self):
        with open(self._testdata(whoami()), 'rb') as f:
            self.subprocess.check_output.return_value = f.read()
            result = multisite.create_zone(
                'brundall-east',
                endpoints=['http://localhost:80'],
                master=True,
                default=True,
                zonegroup='brundall',
                access_key='mykey',
                secret='mypassword',
            )
            self.assertEqual(result['name'], 'brundall-east')
            self.subprocess.check_output.assert_called_with([
                'radosgw-admin', '--id=rgw.testhost',
                'zone', 'create',
                '--rgw-zone=brundall-east',
                '--endpoints=http://localhost:80',
                '--rgw-zonegroup=brundall',
                '--default', '--master',
                '--access-key=mykey',
                '--secret=mypassword',
                '--read-only=0',
            ], stderr=mock.ANY)

    def test_modify_zone(self):
        multisite.modify_zone(
            'brundall-east',
            endpoints=['http://localhost:80', 'https://localhost:443'],
            access_key='mykey',
            secret='secret',
            readonly=True
        )
        self.subprocess.check_output.assert_called_with([
            'radosgw-admin', '--id=rgw.testhost',
            'zone', 'modify',
            '--rgw-zone=brundall-east',
            '--endpoints=http://localhost:80,https://localhost:443',
            '--access-key=mykey', '--secret=secret',
            '--read-only=1',
        ], stderr=mock.ANY)

    def test_modify_zone_promote_master(self):
        multisite.modify_zone(
            'brundall-east',
            default=True,
            master=True,
        )
        self.subprocess.check_output.assert_called_with([
            'radosgw-admin', '--id=rgw.testhost',
            'zone', 'modify',
            '--rgw-zone=brundall-east',
            '--master',
            '--default',
            '--read-only=0',
        ], stderr=mock.ANY)

    def test_modify_zone_partial_credentials(self):
        multisite.modify_zone(
            'brundall-east',
            endpoints=['http://localhost:80', 'https://localhost:443'],
            access_key='mykey',
        )
        self.subprocess.check_output.assert_called_with([
            'radosgw-admin', '--id=rgw.testhost',
            'zone', 'modify',
            '--rgw-zone=brundall-east',
            '--endpoints=http://localhost:80,https://localhost:443',
            '--read-only=0',
        ], stderr=mock.ANY)

    def test_list_zones(self):
        with open(self._testdata(whoami()), 'rb') as f:
            self.subprocess.check_output.return_value = f.read()
            result = multisite.list_zones()
            self.assertTrue('brundall-east' in result)

    def test_update_period(self):
        multisite.update_period()
        self.subprocess.check_call.assert_called_once_with([
            'radosgw-admin', '--id=rgw.testhost',
            'period', 'update', '--commit'
        ])

    @mock.patch.object(multisite, 'list_zonegroups')
    @mock.patch.object(multisite, 'list_zones')
    @mock.patch.object(multisite, 'update_period')
    def test_tidy_defaults(self,
                           mock_update_period,
                           mock_list_zones,
                           mock_list_zonegroups):
        mock_list_zones.return_value = ['default']
        mock_list_zonegroups.return_value = ['default']
        multisite.tidy_defaults()
        self.subprocess.call.assert_has_calls([
            mock.call(['radosgw-admin', '--id=rgw.testhost',
                       'zonegroup', 'remove',
                       '--rgw-zonegroup=default', '--rgw-zone=default']),
            mock.call(['radosgw-admin', '--id=rgw.testhost',
                       'zone', 'delete',
                       '--rgw-zone=default']),
            mock.call(['radosgw-admin', '--id=rgw.testhost',
                       'zonegroup', 'delete',
                       '--rgw-zonegroup=default'])
        ])
        mock_update_period.assert_called_with()

    @mock.patch.object(multisite, 'list_zonegroups')
    @mock.patch.object(multisite, 'list_zones')
    @mock.patch.object(multisite, 'update_period')
    def test_tidy_defaults_noop(self,
                                mock_update_period,
                                mock_list_zones,
                                mock_list_zonegroups):
        mock_list_zones.return_value = ['brundall-east']
        mock_list_zonegroups.return_value = ['brundall']
        multisite.tidy_defaults()
        self.subprocess.call.assert_not_called()
        mock_update_period.assert_not_called()

    def test_pull_realm(self):
        multisite.pull_realm(url='http://master:80',
                             access_key='testkey',
                             secret='testsecret')
        self.subprocess.check_output.assert_called_once_with([
            'radosgw-admin', '--id=rgw.testhost',
            'realm', 'pull',
            '--url=http://master:80',
            '--access-key=testkey', '--secret=testsecret',
        ], stderr=mock.ANY)

    def test_pull_period(self):
        multisite.pull_period(url='http://master:80',
                              access_key='testkey',
                              secret='testsecret')
        self.subprocess.check_output.assert_called_once_with([
            'radosgw-admin', '--id=rgw.testhost',
            'period', 'pull',
            '--url=http://master:80',
            '--access-key=testkey', '--secret=testsecret',
        ], stderr=mock.ANY)
