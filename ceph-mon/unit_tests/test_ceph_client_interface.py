# Copyright 2022 Canonical Ltd
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

"""Tests for reweight_osd action."""

# import json
import unittest.mock as mock
from test_utils import CharmTestCase
from ops.testing import Harness
from manage_test_relations import (
    add_ceph_client_relation,
    add_ceph_mds_relation,
)

with mock.patch('charmhelpers.contrib.hardening.harden.harden') as mock_dec:
    mock_dec.side_effect = (lambda *dargs, **dkwargs: lambda f:
                            lambda *args, **kwargs: f(*args, **kwargs))
    # src.charm imports ceph_hooks, so we need to workaround the inclusion
    # of the 'harden' decorator.
    from src.charm import CephMonCharm


class CephClientTestCase(CharmTestCase):
    """Run tests for action."""

    def setUp(self):
        self.harness = Harness(CephMonCharm)
        self.addCleanup(self.harness.cleanup)

    @mock.patch("src.charm.ceph_client.ceph.get_named_key")
    @mock.patch("src.charm.ceph_client.get_rbd_features")
    @mock.patch("src.charm.ceph_client.get_public_addr")
    @mock.patch.object(CephMonCharm, "ready_for_service")
    @mock.patch("src.charm.ceph_client.send_osd_settings")
    def test_client_relation(
            self, _send_osd_settings, mock_ready_for_service,
            mock_get_public_addr, mock_get_rbd_features, mock_get_named_key):
        mock_get_public_addr.return_value = '127.0.0.1'
        mock_ready_for_service.return_value = True
        mock_get_rbd_features.return_value = 42
        mock_get_named_key.return_value = 'test key'
        self.harness.begin()
        self.harness.set_leader()
        rel_id = add_ceph_client_relation(self.harness)
        unit_rel_data = self.harness.get_relation_data(
            rel_id,
            'ceph-mon/0')
        self.assertEqual(
            unit_rel_data,
            {
                'auth': 'cephx',
                'ceph-public-address': '127.0.0.1',
                'key': 'test key',
                'rbd-features': '42',
            })

    @mock.patch("src.charm.ceph_client.ceph.is_leader")
    @mock.patch.object(CephMonCharm, "process_broker_request")
    @mock.patch("src.charm.ceph_client.ceph.get_named_key")
    @mock.patch("src.charm.ceph_client.get_rbd_features")
    @mock.patch("src.charm.ceph_client.get_public_addr")
    @mock.patch.object(CephMonCharm, "ready_for_service")
    @mock.patch("src.charm.ceph_client.send_osd_settings")
    def test_client_relation_broker(
            self, _send_osd_settings, mock_ready_for_service,
            mock_get_public_addr, mock_get_rbd_features, mock_get_named_key,
            mock_process_broker_request, mock_is_leader):
        mock_get_public_addr.return_value = '127.0.0.1'
        mock_ready_for_service.return_value = True
        mock_get_rbd_features.return_value = 42
        mock_get_named_key.return_value = 'test key'
        mock_process_broker_request.return_value = 'AOK'
        mock_is_leader.return_value = True
        self.harness.begin()
        self.harness.set_leader()
        rel_id = add_ceph_client_relation(self.harness)
        self.harness.update_relation_data(
            rel_id,
            'glance/0',
            {'broker_req': '{"request-id": "req"}'})
        mock_process_broker_request.assert_called_once_with(
            'req', '{"request-id": "req"}'
        )
        unit_rel_data = self.harness.get_relation_data(
            rel_id,
            'ceph-mon/0')
        self.assertEqual(
            unit_rel_data,
            {
                'auth': 'cephx',
                'ceph-public-address': '127.0.0.1',
                'key': 'test key',
                'rbd-features': '42',
                'broker-rsp-glance-0': 'AOK',
                'broker_rsp': 'AOK'
            })
        mock_process_broker_request.reset_mock()
        self.harness.update_relation_data(
            rel_id,
            'glance/0',
            {'broker_req': '{"request-id": "req"}'})
        mock_process_broker_request.assert_not_called()

    @mock.patch("src.charm.hooks.mds_relation_joined")
    @mock.patch("src.charm.ceph_client.ceph.get_named_key")
    @mock.patch("src.charm.ceph_client.get_rbd_features")
    @mock.patch("src.charm.ceph_client.get_public_addr")
    @mock.patch.object(CephMonCharm, "ready_for_service")
    @mock.patch("src.charm.ceph_client.send_osd_settings")
    def test_notify_clients(
            self, _send_osd_settings, mock_ready_for_service,
            mock_get_public_addr, mock_get_rbd_features, mock_get_named_key,
            mock_mds_relation_joined):
        mock_get_public_addr.return_value = '127.0.0.1'
        mock_ready_for_service.return_value = True
        mock_get_rbd_features.return_value = None
        mock_get_named_key.return_value = 'test key'
        self.harness.begin()
        self.harness.set_leader()
        rel_id = add_ceph_client_relation(self.harness)
        add_ceph_mds_relation(self.harness)

        unit_rel_data = self.harness.get_relation_data(
            rel_id,
            'ceph-mon/0')
        self.assertEqual(
            unit_rel_data,
            {
                'auth': 'cephx',
                'ceph-public-address': '127.0.0.1',
                'key': 'test key',
            })
        mock_get_rbd_features.return_value = 42
        self.harness.charm.on.notify_clients.emit()
        unit_rel_data = self.harness.get_relation_data(
            rel_id,
            'ceph-mon/0')
        self.assertEqual(
            unit_rel_data,
            {
                'auth': 'cephx',
                'ceph-public-address': '127.0.0.1',
                'key': 'test key',
                'rbd-features': '42',
            })

        mock_mds_relation_joined.assert_called_with(
            relid='1', unit='ceph-fs/0')
