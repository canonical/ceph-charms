#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

from unittest.mock import patch
import unittest

from ops import model
from ops.testing import Harness

import ceph_status
import charm

from charmhelpers.contrib.storage.linux import ceph as ch_ceph


@patch("charm.hooks")
class TestCephStatus(unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.harness = Harness(charm.CephMonCharm)
        self.addCleanup(self.harness.cleanup)

    def test_init(self, _hooks):
        self.harness.begin()
        status = ceph_status.StatusAssessor(self.harness.charm)
        self.assertTrue(status.charm.custom_status_checks)

    def test_check_insecure_cmr(self, _hooks):
        self.harness.begin()
        status = ceph_status.StatusAssessor(self.harness.charm)
        result = status.check_insecure_cmr()
        self.assertIsInstance(result, model.ActiveStatus)
        self.harness.add_relation_unit(
            self.harness.add_relation("client", "remote"), "remote-foo/0"
        )
        result = status.check_insecure_cmr()
        self.assertIsInstance(result, model.BlockedStatus)

    def test_check_moncount(self, _hooks):
        self.harness.begin()
        status = ceph_status.StatusAssessor(self.harness.charm)
        result = status.check_moncount()
        self.assertIsInstance(result, model.BlockedStatus)
        rel_id = self.harness.add_relation("mon", "ceph-mon")
        for n in (0, 1, 2):
            self.harness.add_relation_unit(rel_id, "ceph-mon/{}".format(n))
        result = status.check_moncount()
        self.assertIsInstance(result, model.ActiveStatus)

    def test_check_ready_mons(self, _hooks):
        self.harness.begin()
        status = ceph_status.StatusAssessor(self.harness.charm)
        result = status.check_ready_mons()
        self.assertIsInstance(result, model.WaitingStatus)
        rel_id = self.harness.add_relation("mon", "ceph-mon")
        for n in (0, 1, 2):
            self.harness.add_relation_unit(rel_id, "ceph-mon/{}".format(n))
            self.harness.update_relation_data(
                rel_id, "ceph-mon/{}".format(n), {"ceph-public-address": "foo"}
            )
        result = status.check_ready_mons()
        self.assertIsInstance(result, model.ActiveStatus)

    @patch("ceph_status.ch_ceph.get_osd_settings")
    def test_check_get_osd_settings(self, get_osd_settings, _hooks):
        self.harness.begin()
        status = ceph_status.StatusAssessor(self.harness.charm)
        result = status.check_get_osd_settings()
        self.assertIsInstance(result, model.ActiveStatus)
        get_osd_settings.side_effect = ch_ceph.OSDSettingConflict(
            "testexception"
        )
        result = status.check_get_osd_settings()
        self.assertIsInstance(result, model.BlockedStatus)

    def test_check_alert_rule_errors(self, _hooks):
        self.harness.begin()
        status = ceph_status.StatusAssessor(self.harness.charm)
        with patch.object(
            self.harness.charm,
            "metrics_endpoint",
            create=True,
        ) as metrics_endpoint:
            metrics_endpoint.have_alert_rule_errors.return_value = True
            result = status.check_alert_rule_errors()
            self.assertIsInstance(result, model.BlockedStatus)

            metrics_endpoint.have_alert_rule_errors.return_value = False
            result = status.check_alert_rule_errors()
            self.assertIsInstance(result, model.ActiveStatus)

    @patch("ceph_status.ceph_utils")
    def test_check_expected_osd_count(self, ceph_utils, _hooks):
        self.harness.begin()
        status = ceph_status.StatusAssessor(self.harness.charm)

        # not bootstrapped
        ceph_utils.is_bootstrapped.return_value = False
        ceph_utils.is_quorum.return_value = False
        result = status.check_expected_osd_count()
        self.assertIsInstance(result, model.BlockedStatus)
        self.assertEqual(result.message, "Unit not clustered (no quorum)")

        # bootstrapped, no osd rel
        ceph_utils.is_bootstrapped.return_value = True
        ceph_utils.is_quorum.return_value = True
        result = status.check_expected_osd_count()
        self.assertIsInstance(result, model.BlockedStatus)
        self.assertEqual(result.message, "Missing relation: OSD")

        # bootstrapped, enough osds
        rel_id = self.harness.add_relation("osd", "ceph-osd")
        for n in (0, 1, 2):
            self.harness.add_relation_unit(rel_id, "ceph-osd/{}".format(n))
            self.harness.update_relation_data(
                rel_id, "ceph-osd/{}".format(n), {"bootstrapped-osds": "1"}
            )
        result = status.check_expected_osd_count()
        self.assertIsInstance(result, model.ActiveStatus)
