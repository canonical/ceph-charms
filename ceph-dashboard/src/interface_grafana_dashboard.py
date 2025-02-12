#!/usr/bin/env python3

import copy
import json
import hashlib
import logging
from uuid import uuid4
from typing import List

from ops.charm import RelationChangedEvent
from ops.framework import (
    StoredState,
    EventBase,
    ObjectEvents,
    EventSource,
    Object)


class GrafanaDashboardEvent(EventBase):
    pass


class GrafanaDashboardEvents(ObjectEvents):
    dash_ready = EventSource(GrafanaDashboardEvent)


class GrafanaDashboardProvides(Object):

    on = GrafanaDashboardEvents()
    _stored = StoredState()

    def __init__(self, charm: str, relation_name: str) -> None:
        super().__init__(charm, relation_name)
        self.relation_name = relation_name
        self.framework.observe(
            charm.on[self.relation_name].relation_changed,
            self._on_relation_changed)

    def _on_relation_changed(self, event: RelationChangedEvent) -> None:
        """Handle the relation-changed event."""
        self.on.dash_ready.emit()

    def get_requests_by_name(self, name: str, relation: str) -> List[str]:
        """Get a let of requests on relation matching given name

        Check the relation data this unit has set on the given relation,
        for requests which a matching name and return them.
        """
        requests = []
        for k, v in relation.data[self.model.unit].items():
            if k.startswith('request'):
                request = json.loads(v)
                if request.get('name') == name:
                    requests.append(request)
        return requests

    def get_request_key(self, request_id: str) -> str:
        """Return the juju relation key for a given request_id"""
        return 'request_{}'.format(request_id)

    def get_request_id(self, name: str, relation: str, digest: str) -> str:
        """Return the request id for a request with given name and digest

        Look for an existing request which has a matching name and digest, if
        there is one return the request id of that request. If no matching
        request is found then generate a new request id.
        """
        logging.debug("Checking for existing request for {}".format(name))
        for request in self.get_requests_by_name(name, relation):
            if request.get('dashboard', {}).get('digest') == digest:
                logging.debug("Found existing dashboard request")
                request_id = request.get('request_id')
                break
        else:
            logging.debug("Generating new request_id")
            request_id = str(uuid4())
        return request_id

    def clear_old_requests(self, name: str, relation: str,
                           digest: str) -> None:
        """Remove requests with matching name but different digest"""
        old_requests = []
        for request in self.get_requests_by_name(name, relation):
            if request.get('dashboard', {}).get('digest') != digest:
                old_requests.append(request.get('request_id'))
        for request_id in old_requests:
            logging.debug("Actually Removing {}".format(request_id))
            rq_key = self.get_request_key(request_id)
            relation.data[self.model.unit][rq_key] = ''

    def register_dashboard(self, name: str, dashboard: str):
        """
        Request a dashboard to be imported.

        :param name: Name of dashboard. Informational only, so that you can
            tell which dashboard request this was, e.g. to check for success or
            failure.
        :param dashboard: Data structure defining the dashboard. Must be JSON
            serializable.  (Note: This should *not* be pre-serialized JSON.)
        """

        _dashboard = copy.deepcopy(dashboard)
        # In this interface the request id for a job name is preserved.
        if self.dashboard_relation:
            digest = hashlib.md5(
                json.dumps(_dashboard).encode("utf8")).hexdigest()
            _dashboard["digest"] = digest
            _dashboard["source_model"] = self.model.name
            request_id = self.get_request_id(name, self.dashboard_relation,
                                             _dashboard.get('digest'))
            rq_key = self.get_request_key(request_id)
            self.dashboard_relation.data[self.model.unit][rq_key] = json.dumps(
                {
                    'request_id': request_id,
                    'name': name,
                    'dashboard': _dashboard,
                },
                sort_keys=True)
            self.clear_old_requests(
                name,
                self.dashboard_relation,
                _dashboard.get('digest'))

    @property
    def dashboard_relation(self):
        return self.model.get_relation(self.relation_name)
