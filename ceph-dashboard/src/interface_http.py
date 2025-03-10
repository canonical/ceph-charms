#!/usr/bin/env python3

import logging
from typing import Dict, Union

from ops.charm import RelationChangedEvent
from ops.model import Relation

from ops.framework import (
    StoredState,
    EventBase,
    ObjectEvents,
    EventSource,
    Object)


class HTTPEvent(EventBase):
    pass


class HTTPEvents(ObjectEvents):
    http_ready = EventSource(HTTPEvent)


class HTTPRequires(Object):

    on = HTTPEvents()
    _stored = StoredState()
    required_keys = {'hostname', 'port'}

    def __init__(self, charm: str, relation_name: str) -> None:
        super().__init__(charm, relation_name)
        self.relation_name = relation_name
        self.framework.observe(
            charm.on[relation_name].relation_changed,
            self.on_changed)

    def on_changed(self, event: RelationChangedEvent) -> None:
        """Handle the relation-changed event

        When the relation changes check the relation data from the remote
        units to see if all the keys needed are present."""
        logging.debug("http on_changed")
        if self.http_relation:
            for u in self.http_relation.units:
                rel_data = self.http_relation.data[u]
                if self.required_keys.issubset(set(rel_data.keys())):
                    self.on.http_ready.emit()

    def get_service_ep_data(self) -> Union[Dict[str, str], None]:
        """Return endpoint data for accessing the remote service.

        Return endpoint data for accessing the remote service. If the relation
        or required keys are missing then return None"""
        logging.debug("http on_changed")
        if self.http_relation:
            for u in self.http_relation.units:
                rel_data = self.http_relation.data[u]
                if self.required_keys.issubset(
                        set(self.http_relation.data[u].keys())):
                    return {'hostname': rel_data['hostname'],
                            'port': rel_data['port']}

    @property
    def http_relation(self) -> Union[Relation, None]:
        """The relation matching self.relation_name if it exists"""
        return self.model.get_relation(self.relation_name)
