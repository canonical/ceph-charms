#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

from unittest.mock import patch
import unittest

from ops.testing import Harness

import ceph_shared
import charm


@patch("charm.hooks")
class TestCephShared(unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.harness = Harness(charm.CephMonCharm)
        self.addCleanup(self.harness.cleanup)

    def test_init(self, _hooks):
        self.harness.begin()
        ceph_info = ceph_shared.CephMonInfo(self.harness.charm)
        self.assertTrue(ceph_info.relations)

    def test_get_peer_mons(self, _hooks):
        self.harness.begin()
        self.harness.set_leader(True)
        ceph_info = ceph_shared.CephMonInfo(self.harness.charm)
        self.harness.add_relation_unit(
            self.harness.add_relation("mon", "ceph-mon"), "ceph-mon/0"
        )
        peer_mons = ceph_info.get_peer_mons()
        self.assertEqual(len(peer_mons), 1)
        peer = list(peer_mons.keys())[0]
        self.assertEqual(peer.name, "ceph-mon/0")

    def test_not_sufficient_osds(self, _hooks):
        self.harness.begin()
        ceph_info = ceph_shared.CephMonInfo(self.harness.charm)
        rel_id = self.harness.add_relation("osd", "ceph-osd")
        self.harness.add_relation_unit(rel_id, "ceph-osd/0")
        have_enough = ceph_info.sufficient_osds(minimum_osds=77)
        self.assertFalse(have_enough)

    def test_sufficient_osds(self, _hooks):
        self.harness.begin()
        ceph_info = ceph_shared.CephMonInfo(self.harness.charm)
        rel_id = self.harness.add_relation("osd", "ceph-osd")
        self.harness.add_relation_unit(rel_id, "ceph-osd/0")
        self.harness.update_relation_data(
            rel_id, "ceph-osd/0", {"bootstrapped-osds": "77"}
        )
        have_enough = ceph_info.sufficient_osds(minimum_osds=77)
        self.assertTrue(have_enough)
