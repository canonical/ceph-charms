# Copyright 2026 Canonical Ltd
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#  http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


import unittest.mock as mock
from test_utils import CharmTestCase
from ops.testing import Harness

with mock.patch('charmhelpers.contrib.hardening.harden.harden') as mock_dec:
    mock_dec.side_effect = (lambda *dargs, **dkwargs: lambda f:
                            lambda *args, **kwargs: f(*args, **kwargs))
    from src.charm import CephMonCharm


class TestCephClientKeyHandling(CharmTestCase):
    """Test cephx key rotation behaviour, covering the LP#2125295 race fix.

    The three branches in _handle_client_relation:
      1. No 'application-name' in client data AND no existing key
         → call get_named_key (first join, no key yet)
      2. No 'application-name' in client data AND key already exists
         → reuse existing key (race condition guard - the LP#2125295 fix)
      3. 'application-name' present in client data
         → call get_named_key with that name (client is ready)
    """

    def setUp(self):
        super().setUp()
        self.harness = Harness(CephMonCharm)
        self.addCleanup(self.harness.cleanup)

        patches = {
            'send_osd_settings': mock.patch(
                "src.charm.ceph_client.send_osd_settings"),
            'get_public_addr': mock.patch(
                "src.charm.ceph_client.get_public_addr"),
            'get_rbd_features': mock.patch(
                "src.charm.ceph_client.get_rbd_features"),
            'get_named_key': mock.patch(
                "src.charm.ceph_client.ceph.get_named_key"),
            'ready_for_service': mock.patch.object(
                CephMonCharm, "ready_for_service"),
        }
        for name, p in patches.items():
            setattr(self, 'mock_' + name, p.start())
            self.addCleanup(p.stop)

        self.mock_get_public_addr.return_value = '127.0.0.1'
        self.mock_get_rbd_features.return_value = None
        self.mock_get_named_key.return_value = 'original-key'
        self.mock_ready_for_service.return_value = True

        self.harness.begin()
        self.harness.set_leader()

    def test_initial_join_calls_get_named_key(self):
        """On first join (no key set yet), get_named_key is called.

        Branch 1: ceph_key is None, so the else branch runs and
        get_named_key is called to generate the initial key.
        """
        rel_id = self.harness.add_relation('client', 'glance')
        self.harness.add_relation_unit(rel_id, 'glance/0')

        self.mock_get_named_key.assert_called_once_with('glance')
        unit_rel_data = self.harness.get_relation_data(rel_id, 'ceph-mon/0')
        self.assertEqual(unit_rel_data['key'], 'original-key')

    def test_reuse_existing_key_when_no_application_name(self):
        """Existing key is reused when client hasn't set application-name yet.

        Branch 2 (LP#2125295 fix): when a relation-changed fires before the
        client hook has set 'application-name', the key already written by
        ceph-mon must not be overwritten with a potentially rotated key.
        """
        rel_id = self.harness.add_relation('client', 'glance')
        self.harness.add_relation_unit(rel_id, 'glance/0')

        # Simulate key rotation: get_named_key would now return a new key.
        self.mock_get_named_key.return_value = 'rotated-key'
        self.mock_get_named_key.reset_mock()

        # Client fires relation-changed without setting application-name.
        self.harness.update_relation_data(
            rel_id, 'glance/0', {'ingress-address': '10.0.0.3'})

        self.mock_get_named_key.assert_not_called()
        unit_rel_data = self.harness.get_relation_data(rel_id, 'ceph-mon/0')
        self.assertEqual(
            unit_rel_data['key'], 'original-key',
            "Key must not change while client hasn't set application-name")

    def test_get_named_key_called_when_application_name_set(self):
        """get_named_key is called with application-name once client is ready.

        Branch 3: when the client sets 'application-name' in its relation
        data, that signals it has finished configuring and the correct named
        key should be fetched using that name.
        """
        rel_id = self.harness.add_relation('client', 'glance')
        self.harness.add_relation_unit(rel_id, 'glance/0')

        self.mock_get_named_key.return_value = 'named-key'
        self.mock_get_named_key.reset_mock()

        # Client sets application-name to signal readiness.
        self.harness.update_relation_data(
            rel_id, 'glance/0', {'application-name': 'custom-nova'})

        self.mock_get_named_key.assert_called_once_with('custom-nova')
        unit_rel_data = self.harness.get_relation_data(rel_id, 'ceph-mon/0')
        self.assertEqual(unit_rel_data['key'], 'named-key')
