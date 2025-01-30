#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
from unittest import mock
from unittest.mock import patch
import unittest
from ops.testing import Harness

import ceph_mds
import charm
from manage_test_relations import (
    add_ceph_mds_relation,
)


@patch("charm.hooks")
class TestCephShared(unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.harness = Harness(charm.CephMonCharm)
        self.addCleanup(self.harness.cleanup)

    def test_init(self, _hooks):
        self.harness.begin()
        mds = ceph_mds.CephMdsProvides(self.harness.charm)
        self.assertTrue(mds.this_unit)

    @mock.patch("src.charm.ceph_client.ceph.is_leader")
    @mock.patch("src.charm.ceph_mds.leader_get", return_value="test-fsid")
    @mock.patch("src.charm.ceph_mds.ceph")
    @mock.patch.object(charm.CephMonCharm, "process_broker_request")
    @mock.patch("src.charm.ceph_client.ceph.get_named_key")
    @mock.patch("src.charm.ceph_client.get_rbd_features")
    @mock.patch("src.charm.ceph_client.get_public_addr")
    @mock.patch.object(charm.CephMonCharm, "ready_for_service")
    @mock.patch("src.charm.ceph_client.send_osd_settings")
    def test_client_relation_broker(
        self,
        _send_osd_settings,
        mock_ready_for_service,
        mock_get_public_addr,
        mock_get_rbd_features,
        mock_get_named_key,
        mock_process_broker_request,
        mock_ceph_utils,
        mock_leader_get,
        mock_is_leader,
        _hooks,
    ):
        mock_get_public_addr.return_value = "127.0.0.1"
        mock_ready_for_service.return_value = True
        mock_get_rbd_features.return_value = 42
        mock_get_named_key.return_value = "test key"
        mock_process_broker_request.return_value = "AOK"
        mock_ceph_utils.get_mds_key.return_value = "test-mds-key"
        mock_is_leader.return_value = True
        self.harness.begin()
        self.harness.set_leader()
        mds = ceph_mds.CephMdsProvides(self.harness.charm)
        rel_id = add_ceph_mds_relation(self.harness)
        self.harness.update_relation_data(
            rel_id, "ceph-fs/0", {"broker_req": '{"request-id": "req"}'}
        )
        self.assertEqual(mds._mds_name, "ceph-fs")
        mock_leader_get.assert_called_with("fsid")
        unit_rel_data = self.harness.get_relation_data(rel_id, "ceph-mon/0")
        self.assertEqual(
            unit_rel_data,
            {
                "auth": "cephx",
                "ceph-public-address": "127.0.0.1",
                "key": "test key",
                "rbd-features": "42",
                "broker-rsp-ceph-fs-0": "AOK",
                "broker_rsp": "AOK",
                'ceph-fs_mds_key': 'test-mds-key',
                'fsid': 'test-fsid',

            },
        )
        mock_process_broker_request.reset_mock()
