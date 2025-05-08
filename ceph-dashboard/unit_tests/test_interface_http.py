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
import interface_http


class MyCharm(CharmBase):

    def __init__(self, *args):
        super().__init__(*args)
        self.framework.meta = CharmMeta.from_yaml(metadata='''
name: my-charm
requires:
  prometheus:
    interface: http
''')

        self.seen_events = []
        self.prometheus = interface_http.HTTPRequires(
            self,
            'prometheus')
        self.framework.observe(
            self.prometheus.on.http_ready,
            self._log_event)

    def _log_event(self, event):
        self.seen_events.append(type(event).__name__)


class TestHTTPRequires(unittest.TestCase):

    def setUp(self):
        super().setUp()
        self.harness = Harness(
            MyCharm,
        )

    def add_http_relation(self):
        rel_id = self.harness.add_relation('prometheus', 'prometheus')
        self.harness.add_relation_unit(
            rel_id,
            'prometheus/0')
        return rel_id

    def test_relation_name(self):
        self.harness.begin()
        self.assertEqual(
            self.harness.charm.prometheus.relation_name,
            'prometheus')

    def test_http_ready_event(self):
        self.harness.begin()
        rel_id = self.add_http_relation()
        self.assertEqual(
            self.harness.charm.seen_events,
            [])
        self.harness.update_relation_data(
            rel_id,
            'prometheus/0',
            {
                'hostname': 'promhost',
                'port': 3000})
        self.assertEqual(
            self.harness.charm.seen_events,
            ['HTTPEvent'])

    def test_get_service_ep_data(self):
        self.harness.begin()
        rel_id = self.add_http_relation()
        self.harness.update_relation_data(
            rel_id,
            'prometheus/0',
            {
                'hostname': 'promhost',
                'port': 3000})
        self.assertEqual(
            self.harness.charm.prometheus.get_service_ep_data(),
            {'hostname': 'promhost', 'port': 3000})
