# Copyright 2026 Canonical Ltd
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

import unittest

from unittest.mock import patch

import charmhelpers.contrib.openstack.context as os_context
import charmhelpers.contrib.openstack.ip as os_ip


class ResolveAddressesTests(unittest.TestCase):

    @patch('charmhelpers.contrib.openstack.ip.is_address_in_network')
    @patch('charmhelpers.contrib.openstack.ip.config')
    @patch('charmhelpers.contrib.openstack.ip.is_clustered')
    def test_resolve_addresses_returns_all_matching_vips(
            self, is_clustered, config, is_address_in_network):
        is_clustered.return_value = True

        def _config_side_effect(key):
            if key == 'vip':
                return '10.0.0.10 10.0.0.11 10.1.0.10'
            if key == 'os-public-network':
                return '10.0.0.0/24'
            if key == 'os-public-hostname':
                return None
            return None

        config.side_effect = _config_side_effect
        is_address_in_network.side_effect = (
            lambda network, address: address.startswith('10.0.0.')
        )

        addresses = os_ip.resolve_addresses(endpoint_type=os_ip.PUBLIC,
                                            override=True)

        self.assertEqual(['10.0.0.10', '10.0.0.11'], addresses)

    @patch('charmhelpers.contrib.openstack.ip.resolve_address')
    @patch('charmhelpers.contrib.openstack.ip.is_address_in_network')
    @patch('charmhelpers.contrib.openstack.ip.config')
    @patch('charmhelpers.contrib.openstack.ip.is_clustered')
    def test_resolve_addresses_falls_back_to_resolve_address(
            self, is_clustered, config,
            is_address_in_network, resolve_address):
        is_clustered.return_value = True

        def _config_side_effect(key):
            if key == 'vip':
                return '10.0.0.10 10.0.0.11'
            if key == 'os-public-network':
                return '172.16.0.0/24'
            if key == 'os-public-hostname':
                return None
            return None

        config.side_effect = _config_side_effect
        is_address_in_network.return_value = False
        resolve_address.return_value = '192.168.122.10'

        addresses = os_ip.resolve_addresses(endpoint_type=os_ip.PUBLIC,
                                            override=True)

        self.assertEqual(['192.168.122.10'], addresses)


