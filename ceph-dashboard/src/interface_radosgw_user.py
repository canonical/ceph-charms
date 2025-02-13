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

import json

from ops.framework import (
    StoredState,
    EventBase,
    ObjectEvents,
    EventSource,
    Object)


class RadosGWUserEvent(EventBase):
    pass


class RadosGWUserEvents(ObjectEvents):
    gw_user_ready = EventSource(RadosGWUserEvent)


class RadosGWUserRequires(Object):

    on = RadosGWUserEvents()
    _stored = StoredState()

    def __init__(self, charm, relation_name, request_system_role=False):
        super().__init__(charm, relation_name)
        self.relation_name = relation_name
        self.request_system_role = request_system_role
        self.framework.observe(
            charm.on[self.relation_name].relation_joined,
            self.request_user)
        self.framework.observe(
            charm.on[self.relation_name].relation_changed,
            self._on_relation_changed)

    def request_user(self, event):
        if self.model.unit.is_leader():
            for relation in self.framework.model.relations[self.relation_name]:
                relation.data[self.model.app]['system-role'] = json.dumps(
                    self.request_system_role)

    def get_user_creds(self):
        creds = []
        for relation in self.framework.model.relations[self.relation_name]:
            app_data = relation.data[relation.app]
            for unit in relation.units:
                unit_data = relation.data[unit]
                cred_data = {
                    'access_key': app_data.get('access-key'),
                    'secret_key': app_data.get('secret-key'),
                    'uid': app_data.get('uid'),
                    'daemon_id': unit_data.get('daemon-id')}
                if all(cred_data.values()):
                    creds.append(cred_data)
        creds = sorted(creds, key=lambda k: k['daemon_id'])
        return creds

    def _on_relation_changed(self, event):
        """Handle the relation-changed event."""
        if self.get_user_creds():
            self.on.gw_user_ready.emit()
