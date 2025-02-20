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

import unittest
import sys
sys.path.append('lib')  # noqa
sys.path.append('src')  # noqa
from ops.testing import Harness
from ops.charm import CharmBase, CharmMeta
import interface_dashboard


class MyCharm(CharmBase):

    def __init__(self, *args):
        super().__init__(*args)
        self.framework.meta = CharmMeta.from_yaml(metadata='''
name: my-charm
requires:
  dashboard:
    interface: ceph-dashboard
    scope: container
''')

        self.seen_events = []
        self.mon = interface_dashboard.CephDashboardRequires(
            self,
            'dashboard')

        self.framework.observe(
            self.mon.on.mon_ready,
            self._log_event)

    def _log_event(self, event):
        self.seen_events.append(type(event).__name__)


class TestCephDashboardRequires(unittest.TestCase):

    def setUp(self):
        super().setUp()
        self.harness = Harness(
            MyCharm,
        )

    def add_dashboard_relation(self):
        rel_id = self.harness.add_relation('dashboard', 'ceph-mon')
        self.harness.add_relation_unit(
            rel_id,
            'ceph-mon/0')
        return rel_id

    def test_relation_name(self):
        self.harness.begin()
        self.assertEqual(
            self.harness.charm.mon.relation_name,
            'dashboard')

    def test_dashboard_relation(self):
        self.harness.begin()
        self.assertIsNone(
            self.harness.charm.mon.dashboard_relation)
        rel_id = self.add_dashboard_relation()
        self.assertEqual(
            self.harness.charm.mon.dashboard_relation.id,
            rel_id)

    def test_on_changed(self):
        self.harness.begin()
        # No MonReadyEvent as relation is absent
        self.assertEqual(
            self.harness.charm.seen_events,
            [])
        rel_id = self.add_dashboard_relation()
        # No MonReadyEvent as ceph-mon has not declared it is ready.
        self.assertEqual(
            self.harness.charm.seen_events,
            [])
        self.harness.update_relation_data(
            rel_id,
            'ceph-mon/0',
            {'mon-ready': 'True'})
        self.assertEqual(
            self.harness.charm.seen_events,
            ['MonReadyEvent'])
        self.assertTrue(
            self.harness.charm.mon.mons_ready)

    def test_on_changed_not_ready_unit(self):
        self.harness.begin()
        # No MonReadyEvent as relation is absent
        self.assertEqual(
            self.harness.charm.seen_events,
            [])
        rel_id = self.add_dashboard_relation()
        # No MonReadyEvent as ceph-mon has not declared it is ready.
        self.assertEqual(
            self.harness.charm.seen_events,
            [])
        self.harness.update_relation_data(
            rel_id,
            'ceph-mon/0',
            {})
        self.assertEqual(
            self.harness.charm.seen_events,
            [])
        self.assertFalse(
            self.harness.charm.mon.mons_ready)
