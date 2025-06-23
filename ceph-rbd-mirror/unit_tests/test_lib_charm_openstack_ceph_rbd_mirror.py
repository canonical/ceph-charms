# Copyright 2018 Canonical Ltd
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

import collections
import json
from unittest import mock
import subprocess

import charms_openstack.test_utils as test_utils

import charm.openstack.ceph_rbd_mirror as ceph_rbd_mirror


class Helper(test_utils.PatchHelper):

    def setUp(self):
        super().setUp()
        self.patch_release(ceph_rbd_mirror.CephRBDMirrorCharm.release)


class TestCephRBDMirrorCharm(Helper):

    def test_custom_assess_status_check(self):
        self.patch_object(ceph_rbd_mirror.socket, 'gethostname')
        self.patch_object(ceph_rbd_mirror.reactive, 'is_flag_set')
        self.is_flag_set.return_value = False
        crmc = ceph_rbd_mirror.CephRBDMirrorCharm()
        self.assertEqual(crmc.custom_assess_status_check(), (None, None))
        self.is_flag_set.return_value = True
        self.patch_object(ceph_rbd_mirror.reactive, 'endpoint_from_flag')
        self.assertEqual(crmc.custom_assess_status_check(),
                         ('waiting', 'Waiting for pools to be created'))
        self.endpoint_from_flag.assert_called_once_with(
            'ceph-local.available')
        crmc.mirror_pools_summary = mock.MagicMock()
        crmc.mirror_pools_summary.return_value = collections.OrderedDict({
            'pool_health': collections.OrderedDict(
                {'OK': 1, 'WARN': 1, 'ERROR': 1}),
            'image_states': collections.OrderedDict(
                {'stopped': 2, 'replaying': 2}),
        })
        result = crmc.custom_assess_status_check()
        # Disabling blocked state until
        # https://bugs.launchpad.net/charm-ceph-rbd-mirror/+bug/1879749
        # is resolved
        # self.assertTrue('blocked' in result[0])
        # the order of which the statuses appear in the string is undefined
        self.assertTrue('OK (1)' in result[1])
        self.assertTrue('WARN (1)' in result[1])
        self.assertTrue('ERROR (1)' in result[1])
        self.assertTrue('Primary (2)' in result[1])
        self.assertTrue('Secondary (2)' in result[1])
        crmc.mirror_pools_summary.return_value = collections.OrderedDict({
            'pool_health': collections.OrderedDict({'OK': 1}),
            'image_states': collections.OrderedDict({'stopped': 2}),
        })
        self.assertEqual(crmc.custom_assess_status_check(),
                         ('active', 'Unit is ready (Pools OK (1) '
                                    'Images Primary (2))'))
        crmc.mirror_pools_summary.side_effect = subprocess.CalledProcessError(
            42, [])
        self.assertEqual(crmc.custom_assess_status_check(), (None, None))

    def test__mirror_pool_info(self):
        self.patch_object(ceph_rbd_mirror.socket, 'gethostname')
        self.patch_object(ceph_rbd_mirror.subprocess, 'check_output')
        self.gethostname.return_value = 'ahostname'
        self.check_output.return_value = '{}'
        crmc = ceph_rbd_mirror.CephRBDMirrorCharm()
        crmc._mirror_pool_info('apool')
        self.check_output.assert_called_once_with(
            ['rbd', '--id', 'rbd-mirror.ahostname', 'mirror', 'pool', 'info',
             '--format', 'json', 'apool'], universal_newlines=True)

    def test_mirror_pool_enabled(self):
        self.patch_object(ceph_rbd_mirror.socket, 'gethostname')
        crmc = ceph_rbd_mirror.CephRBDMirrorCharm()
        _mirror_pool_info = mock.MagicMock()
        _mirror_pool_info.return_value = {
            'mode': 'pool',
            'peers': [{
                'uuid': '0e4dfe58-93fc-44f8-8c74-7e700f950118',
                'cluster_name': 'remote',
                'client_name':
                    'client.rbd-mirror.juju-c50b1a-zaza-4ce96f1e7e43-12'}]
        }
        crmc._mirror_pool_info = _mirror_pool_info
        self.assertTrue(crmc.mirror_pool_enabled('apool', mode='pool'))
        _mirror_pool_info.assert_called_once_with('apool')
        _mirror_pool_info.return_value = {'mode': 'disabled'}
        self.assertFalse(crmc.mirror_pool_enabled('apool', mode='pool'))

    def test_mirror_pool_has_peers(self):
        self.patch_object(ceph_rbd_mirror.socket, 'gethostname')
        crmc = ceph_rbd_mirror.CephRBDMirrorCharm()
        _mirror_pool_info = mock.MagicMock()
        _mirror_pool_info.return_value = {
            'mode': 'pool',
            'peers': [{
                'uuid': '0e4dfe58-93fc-44f8-8c74-7e700f950118',
                'cluster_name': 'remote',
                'client_name':
                    'client.rbd-mirror.juju-c50b1a-zaza-4ce96f1e7e43-12'}]
        }
        crmc._mirror_pool_info = _mirror_pool_info
        self.assertTrue(crmc.mirror_pool_has_peers('apool'))
        _mirror_pool_info.assert_called_once_with('apool')
        _mirror_pool_info.return_value = {
            'mode': 'pool',
            'peers': []}
        self.assertFalse(crmc.mirror_pool_has_peers('apool'))

    def test_pools_in_broker_request(self):
        rq = mock.MagicMock()
        rq.api_version = 1
        rq.ops = [{'op': 'create-pool', 'name': 'fakepool'}]
        crmc = ceph_rbd_mirror.CephRBDMirrorCharm()
        self.assertIn('fakepool', crmc.pools_in_broker_request(rq))

    def test_collapse_and_filter_broker_requests(self):
        self.patch_object(ceph_rbd_mirror.ch_ceph, 'CephBrokerRq')

        class FakeCephBrokerRq(object):

            def __init__(self):
                self.ops = []

            def add_op(self, op):
                self.ops.append(op)

        self.CephBrokerRq.side_effect = FakeCephBrokerRq

        broker_requests = [
            {
                'api-version': 1,
                'ops': [
                    {
                        'op': 'create-pool',
                        'name': 'pool-rq0',
                        'app-name': 'rbd',
                    },
                ]
            },
            {
                'api-version': 1,
                'ops': [
                    {
                        'op': 'create-pool',
                        'name': 'pool-rq1',
                        'app-name': 'notrbd',
                    },
                ]
            },
            {
                'api-version': 1,
                'ops': [
                    {
                        'op': 'create-pool',
                        'name': 'pool-rq2',
                        'app-name': 'rbd',
                        'someotherkey': 'value',
                    },
                ]
            },
        ]
        crmc = ceph_rbd_mirror.CephRBDMirrorCharm()
        rq = crmc.collapse_and_filter_broker_requests(
            broker_requests,
            set(('create-pool',)),
            require_vp={'app-name': 'rbd'})
        self.assertDictEqual(
            rq.ops[0],
            {'app-name': 'rbd', 'name': 'pool-rq0', 'op': 'create-pool'})
        self.assertDictEqual(
            rq.ops[1],
            {'app-name': 'rbd', 'name': 'pool-rq2', 'op': 'create-pool',
             'someotherkey': 'value'})
        self.assertTrue(len(rq.ops) == 2)
        rq = crmc.collapse_and_filter_broker_requests(
            broker_requests,
            set(('create-pool',)),
            require_vp={'app-name': 'rbd', 'someotherkey': 'value'})
        self.assertDictEqual(
            rq.ops[0],
            {'app-name': 'rbd', 'name': 'pool-rq2', 'op': 'create-pool',
             'someotherkey': 'value'})
        self.assertTrue(len(rq.ops) == 1)

    def test_pool_mirroring_mode(self):
        self.patch_object(ceph_rbd_mirror.ch_ceph, 'CephBrokerRq')

        class FakeCephBrokerRq(object):
            def __init__(self, raw_request_data=None):
                request_data = json.loads(raw_request_data)
                self.api_version = request_data['api-version']
                self.request_id = request_data['request-id']
                self.set_ops(request_data['ops'])

            def set_ops(self, ops):
                self.ops = ops

            def add_op(self, op):
                self.ops.append(op)

        self.CephBrokerRq.side_effect = FakeCephBrokerRq

        brq1_data = json.dumps({
            'api-version': 1,
            'request-id': 'broker_rq1',
            'ops': [
                {
                    'op': 'create-pool',
                    'name': 'pool-rq0',
                    'app-name': 'rbd-pool',
                    'rbd-mirroring-mode': 'pool'
                },
            ]
        })
        brq2_data = json.dumps({
            'api-version': 1,
            'request-id': 'broker_rq2',
            'ops': [
                {
                    'op': 'create-pool',
                    'name': 'pool-rq1',
                    'app-name': 'rbd-image',
                    'rbd-mirroring-mode': 'image'
                },
            ]
        })

        brq1 = self.CephBrokerRq(raw_request_data=brq1_data)
        brq2 = self.CephBrokerRq(raw_request_data=brq2_data)
        broker_requests = [brq1, brq2, None]
        crmc = ceph_rbd_mirror.CephRBDMirrorCharm()
        rq0 = crmc.pool_mirroring_mode('pool-rq0', broker_requests)
        self.assertEqual('pool', rq0)
        rq1 = crmc.pool_mirroring_mode('pool-rq1', broker_requests)
        self.assertEqual('image', rq1)

    def test_eligible_pools(self):
        pools = {
            'rbd-replicated': {
                'applications': {'rbd': {}},
                'parameters': {'erasure_code_profile': ''},
            },
            'rbd-ec-metadata': {
                'applications': {'rbd': {}},
                'parameters': {'erasure_code_profile': ''},
            },
            'rbd-ec-data': {
                'applications': {'rbd'},
                'parameters': {'erasure_code_profile': 'test_profile'},
            },
            'non-rbd-pool': {
                'applications': {'rgw': {}},
                'parameters': {},
            },
        }

        crmc = ceph_rbd_mirror.CephRBDMirrorCharm()
        eligible = crmc.eligible_pools(pools)
        self.assertIsNotNone(eligible)
        self.assertEqual(eligible, {
            name: attrs for name, attrs in pools.items()
            if name in {'rbd-replicated', 'rbd-ec-metadata'}
        })
