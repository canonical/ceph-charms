#! /usr/bin/env python3
#
# Copyright 2024 Canonical Ltd
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#  http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import json
import unittest
import unittest.mock as mock

import ops
import ops.testing

import src.charm as charm


class MockSocket:
    def __init__(self, skip_first=False, cached_resp={}):
        self.sendto = mock.MagicMock()
        self.sendto.side_effect = self._sendto
        self.recv = mock.MagicMock()
        self.recv.side_effect = self._recv
        self.response = None
        self.skip_first = skip_first
        self.cached_resp = cached_resp

    def _sendto(self, msg, *args):
        self.response = self._compute_response(msg)

    def _recv(self, *args):
        ret = self.response
        self.response = None
        return ret

    def close(self):
        pass

    def _compute_response(self, msg):
        msg = json.loads(msg)
        method = msg['method']
        resp = self.cached_resp.get(method)
        if resp is not None:
            return resp
        elif method not in ('create', 'list', 'find', 'host_add', 'host_del'):
            return b'{}'

        ret = {'nqn': 'nqn.1', 'addr': '3.3.3.3', 'port': 1,
               'pool': 'mypool', 'image': 'myimage', 'cluster': 'ceph.1'}
        if method == 'list':
            ret = [ret]
        elif (method in ('find', 'host_add', 'host_del') and
                msg['params']['nqn'] != 'nqn.1' or self.skip_first):
            ret = {}
            if method in ('host_add', 'host_del'):
                ret['error'] = ""

        self.skip_first = False
        return json.dumps(ret).encode('utf8')


