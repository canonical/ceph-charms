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

import hashlib
import json
import unittest
import sys
sys.path.append('lib')  # noqa
sys.path.append('src')  # noqa
from ops.testing import Harness
from ops.charm import CharmBase
import interface_grafana_dashboard


class TestGrafanaDashboardProvides(unittest.TestCase):

    class MyCharm(CharmBase):

        def __init__(self, *args):
            super().__init__(*args)
            self.seen_events = []
            self.grafana_dashboard = \
                interface_grafana_dashboard.GrafanaDashboardProvides(
                    self,
                    'grafana-dashboard')
            self.seen_events = []

            self.framework.observe(
                self.grafana_dashboard.on.dash_ready,
                self._log_event)

        def _log_event(self, event):
            self.seen_events.append(type(event).__name__)

    def setUp(self):
        super().setUp()
        self.harness = Harness(
            self.MyCharm,
            meta='''
name: my-charm
provides:
  grafana-dashboard:
    interface: grafana-dashboard
'''
        )

    def add_grafana_dashboard_relation(self):
        rel_id = self.harness.add_relation(
            'grafana-dashboard',
            'grafana')
        self.harness.add_relation_unit(
            rel_id,
            'grafana/0')
        self.harness.update_relation_data(
            rel_id,
            'grafana/0',
            {'ingress-address': '10.0.0.3'})
        return rel_id

    def test_init(self):
        self.harness.begin()
        self.assertEqual(
            self.harness.charm.grafana_dashboard.relation_name,
            'grafana-dashboard')

    def test_on_changed(self):
        self.harness.begin()
        # No GrafanaDashboardEvent as relation is absent
        self.assertEqual(
            self.harness.charm.seen_events,
            [])
        self.add_grafana_dashboard_relation()
        self.assertEqual(
            self.harness.charm.seen_events,
            ['GrafanaDashboardEvent'])

    def get_requests_on_relation(self, rel_data):
        requests = {k: v for k, v in rel_data.items()
                    if k.startswith('request')}
        return requests

    def test_register_dashboard(self):
        self.harness.begin()
        rel_id = self.add_grafana_dashboard_relation()
        dashboard = {
            'uid': '123',
            'foo': 'ba1'}
        digest = hashlib.md5(json.dumps(dashboard).encode("utf8")).hexdigest()
        self.harness.charm.grafana_dashboard.register_dashboard(
            'my-dash.json',
            dashboard)
        rel_data = self.harness.get_relation_data(
            rel_id,
            'my-charm/0')
        requests = self.get_requests_on_relation(rel_data)
        self.assertEqual(
            len(requests),
            1)
        key = list(requests.keys())[0]
        expect = {
            "dashboard": {
                "digest": digest,
                "foo": "ba1",
                "source_model": None,  # Model name appears as None in testing
                                       # harness
                "uid": "123"},
            "name": "my-dash.json",
            "request_id": key.replace("request_", "")}
        self.assertEqual(
            requests[key],
            json.dumps(expect))
        # Register the same dashboard again
        self.harness.charm.grafana_dashboard.register_dashboard(
            'my-dash.json',
            dashboard)
        # Check the relation data is unchanged
        requests = self.get_requests_on_relation(rel_data)
        self.assertEqual(
            len(requests),
            1)
        new_key = list(requests.keys())[0]
        # A duplicate was registered so the key should be unchanged.
        self.assertEqual(
            new_key,
            key)
        expect = {
            "dashboard": {
                "digest": digest,
                "foo": "ba1",
                "source_model": None,  # Model name appears as None in testing
                                       # harness
                "uid": "123"},
            "name": "my-dash.json",
            "request_id": new_key.replace("request_", "")}
        self.assertEqual(
            requests[new_key],
            json.dumps(expect))
        # Update an existing dashboard with a new version. This should create
        # a new request and remove the old one.
        updated_dashboard = {
            'uid': '123',
            'foo': 'ba2'}
        updated_digest = hashlib.md5(
            json.dumps(updated_dashboard).encode("utf8")).hexdigest()
        self.harness.charm.grafana_dashboard.register_dashboard(
            'my-dash.json',
            updated_dashboard)
        rel_data = self.harness.get_relation_data(
            rel_id,
            'my-charm/0')
        requests = self.get_requests_on_relation(rel_data)
        # The old request should have been removed so there is still just one
        # key.
        self.assertEqual(
            len(requests),
            1)
        updated_key = list(requests.keys())[0]
        expect = {
            "dashboard": {
                "digest": updated_digest,
                "foo": "ba2",
                "source_model": None,  # Model name appears as None in testing
                                       # harness
                "uid": "123"},
            "name": "my-dash.json",
            "request_id": updated_key.replace("request_", "")}
        self.assertEqual(
            requests[updated_key],
            json.dumps(expect))
