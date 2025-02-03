#!/usr/bin/env python3

import logging

from ops.framework import (
    StoredState,
    EventBase,
    ObjectEvents,
    EventSource,
    Object)


class MonReadyEvent(EventBase):
    pass


class CephDashboardEvents(ObjectEvents):
    mon_ready = EventSource(MonReadyEvent)


class CephDashboardRequires(Object):

    on = CephDashboardEvents()
    _stored = StoredState()
    READY_KEY = 'mon-ready'

    def __init__(self, charm, relation_name):
        super().__init__(charm, relation_name)
        self.relation_name = relation_name
        self.framework.observe(
            charm.on[relation_name].relation_changed,
            self.on_changed)

    @property
    def mons_ready(self) -> bool:
        """Check that all mons have reported ready."""
        ready = False
        if self.dashboard_relation:
            # There will only be one unit as this is a subordinate relation.
            for unit in self.dashboard_relation.units:
                unit_data = self.dashboard_relation.data[unit]
                if unit_data.get(self.READY_KEY) == 'True':
                    ready = True
        return ready

    def on_changed(self, event):
        """Emit mon_ready if mons are ready."""
        logging.debug("CephDashboardRequires on_changed")
        if self.mons_ready:
            self.on.mon_ready.emit()

    @property
    def dashboard_relation(self):
        return self.framework.model.get_relation(self.relation_name)
