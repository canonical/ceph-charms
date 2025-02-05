# Copyright 2019 Canonical Ltd
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
import sys

sys.modules['charms.layer'] = mock.MagicMock()
import actions.actions as actions
import charm.openstack.ceph_rbd_mirror as crm

import charms_openstack.test_utils as test_utils


class TestCephRBDMirrorActions(test_utils.PatchHelper):

    def setUp(self):
        super().setUp()
        self.patch_release(crm.CephRBDMirrorCharm.release)
        self.crm_charm = mock.MagicMock()
        self.patch_object(actions.charms_openstack.charm,
                          'provide_charm_instance',
                          new=mock.MagicMock())
        self.provide_charm_instance().__enter__.return_value = \
            self.crm_charm
        self.provide_charm_instance().__exit__.return_value = None

    def test_rbd_mirror_action(self):
        self.patch_object(actions.reactive, 'endpoint_from_name')
        self.patch_object(actions.ch_core.hookenv, 'action_get')
        self.patch_object(actions.subprocess, 'check_output')
        self.patch_object(actions.ch_core.hookenv, 'action_set')
        endpoint = mock.MagicMock()
        endpoint.pools = collections.OrderedDict(
            {'apool': {'applications': {'rbd': {}}},
             'bpool': {'applications': {'rbd': {}}}})
        self.endpoint_from_name.return_value = endpoint
        self.crm_charm.eligible_pools.return_value = endpoint.pools
        self.crm_charm.ceph_id = 'acephid'
        self.action_get.return_value = False
        self.check_output.return_value = 'Promoted 0 mirrored images\n'
        actions.rbd_mirror_action(['promote'])
        self.endpoint_from_name.assert_called_once_with('ceph-local')
        self.crm_charm.eligible_pools.assert_called_once_with(endpoint.pools)
        self.action_get.assert_has_calls([
            mock.call('pools'),
            mock.call('force'),
            mock.call('verbose'),
            mock.call('format'),
        ])
        self.check_output.assert_has_calls([
            mock.call(['rbd', '--id', 'acephid', 'mirror', 'pool', 'promote',
                       'apool'],
                      stderr=actions.subprocess.STDOUT,
                      universal_newlines=True),
            mock.call(['rbd', '--id', 'acephid', 'mirror', 'pool', 'promote',
                       'bpool'],
                      stderr=actions.subprocess.STDOUT,
                      universal_newlines=True),
        ], any_order=True)
        # the order the pools has in the output string is undefined
        self.action_set.assert_called_once_with(
            {'output': mock.ANY})
        self.assertEqual(
            sorted(self.action_set.call_args[0][0]['output'].split('\n')),
            ['apool: Promoted 0 mirrored images',
             'bpool: Promoted 0 mirrored images'])
        self.action_get.side_effect = [None, True, True, False]
        self.check_output.reset_mock()
        actions.rbd_mirror_action(['promote'])
        self.check_output.assert_has_calls([
            mock.call(['rbd', '--id', 'acephid', 'mirror', 'pool', 'promote',
                       '--force', '--verbose', 'apool'],
                      stderr=actions.subprocess.STDOUT,
                      universal_newlines=True),
            mock.call(['rbd', '--id', 'acephid', 'mirror', 'pool', 'promote',
                       '--force', '--verbose', 'bpool'],
                      stderr=actions.subprocess.STDOUT,
                      universal_newlines=True),
        ], any_order=True)
        self.action_get.assert_has_calls([
            mock.call('pools'),
            mock.call('force'),
            mock.call('verbose'),
            mock.call('format'),
        ])
        self.action_get.side_effect = ['apool', True, True, False]
        self.check_output.reset_mock()
        actions.rbd_mirror_action(['promote'])
        self.check_output.assert_called_once_with(
            ['rbd', '--id', 'acephid', 'mirror', 'pool', 'promote',
             '--force', '--verbose', 'apool'],
            stderr=actions.subprocess.STDOUT,
            universal_newlines=True)
        self.action_get.assert_has_calls([
            mock.call('pools'),
            mock.call('force'),
            mock.call('verbose'),
            mock.call('format'),
        ])

    def test_refresh_pools(self):
        self.patch_object(actions.reactive, 'is_flag_set')
        self.patch_object(actions.ch_core.hookenv, 'action_fail')
        self.is_flag_set.return_value = False
        actions.refresh_pools([])
        self.is_flag_set.assert_called_once_with('leadership.is_leader')
        self.action_fail.assert_called_once_with(
            'run action on the leader unit')
        self.is_flag_set.return_value = True
        self.patch_object(actions.reactive, 'set_flag')
        self.patch_object(actions.ch_core.unitdata, '_KV')
        self.patch_object(actions.reactive, 'main')
        actions.refresh_pools([])
        self.set_flag.assert_called_once_with('refresh.pools')
        self._KV.flush.assert_called_once_with()
        self.main.assert_called_once_with()

    def test_resync_pools(self):
        self.patch_object(actions.reactive, 'endpoint_from_name')
        self.patch_object(actions.ch_core.hookenv, 'action_get')
        self.patch_object(actions.subprocess, 'check_output')
        self.patch_object(actions.ch_core.hookenv, 'action_set')
        endpoint = mock.MagicMock()
        endpoint.pools = collections.OrderedDict(
            {'apool': {'applications': {'rbd': {}}}})
        self.endpoint_from_name.return_value = endpoint
        self.crm_charm.eligible_pools.return_value = endpoint.pools
        self.crm_charm.ceph_id = 'acephid'
        self.action_get.side_effect = [False, None]
        actions.resync_pools([])
        self.action_get.assert_has_calls([
            mock.call('i-really-mean-it'),
        ])
        self.assertFalse(self.check_output.called)
        self.assertFalse(self.action_set.called)
        self.action_get.side_effect = [True, 'bpool']
        self.check_output.return_value = json.dumps([])
        actions.resync_pools([])
        self.action_get.assert_has_calls([
            mock.call('i-really-mean-it'),
            mock.call('pools'),
        ])
        self.check_output.assert_called_once_with(
            ['rbd', '--id', 'acephid', '--format', 'json',
             '-p', 'bpool', 'ls'],
            universal_newlines=True)
        self.action_set.assert_called_once_with({'output': ''})
        self.action_get.side_effect = [True, None]
        self.check_output.side_effect = [
            json.dumps(['imagea', 'imageb']),
            json.dumps({'mirroring': {'state': 'enabled'}}),
            'resync flagged for imagea\n',
            json.dumps({'mirroring': {'state': 'disabled'}}),
        ]
        self.check_output.reset_mock()
        actions.resync_pools([])
        self.action_get.assert_has_calls([
            mock.call('i-really-mean-it'),
            mock.call('pools'),
        ])
        self.assertEqual(
            sorted(self.action_set.call_args[0][0]['output'].split('\n')),
            ['apool/imagea: resync flagged for imagea'])

    def test_main(self):
        self.patch_object(actions, 'ACTIONS')
        self.patch_object(actions.ch_core.hookenv, 'action_fail')
        args = ['/non-existent/path/to/charm/binary/promote']
        function = mock.MagicMock()
        self.ACTIONS.__getitem__.return_value = function
        actions.main(args)
        function.assert_called_once_with(args)
        self.ACTIONS.__getitem__.side_effect = KeyError
        self.assertEqual(actions.main(args), 'Action promote is undefined')
        self.ACTIONS.__getitem__.side_effect = None
        function.side_effect = Exception('random exception')
        actions.main(args)
        self.action_fail.assert_called_once_with('random exception')
