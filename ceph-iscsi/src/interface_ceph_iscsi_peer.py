#!/usr/bin/env python3

import json
import logging
import socket

from ops.framework import (
    StoredState,
    EventBase,
    ObjectEvents,
    EventSource,
    Object)


class HasPeersEvent(EventBase):
    pass


class ReadyPeersEvent(EventBase):
    pass


class AllowedIpsChangedEvent(EventBase):
    pass


class CephISCSIGatewayPeerEvents(ObjectEvents):
    has_peers = EventSource(HasPeersEvent)
    ready_peers = EventSource(ReadyPeersEvent)
    allowed_ips_changed = EventSource(AllowedIpsChangedEvent)


class CephISCSIGatewayPeers(Object):

    on = CephISCSIGatewayPeerEvents()
    _stored = StoredState()
    PASSWORD_KEY = 'admin_password'
    READY_KEY = 'gateway_ready'
    FQDN_KEY = 'gateway_fqdn'
    ALLOWED_IPS_KEY = 'allowed_ips'

    def __init__(self, charm, relation_name):
        super().__init__(charm, relation_name)
        self.relation_name = relation_name
        self.this_unit = self.framework.model.unit
        self._stored.set_default(
            allowed_ips=[])
        self.framework.observe(
            charm.on[relation_name].relation_changed,
            self.on_changed)

    def on_changed(self, event):
        logging.info("CephISCSIGatewayPeers on_changed")
        self.on.has_peers.emit()
        if self.ready_peer_details:
            self.on.ready_peers.emit()
        if self.allowed_ips != self._stored.allowed_ips:
            self.on.allowed_ips_changed.emit()
        self._stored.allowed_ips = self.allowed_ips

    def set_admin_password(self, password):
        logging.info("Setting admin password")
        self.peer_rel.data[self.peer_rel.app][self.PASSWORD_KEY] = password

    def set_allowed_ips(self, ips, append=True):
        trusted_ips = []
        if append and self.allowed_ips:
            trusted_ips = self.allowed_ips
        trusted_ips.extend(ips)
        trusted_ips = sorted(list(set(trusted_ips)))
        ip_str = json.dumps(trusted_ips)
        logging.info("Setting allowed ips to: %s", ip_str)
        self.peer_rel.data[self.peer_rel.app][self.ALLOWED_IPS_KEY] = ip_str

    def announce_ready(self):
        logging.info("announcing ready")
        self.peer_rel.data[self.this_unit][self.READY_KEY] = 'True'
        self.peer_rel.data[self.this_unit][self.FQDN_KEY] = self.fqdn

    @property
    def ready_peer_details(self):
        peers = {
            self.framework.model.unit.name: {
                'fqdn': self.fqdn,
                'ip': self.cluster_bind_address}}
        for u in self.peer_rel.units:
            if self.peer_rel.data[u].get(self.READY_KEY) == 'True':
                peers[u.name] = {
                    'fqdn': self.peer_rel.data[u][self.FQDN_KEY],
                    'ip': self.peer_rel.data[u]['ingress-address']}
        return peers

    @property
    def fqdn(self):
        return socket.getfqdn()

    @property
    def is_joined(self):
        return self.peer_rel is not None

    @property
    def peer_rel(self):
        return self.framework.model.get_relation(self.relation_name)

    @property
    def peer_binding(self):
        return self.framework.model.get_binding(self.peer_rel)

    @property
    def cluster_bind_address(self):
        return str(self.peer_binding.network.bind_address)

    @property
    def admin_password(self):
        if not self.peer_rel:
            return None
        return self.peer_rel.data[self.peer_rel.app].get(self.PASSWORD_KEY)

    @property
    def allowed_ips(self):
        if not self.peer_rel:
            return None
        ip_str = self.peer_rel.data[self.peer_rel.app].get(
            self.ALLOWED_IPS_KEY, '[]')
        return json.loads(ip_str)

    @property
    def peer_addresses(self):
        addresses = [self.cluster_bind_address]
        for u in self.peer_rel.units:
            addresses.append(self.peer_rel.data[u]['ingress-address'])
        return sorted(addresses)

    @property
    def peer_count(self):
        if self.peer_rel:
            return len(self.peer_rel.units)
        else:
            return 0

    @property
    def unit_count(self):
        return self.peer_count + 1
