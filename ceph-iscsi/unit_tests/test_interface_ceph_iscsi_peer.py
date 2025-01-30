#!/usr/bin/env python3

import unittest
import sys

sys.path.append('lib')  # noqa
sys.path.append('src')  # noqa

import interface_ceph_iscsi_peer

from unittest import mock
from mock import PropertyMock

from ops import framework
from ops.testing import Harness
from ops.charm import CharmBase

from interface_ceph_iscsi_peer import CephISCSIGatewayPeers, ReadyPeersEvent


class TestCephISCSIGatewayPeers(unittest.TestCase):

    def setUp(self):
        self.harness = Harness(CharmBase, meta='''
            name: ceph-iscsi
            peers:
              cluster:
                interface: ceph-iscsi-peer
        ''')

    @mock.patch.object(CephISCSIGatewayPeers, 'cluster_bind_address',
                       new_callable=PropertyMock)
    @mock.patch('socket.getfqdn')
    def test_on_changed(self, _getfqdn, _cluster_bind_address):
        our_fqdn = 'ceph-iscsi-0.example'
        _getfqdn.return_value = our_fqdn
        # TODO: Replace this with calls to the test harness once
        # https://github.com/canonical/operator/issues/222 is fixed.
        _cluster_bind_address.return_value = '192.0.2.1'

        class TestReceiver(framework.Object):

            def __init__(self, parent, key):
                super().__init__(parent, key)
                self.observed_events = []

            def on_ready_peers(self, event):
                self.observed_events.append(event)

        self.harness.begin()
        self.peers = CephISCSIGatewayPeers(self.harness.charm, 'cluster')

        receiver = TestReceiver(self.harness.framework, 'receiver')
        self.harness.framework.observe(self.peers.on.ready_peers,
                                       receiver.on_ready_peers)
        relation_id = self.harness.add_relation('cluster', 'ceph-iscsi')
        self.harness.add_relation_unit(
            relation_id,
            'ceph-iscsi/1')
        self.harness.update_relation_data(
            relation_id,
            'ceph-iscsi/1',
            {
                'ingress-address': '192.0.2.2',
                'gateway_ready': 'True',
                'gateway_fqdn': 'ceph-iscsi-1.example'
            })
        self.assertEqual(len(receiver.observed_events), 1)
        self.assertIsInstance(receiver.observed_events[0],
                              ReadyPeersEvent)

    def test_set_admin_password(self):
        self.harness.set_leader()
        self.harness.begin()
        self.peers = CephISCSIGatewayPeers(self.harness.charm, 'cluster')
        self.harness.add_relation('cluster', 'ceph-iscsi')

        self.peers.set_admin_password('s3cr3t')
        rel_data = self.harness.charm.model.get_relation('cluster').data
        our_app = self.harness.charm.app
        self.assertEqual(rel_data[our_app]['admin_password'], 's3cr3t')

    @mock.patch('socket.getfqdn')
    def test_announce_ready(self, _getfqdn):
        our_fqdn = 'ceph-iscsi-0.example'
        _getfqdn.return_value = our_fqdn
        self.harness.begin()
        self.peers = CephISCSIGatewayPeers(self.harness.charm, 'cluster')
        self.harness.add_relation('cluster', 'ceph-iscsi')

        self.peers.announce_ready()
        rel_data = self.harness.charm.model.get_relation('cluster').data
        our_unit = self.harness.charm.unit
        self.assertEqual(rel_data[our_unit]['gateway_fqdn'], our_fqdn)
        self.assertEqual(rel_data[our_unit]['gateway_ready'], 'True')

    @mock.patch.object(CephISCSIGatewayPeers, 'cluster_bind_address',
                       new_callable=PropertyMock)
    @mock.patch('socket.getfqdn')
    def test_ready_peer_details(self, _getfqdn, _cluster_bind_address):
        _getfqdn.return_value = 'ceph-iscsi-0.example'
        # TODO: Replace this with calls to the test harness once
        # https://github.com/canonical/operator/issues/222 is fixed.
        _cluster_bind_address.return_value = '192.0.2.1'

        self.harness.begin()
        self.peers = CephISCSIGatewayPeers(self.harness.charm, 'cluster')
        relation_id = self.harness.add_relation('cluster', 'ceph-iscsi')

        self.harness.add_relation_unit(
            relation_id,
            'ceph-iscsi/1')
        self.harness.update_relation_data(
            relation_id,
            'ceph-iscsi/1',
            {
                'ingress-address': '192.0.2.2',
                'gateway_ready': 'True',
                'gateway_fqdn': 'ceph-iscsi-1.example'
            })
        self.harness.add_relation_unit(
            relation_id,
            'ceph-iscsi/2')
        self.harness.update_relation_data(
            relation_id,
            'ceph-iscsi/2',
            {
                'ingress-address': '192.0.2.3',
                'gateway_ready': 'True',
                'gateway_fqdn': 'ceph-iscsi-2.example',
            })
        self.harness.add_relation_unit(
            relation_id,
            'ceph-iscsi/3')
        self.harness.update_relation_data(
            relation_id,
            'ceph-iscsi/3',
            {'ingress-address': '192.0.2.4'})

        self.peers.ready_peer_details

    @mock.patch.object(interface_ceph_iscsi_peer.CephISCSIGatewayPeers,
                       'cluster_bind_address', new_callable=PropertyMock)
    def test_ready_peer_addresses(self, _cluster_bind_address):
        # TODO: Replace this with calls to the test harness once
        # https://github.com/canonical/operator/issues/222 is fixed.
        _cluster_bind_address.return_value = '192.0.2.1'

        self.harness.begin()
        self.peers = CephISCSIGatewayPeers(self.harness.charm, 'cluster')
        relation_id = self.harness.add_relation('cluster', 'ceph-iscsi')

        self.harness.add_relation_unit(
            relation_id,
            'ceph-iscsi/1')
        self.harness.update_relation_data(
            relation_id,
            'ceph-iscsi/1',
            {
                'ingress-address': '192.0.2.2',
                'gateway_ready': 'True',
                'gateway_fqdn': 'ceph-iscsi-1.example'
            })
        self.harness.add_relation_unit(
            relation_id,
            'ceph-iscsi/2')
        self.harness.update_relation_data(
            relation_id,
            'ceph-iscsi/2',
            {
                'ingress-address': '192.0.2.3',
                'gateway_ready': 'True',
                'gateway_fqdn': 'ceph-iscsi-2.example',
            })
        self.assertEqual(['192.0.2.1', '192.0.2.2', '192.0.2.3'],
                         self.peers.peer_addresses)


if __name__ == '__main__':
    unittest.main()
