#!/usr/bin/env python3

# import json
import logging
import os
# import socket
import uuid

from ops.framework import (
    StoredState,
    EventBase,
    ObjectEvents,
    EventSource,
    Object)


class PoolInitialisedEvent(EventBase):
    pass


class ReloadNonceEvent(EventBase):
    pass


class DepartedEvent(EventBase):
    pass


class CephNFSPeerEvents(ObjectEvents):
    pool_initialised = EventSource(PoolInitialisedEvent)
    reload_nonce = EventSource(ReloadNonceEvent)
    departing = EventSource(DepartedEvent)


class CephNFSPeers(Object):

    on = CephNFSPeerEvents()
    _stored = StoredState()

    def __init__(self, charm, relation_name):
        super().__init__(charm, relation_name)
        self.relation_name = relation_name
        self.this_unit = self.framework.model.unit
        self._stored.set_default(
            pool_initialised=False,
            reload_nonce=None)
        self.framework.observe(
            charm.on[relation_name].relation_changed,
            self.on_changed)
        self.framework.observe(
            charm.on[relation_name].relation_departed,
            self.on_departed)

    def on_changed(self, event):
        logging.info("CephNFSPeers on_changed")
        logging.debug('pool_initialised: {}'.format(self.pool_initialised))
        if self.pool_initialised == 'True' and  \
           not self._stored.pool_initialised:
            logging.info("emiting pool initialised")
            self.on.pool_initialised.emit()
            self._stored.pool_initialised = True
        logging.debug('reload_nonce: {}'.format(self.reload_nonce))
        if self._stored.reload_nonce != self.reload_nonce:
            logging.info("emiting reload nonce")
            self.on.reload_nonce.emit()
        self._stored.reload_nonce = self.reload_nonce

    def on_departed(self, event):
        logging.warning("CephNFSPeers on_departed")
        if self.this_unit.name == os.getenv('JUJU_DEPARTING_UNIT'):
            self.on.departing.emit()

    def initialised_pool(self):
        logging.info("Setting pool initialised")
        self.peer_rel.data[self.peer_rel.app]['pool_initialised'] = 'True'
        self.on.pool_initialised.emit()

    def trigger_reload(self):
        self.peer_rel.data[
            self.peer_rel.app
        ]['reload_nonce'] = str(uuid.uuid4())
        self.on.reload_nonce.emit()

    @property
    def pool_initialised(self):
        return self.peer_rel.data[self.peer_rel.app].get('pool_initialised')

    @property
    def reload_nonce(self):
        return self.peer_rel.data[self.peer_rel.app].get('reload_nonce')

    @property
    def peer_rel(self):
        return self.framework.model.get_relation(self.relation_name)