class ApacheSSLContextAddressTests(unittest.TestCase):

    @patch('charmhelpers.contrib.openstack.context.log')
    @patch('charmhelpers.contrib.openstack.context.resolve_addresses')
    @patch('charmhelpers.contrib.openstack.context.resolve_address')
    @patch('charmhelpers.contrib.openstack.context'
           '.network_get_primary_address')
    @patch('charmhelpers.contrib.openstack.context.local_address')
    @patch('charmhelpers.contrib.openstack.context.config')
    def test_get_network_addresses_includes_all_resolved_addresses(
            self, config, local_address, network_get_primary_address,
            resolve_address, resolve_addresses, _log):
        ctxt = os_context.ApacheSSLContext()

        def _config_side_effect(key):
            if key in ('os-internal-network', 'os-admin-network',
                       'os-public-network'):
                return None
            return None

        config.side_effect = _config_side_effect
        local_address.return_value = '192.168.122.10'
        network_get_primary_address.side_effect = (
            lambda binding: {
                'internal': '192.168.10.10',
                'admin': '192.168.20.10',
                'public': '192.168.30.10',
            }[binding]
        )
        resolve_address.side_effect = (
            lambda endpoint_type: {
                os_ip.INTERNAL: 'int.example.com',
                os_ip.ADMIN: 'admin.example.com',
                os_ip.PUBLIC: 'public.example.com',
            }[endpoint_type]
        )
        resolve_addresses.side_effect = (
            lambda endpoint_type: {
                os_ip.INTERNAL: ['10.0.1.10'],
                os_ip.ADMIN: ['10.0.2.10', '10.0.2.10', '10.0.2.11'],
                os_ip.PUBLIC: ['10.0.3.10'],
            }[endpoint_type]
        )

        addresses = ctxt.get_network_addresses()

        self.assertEqual([
            ('192.168.10.10', '10.0.1.10'),
            ('192.168.20.10', '10.0.2.10'),
            ('192.168.20.10', '10.0.2.11'),
            ('192.168.30.10', '10.0.3.10'),
        ], addresses)

    @patch('charmhelpers.contrib.openstack.context.log')
    @patch('charmhelpers.contrib.openstack.context.is_clustered')
    @patch('charmhelpers.contrib.openstack.context.resolve_addresses')
    @patch('charmhelpers.contrib.openstack.context.resolve_address')
    @patch('charmhelpers.contrib.openstack.context'
           '.network_get_primary_address')
    @patch('charmhelpers.contrib.openstack.context.get_address_in_network')
    @patch('charmhelpers.contrib.openstack.context.local_address')
    @patch('charmhelpers.contrib.openstack.context.config')
    def test_get_network_addresses_replaces_vip_bind_address(
            self, config, local_address, get_address_in_network,
            network_get_primary_address, resolve_address,
            resolve_addresses, is_clustered, _log):
        """If get_address_in_network returns a VIP, fall back to binding."""
        ctxt = os_context.ApacheSSLContext()
        is_clustered.return_value = True

        def _config_side_effect(key):
            if key == 'vip':
                return '10.1.33.1 10.1.41.1 192.168.99.51'
            if key == 'os-internal-network':
                return '10.1.32.0/21'
            if key == 'os-admin-network':
                return '10.1.40.0/21'
            if key == 'os-public-network':
                return '192.168.96.0/21'
            return None

        config.side_effect = _config_side_effect
        local_address.return_value = '10.1.32.111'

        # Simulate get_address_in_network returning VIPs (as can happen
        # when Pacemaker assigns them as secondary addresses).
        def _get_addr_in_network(net, fallback):
            return {
                '10.1.32.0/21': '10.1.33.1',   # VIP
                '10.1.40.0/21': '10.1.41.1',   # VIP
                '192.168.96.0/21': '192.168.99.206',  # local
            }.get(net, fallback)

        get_address_in_network.side_effect = _get_addr_in_network

        # network_get_primary_address returns the correct local IPs
        network_get_primary_address.side_effect = (
            lambda binding: {
                'internal': '10.1.32.111',
                'admin': '10.1.40.116',
                'public': '192.168.99.206',
            }[binding]
        )
        resolve_addresses.side_effect = (
            lambda endpoint_type: {
                os_ip.INTERNAL: ['10.1.33.1'],
                os_ip.ADMIN: ['10.1.41.1'],
                os_ip.PUBLIC: ['192.168.99.51'],
            }[endpoint_type]
        )

        addresses = ctxt.get_network_addresses()

        # VIPs must NOT appear as bind addresses
        for addr, _endpoint in addresses:
            self.assertNotIn(addr, {'10.1.33.1', '10.1.41.1',
                                    '192.168.99.51'})
        # All 3 VIPs must appear as endpoints
        endpoints = [ep for _, ep in addresses]
        self.assertIn('10.1.33.1', endpoints)
        self.assertIn('10.1.41.1', endpoints)
        self.assertIn('192.168.99.51', endpoints)

    @patch('charmhelpers.contrib.openstack.context.log')
    @patch('charmhelpers.contrib.openstack.context.is_clustered')
    @patch('charmhelpers.contrib.openstack.context.resolve_network_cidr')
    @patch('charmhelpers.contrib.openstack.context.is_address_in_network')
    @patch('charmhelpers.contrib.openstack.context.resolve_addresses')
    @patch('charmhelpers.contrib.openstack.context.resolve_address')
    @patch('charmhelpers.contrib.openstack.context'
           '.network_get_primary_address')
    @patch('charmhelpers.contrib.openstack.context.get_address_in_network')
    @patch('charmhelpers.contrib.openstack.context.local_address')
    @patch('charmhelpers.contrib.openstack.context.config')
    def test_get_network_addresses_covers_uncovered_vips(
            self, config, local_address, get_address_in_network,
            network_get_primary_address,
            resolve_address, resolve_addresses, is_address_in_network,
            resolve_network_cidr, is_clustered, _log):
        """VIPs not matched by any endpoint type must still get entries."""
        ctxt = os_context.ApacheSSLContext()
        is_clustered.return_value = True

        def _config_side_effect(key):
            if key == 'vip':
                return '10.1.33.1 10.1.41.1 192.168.99.51'
            if key == 'os-internal-network':
                return '10.1.32.0/21'
            if key == 'os-admin-network':
                return '10.1.40.0/21'
            # PUBLIC not configured, falls back to binding which
            # resolves to same network as INTERNAL.
            if key == 'os-public-network':
                return None
            return None

        config.side_effect = _config_side_effect
        local_address.return_value = '10.1.32.111'

        # get_address_in_network simulates Pacemaker VIPs on eth2/eth3
        # and the correct local address on eth0's network.
        def _get_address_in_network(net, fallback):
            return {
                '10.1.32.0/21': '10.1.33.1',       # VIP (secondary)
                '10.1.40.0/21': '10.1.41.1',       # VIP (secondary)
                '192.168.96.0/21': '192.168.99.206',  # local primary
            }.get(net, fallback)

        get_address_in_network.side_effect = _get_address_in_network

        # Public binding returns same address as internal (LP#2161718)
        network_get_primary_address.side_effect = (
            lambda binding: {
                'internal': '10.1.32.111',
                'admin': '10.1.40.116',
                'public': '10.1.32.111',  # same as internal
            }[binding]
        )
        # resolve_addresses for PUBLIC returns same VIP as INTERNAL
        # since the binding is in the same network
        resolve_addresses.side_effect = (
            lambda endpoint_type: {
                os_ip.INTERNAL: ['10.1.33.1'],
                os_ip.ADMIN: ['10.1.41.1'],
                os_ip.PUBLIC: ['10.1.33.1'],  # same as INTERNAL
            }[endpoint_type]
        )

        # is_address_in_network for the reconciliation phase
        def _is_address_in_network(network, address):
            import ipaddress
            return ipaddress.ip_address(address) in \
                ipaddress.ip_network(network)

        is_address_in_network.side_effect = _is_address_in_network

        # resolve_network_cidr maps all relevant addresses including VIP
        resolve_network_cidr.side_effect = (
            lambda addr: {
                '10.1.32.111': '10.1.32.0/21',
                '10.1.40.116': '10.1.40.0/21',
                '192.168.99.51': '192.168.96.0/21',
            }[addr]
        )

        addresses = ctxt.get_network_addresses()

        # The uncovered VIP 192.168.99.51 must appear as an endpoint
        # bound to the LOCAL address on that network (not the VIP)
        endpoints = [ep for _, ep in addresses]
        self.assertIn('192.168.99.51', endpoints)
        self.assertIn('10.1.33.1', endpoints)
        self.assertIn('10.1.41.1', endpoints)
        # Verify the bind address for the uncovered VIP is the local IP
        vip_tuple = [(a, e) for a, e in addresses if e == '192.168.99.51']
        self.assertEqual(vip_tuple, [('192.168.99.206', '192.168.99.51')])


if __name__ == '__main__':
    unittest.main()