class TestCharm(unittest.TestCase):
    def setUp(self):
        self.harness = ops.testing.Harness(charm.CephNVMECharm)
        self.addCleanup(self.harness.cleanup)

    def add_peers(self):
        rel_id = self.harness.add_relation('peers', 'ceph-nvme')
        self.harness.add_relation_unit(rel_id, 'ceph-nvme/1')

    def add_ceph_relation(self):
        rel_id = self.harness.add_relation('ceph-client', 'ceph-mon')
        self.harness.add_relation_unit(rel_id, 'ceph-mon/0')
        self.harness.update_relation_data(
            rel_id,
            'ceph-mon/0',
            {
                'key': 'some-key',
                'mon_hosts': '2.2.2.2',
                'auth': 'some-auth',
            })

    def _check_calls(self, call_args_list, expected):
        calls = [(json.loads(call.args[0])['method'], call.args[1][0])
                 for call in call_args_list]

        self.assertEqual(len(calls), len(expected))
        for i, (method, local) in enumerate(expected):
            self.assertEqual(calls[i][0], method)
            self.assertEqual(calls[i][1] != '1.1.1.1', local)

    def _setup_mock_params(self, check_output, **kwargs):
        check_output.return_value = (
            b'{"private-address":"1.1.1.1","egress-subnets":"1.1.1.1/32",'
            b'"ingress-address":"1.1.1.1"}')
        event = mock.MagicMock()
        event.set_results = mock.MagicMock()
        event.fail = mock.MagicMock()
        rpc_sock = MockSocket(**kwargs)

        self.harness.begin()
        charm = self.harness.charm
        charm._rpc_sock = lambda *_: rpc_sock

        self.add_peers()
        self.add_ceph_relation()
        return charm, rpc_sock, event

    @mock.patch.object(charm.subprocess, 'check_output')
    def test_broken_ceph_relation(self, check_output):
        cached_resp = {'cluster_add': b'{"error": {"code": -2}}'}
        charm, rpc_sock, event = self._setup_mock_params(
            check_output, cached_resp=cached_resp)

        charm._stored.installed_services = True

        def _fail(_):
            raise RuntimeError("cannot call 'fail' on Pool Events")

        event.fail = _fail
        charm._on_ceph_relation_changed(event)
        event.defer.assert_called()

    @mock.patch.object(charm.subprocess, 'check_output')
    def test_create(self, check_output):
        charm, rpc_sock, event = self._setup_mock_params(check_output)
        event.params = {
            'rbd-pool': 'mypool',
            'rbd-image': 'myimage',
            'units': '2'
        }

        charm.on_create_endpoint_action(event)
        event.set_results.assert_called_with(
            {'nqn': 'nqn.1', 'address': '3.3.3.3',
             'port': 1, 'units': 2})

        # We expect the following calls:
        # local-create
        # remote-create
        # local-join
        # remote-join
        expected = [('create', True), ('create', False),
                    ('join', True), ('join', False)]
        self._check_calls(rpc_sock.sendto.call_args_list, expected)

    @mock.patch.object(charm.subprocess, 'check_output')
    def test_create_no_ha(self, check_output):
        charm, rpc_sock, event = self._setup_mock_params(check_output)
        event.params = {
            'rbd-pool': 'mypool',
            'rbd-image': 'myimage',
            'units': '1'
        }

        charm.on_create_endpoint_action(event)
        event.set_results.assert_called_with(
            {'nqn': 'nqn.1', 'address': '3.3.3.3',
             'port': 1, 'units': 1})

        # We expect no remote calls for this test.
        expected = [('create', True)]
        self._check_calls(rpc_sock.sendto.call_args_list, expected)

    @mock.patch.object(charm.subprocess, 'check_output')
    def test_delete(self, check_output):
        charm, rpc_sock, event = self._setup_mock_params(check_output)
        event.params = {'nqn': 'nqn.1'}

        charm.on_delete_endpoint_action(event)
        event.set_results.assert_called_with({'message': 'success'})

        # We expect the following calls:
        # local-find
        # remote-leave
        # local-remove
        expected = [('find', True), ('leave', False), ('remove', True)]
        self._check_calls(rpc_sock.sendto.call_args_list, expected)

    @mock.patch.object(charm.subprocess, 'check_output')
    def test_delete_fail(self, check_output):
        charm, rpc_sock, event = self._setup_mock_params(check_output)
        event.params = {'nqn': 'nonexistent'}

        charm.on_delete_endpoint_action(event)
        event.fail.assert_called()

    @mock.patch.object(charm.subprocess, 'check_output')
    @mock.patch.object(charm.subprocess, 'run')
    def test_join(self, run, check_output):
        charm, rpc_sock, event = self._setup_mock_params(check_output,
                                                         skip_first=True)
        charm._select_addr = lambda *_: '1.1.1.1'
        event.params = {'nqn': 'nqn.1'}

        charm.on_join_endpoint_action(event)
        event.set_results.assert_called_with(
            {'nqn': 'nqn.1', 'address': '3.3.3.3',
             'port': 1, 'units': 1})

        # We expect the following calls:
        # local-find
        # remote-find
        # local-create
        # remote-join
        # local-join
        expected = [('find', True), ('find', False), ('create', True),
                    ('join', False), ('join', True)]
        self._check_calls(rpc_sock.sendto.call_args_list, expected)

    @mock.patch.object(charm.subprocess, 'check_output')
    def test_join_failed(self, check_output):
        charm, rpc_sock, event = self._setup_mock_params(check_output)
        event.params = {'nqn': 'nonexistent'}

        charm.on_join_endpoint_action(event)
        event.fail.assert_called()

    @mock.patch.object(charm.subprocess, 'check_output')
    def test_list(self, check_output):
        charm, rpc_sock, event = self._setup_mock_params(check_output)

        charm.on_list_endpoints_action(event)
        args = event.set_results.call_args_list[0][0][0]['endpoints']
        self.assertEqual(len(args), 1)
        self.assertEqual(args[0]['nqn'], 'nqn.1')

    @mock.patch.object(charm.subprocess, 'check_output')
    def test_add_host(self, check_output):
        charm, rpc_sock, event = self._setup_mock_params(check_output)
        event.params = {'hostnqn': 'host_nqn', 'nqn': 'nqn.1',
                        'dhchap-key': 'some-key'}

        charm.on_add_host_action(event)
        event.set_results.assert_called()

        # We expect the following calls:
        # local-addhost
        # remove-addhost

        expected = [('host_add', True), ('host_add', False)]
        self._check_calls(rpc_sock.sendto.call_args_list, expected)

    @mock.patch.object(charm.subprocess, 'check_output')
    def test_add_host_failed(self, check_output):
        charm, rpc_sock, event = self._setup_mock_params(check_output)
        event.params = {'hostnqn': 'host_nqn', 'nqn': 'nonexistent'}

        charm.on_add_host_action(event)
        event.fail.assert_called()

    @mock.patch.object(charm.subprocess, 'check_output')
    def test_delete_host(self, check_output):
        charm, rpc_sock, event = self._setup_mock_params(check_output)
        event.params = {'host': 'host_nqn', 'nqn': 'nqn.1'}
        charm.on_delete_host_action(event)

        # We expect the following calls:
        # local-deletehost
        # remote-deletehost

        event.set_results.assert_called()
        expected = [('host_del', True), ('host_del', False)]
        self._check_calls(rpc_sock.sendto.call_args_list, expected)

    @mock.patch.object(charm.subprocess, 'check_output')
    def test_delete_host_failed(self, check_output):
        charm, rpc_sock, event = self._setup_mock_params(check_output)
        event.params = {'host': 'host_nqn', 'nqn': 'nonexistent'}
        charm.on_delete_host_action(event)

        event.fail.assert_called()

    @mock.patch.object(charm.subprocess, 'check_call')
    @mock.patch.object(charm.subprocess, 'check_output')
    def test_reset_target(self, check_output, check_call):
        charm, rpc_sock, event = self._setup_mock_params(check_output)
        self.harness.charm.on_reset_target_action(mock.MagicMock())

        # We expect the following calls:
        # local-list
        # remote-leave
        # local-remove
        expected = [('list', True), ('leave', False), ('remove', True)]
        self._check_calls(rpc_sock.sendto.call_args_list, expected)

    @mock.patch.object(charm.subprocess, 'check_output')
    def test_relation_departed(self, check_output):
        charm, rpc_sock, event = self._setup_mock_params(check_output)
        charm.on_peers_relation_departed(event)

        # We expect the following calls:
        # local-list
        # remote-leave
        expected = [('list', True), ('leave', False)]
        self._check_calls(rpc_sock.sendto.call_args_list, expected)
