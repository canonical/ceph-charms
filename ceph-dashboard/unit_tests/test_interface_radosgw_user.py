#!/usr/bin/env python3

# Copyright 2021 Canonical Ltd.
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
from ops.charm import CharmBase
import interface_radosgw_user


class TestRadosGWUserRequires(unittest.TestCase):

    class MyCharm(CharmBase):

        def __init__(self, *args):
            super().__init__(*args)
            self.seen_events = []
            self.radosgw_user = interface_radosgw_user.RadosGWUserRequires(
                self,
                'radosgw-dashboard')

            self.framework.observe(
                self.radosgw_user.on.gw_user_ready,
                self._log_event)

        def _log_event(self, event):
            self.seen_events.append(type(event).__name__)

    def setUp(self):
        super().setUp()
        self.harness = Harness(
            self.MyCharm,
            meta='''
name: my-charm
requires:
  radosgw-dashboard:
    interface: radosgw-user
'''
        )

    def test_init(self):
        self.harness.begin()
        self.assertEqual(
            self.harness.charm.radosgw_user.relation_name,
            'radosgw-dashboard')

    def test_add_radosgw_dashboard_relation(self):
        rel_id1 = self.harness.add_relation('radosgw-dashboard', 'ceph-eu')
        rel_id2 = self.harness.add_relation('radosgw-dashboard', 'ceph-us')
        self.harness.begin()
        self.assertEqual(
            self.harness.charm.seen_events,
            [])
        self.harness.set_leader()
        self.harness.add_relation_unit(
            rel_id1,
            'ceph-eu/0')
        self.harness.add_relation_unit(
            rel_id1,
            'ceph-eu/1')
        self.harness.add_relation_unit(
            rel_id2,
            'ceph-us/0')
        self.harness.add_relation_unit(
            rel_id2,
            'ceph-us/1')
        self.harness.update_relation_data(
            rel_id1,
            'ceph-eu/0',
            {
                'daemon-id': 'juju-80416c-zaza-7af97ef8a776-3'})
        self.harness.update_relation_data(
            rel_id1,
            'ceph-eu/1',
            {
                'daemon-id': 'juju-80416c-zaza-7af97ef8a776-4'})
        self.harness.update_relation_data(
            rel_id2,
            'ceph-us/0',
            {
                'daemon-id': 'juju-dddddd-zaza-sdfsfsfs-4'})
        self.harness.update_relation_data(
            rel_id2,
            'ceph-us/1',
            {
                'daemon-id': 'juju-dddddd-zaza-sdfsfsfs-5'})
        self.harness.update_relation_data(
            rel_id1,
            'ceph-eu',
            {
                'access-key': 'XNUZVPL364U0BL1OXWJZ',
                'secret-key': 'SgBo115xJcW90nkQ5EaNQ6fPeyeUUT0GxhwQbLFo',
                'uid': 'radosgw-user-9'})
        self.assertEqual(
            self.harness.charm.seen_events,
            ['RadosGWUserEvent'])
        self.harness.update_relation_data(
            rel_id2,
            'ceph-us',
            {
                'access-key': 'JGHKJGDKJGJGJHGYYYYM',
                'secret-key': 'iljkdfhHKHKd88LKxNLSKDiijfjfjfldjfjlf44',
                'uid': 'radosgw-user-10'})
        self.assertEqual(
            self.harness.charm.radosgw_user.get_user_creds(),
            [
                {
                    'access_key': 'XNUZVPL364U0BL1OXWJZ',
                    'daemon_id': 'juju-80416c-zaza-7af97ef8a776-3',
                    'secret_key': 'SgBo115xJcW90nkQ5EaNQ6fPeyeUUT0GxhwQbLFo',
                    'uid': 'radosgw-user-9'},
                {
                    'access_key': 'XNUZVPL364U0BL1OXWJZ',
                    'daemon_id': 'juju-80416c-zaza-7af97ef8a776-4',
                    'secret_key': 'SgBo115xJcW90nkQ5EaNQ6fPeyeUUT0GxhwQbLFo',
                    'uid': 'radosgw-user-9'},
                {
                    'access_key': 'JGHKJGDKJGJGJHGYYYYM',
                    'daemon_id': 'juju-dddddd-zaza-sdfsfsfs-4',
                    'secret_key': 'iljkdfhHKHKd88LKxNLSKDiijfjfjfldjfjlf44',
                    'uid': 'radosgw-user-10'},
                {
                    'access_key': 'JGHKJGDKJGJGJHGYYYYM',
                    'daemon_id': 'juju-dddddd-zaza-sdfsfsfs-5',
                    'secret_key': 'iljkdfhHKHKd88LKxNLSKDiijfjfjfldjfjlf44',
                    'uid': 'radosgw-user-10'}])

    def test_add_radosgw_dashboard_relation_missing_data(self):
        rel_id1 = self.harness.add_relation('radosgw-dashboard', 'ceph-eu')
        self.harness.begin()
        self.assertEqual(
            self.harness.charm.seen_events,
            [])
        self.harness.set_leader()
        self.harness.add_relation_unit(
            rel_id1,
            'ceph-eu/0')
        self.harness.update_relation_data(
            rel_id1,
            'ceph-eu/0',
            {
                'daemon-id': 'juju-80416c-zaza-7af97ef8a776-3'})
        self.harness.update_relation_data(
            rel_id1,
            'ceph-eu',
            {
                'secret-key': 'SgBo115xJcW90nkQ5EaNQ6fPeyeUUT0GxhwQbLFo',
                'uid': 'radosgw-user-9'})
        self.assertEqual(
            self.harness.charm.radosgw_user.get_user_creds(),
            [])
        self.assertEqual(
            self.harness.charm.seen_events,
            [])
