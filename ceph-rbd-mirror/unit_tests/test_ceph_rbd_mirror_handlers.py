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

from unittest import mock

import charm.openstack.ceph_rbd_mirror as crm
import reactive.ceph_rbd_mirror_handlers as handlers

import charms_openstack.test_utils as test_utils


class TestRegisteredHooks(test_utils.TestRegisteredHooks):

    def test_hooks(self):
        defaults = [
            'charm.installed',
            'config.rendered',
            'update-status',
            'upgrade-charm',
        ]
        hook_set = {
            'when': {
                'config_changed': (
                    'config.changed',
                    'ceph-local.available',
                    'ceph-remote.available',
                ),
                'render_stuff': (
                    'ceph-local.available',
                    'ceph-remote.available',
                ),
                'configure_pools': (
                    'leadership.is_leader',
                    'config.rendered',
                    'ceph-local.available',
                    'ceph-remote.available',
                ),
                'refresh_pools': (
                    'leadership.is_leader',
                    'refresh.pools',
                    'ceph-local.available',
                    'ceph-remote.available',
                ),
                'request_keys': (
                    'ceph-local.connected',
                    'ceph-remote.connected',
                ),
            },
            'when_none': {
                'config_changed': (
                    'is-update-status-hook',),
                'render_stuff': (
                    'is-update-status-hook',),
                'refresh_pools': (
                    'is-update-status-hook',),
                'configure_pools': (
                    'is-update-status-hook',),
                'request_keys': (
                    'is-update-status-hook',
                    'ceph-local.available',
                    'ceph-remote.available',
                ),
            },
        }
        # test that the hooks were registered
        self.registered_hooks_test_helper(handlers, hook_set, defaults)


class TestCephRBDMirrorHandlers(test_utils.PatchHelper):

    def setUp(self):
        super().setUp()
        self.patch_release(crm.CephRBDMirrorCharm.release)
        self.crm_charm = mock.MagicMock()
        self.patch_object(handlers.charm, 'provide_charm_instance',
                          new=mock.MagicMock())
        self.provide_charm_instance().__enter__.return_value = \
            self.crm_charm
        self.provide_charm_instance().__exit__.return_value = None

    def test_request_keys(self):
        self.patch_object(handlers.reactive, 'endpoint_from_flag')
        endpoint_local = mock.MagicMock()
        endpoint_remote = mock.MagicMock()
        endpoint_local.endpoint_name = 'ceph-local'
        endpoint_remote.endpoint_name = 'ceph-remote'
        self.endpoint_from_flag.side_effect = [endpoint_local,
                                               endpoint_remote]
        handlers.request_keys()
        self.endpoint_from_flag.assert_has_calls([
            mock.call('ceph-local.connected'),
            mock.call('ceph-remote.connected'),
        ])
        endpoint_local.request_key.assert_called_once_with()
        endpoint_remote.request_key.assert_called_once_with()
        self.crm_charm.assess_status.assert_called_once_with()

    def test_config_changed(self):
        self.patch_object(handlers.reactive, 'endpoint_from_flag')
        handlers.config_changed()
        self.endpoint_from_flag.assert_has_calls([
            mock.call('ceph-local.available'),
            mock.call('ceph-remote.available'),
        ])
        self.crm_charm.upgrade_if_available.assert_called_once_with(
            [self.endpoint_from_flag(), self.endpoint_from_flag()])
        self.crm_charm.assess_status.assert_called_once_with()

    def test_render_stuff(self):
        self.patch_object(handlers.ch_core.host, 'service')
        endpoint_local = mock.MagicMock()
        endpoint_remote = mock.MagicMock()
        endpoint_local.endpoint_name = 'ceph-local'
        endpoint_local.pools = {}
        endpoint_remote.endpoint_name = 'ceph-remote'
        endpoint_remote.pools = {}
        self.crm_charm.services = ['aservice']
        endpoint_local.key = None
        handlers.render_stuff(endpoint_local, endpoint_remote)
        self.assertFalse(self.crm_charm.configure_ceph_keyring.called)
        endpoint_local.key = 'akey'
        handlers.render_stuff(endpoint_local, endpoint_remote)
        self.crm_charm.configure_ceph_keyring.assert_has_calls([
            mock.call(endpoint_local.key, cluster_name=None),
            mock.call(endpoint_remote.key, cluster_name='remote'),
        ])
        self.crm_charm.render_with_interfaces.assert_called_once_with(
            (endpoint_local, endpoint_remote))

    def test_refresh_pools(self):
        self.patch_object(handlers.reactive, 'endpoint_from_name')
        self.patch_object(handlers.reactive, 'clear_flag')
        endpoint_local = mock.MagicMock()
        endpoint_remote = mock.MagicMock()
        self.endpoint_from_name.side_effect = [endpoint_local, endpoint_remote]
        handlers.refresh_pools()
        self.endpoint_from_name.assert_has_calls([
            mock.call('ceph-local'),
            mock.call('ceph-remote'),
        ])
        endpoint_local.refresh_pools.assert_called_once_with()
        endpoint_remote.refresh_pools.assert_called_once_with()
        self.clear_flag.assert_called_once_with('refresh.pools')

    def test_configure_pools(self):
        self.patch_object(handlers.reactive, 'endpoint_from_flag')
        endpoint_local = mock.MagicMock()
        endpoint_remote = mock.MagicMock()
        self.crm_charm.collapse_and_filter_broker_requests.side_effect = [
            endpoint_local, endpoint_remote]
        endpoint_local.endpoint_name = 'ceph-local'
        endpoint_local.pools = {
            'cinder-ceph': {
                'applications': {'rbd': {}},
                'parameters': {
                    'pg_num': 42,
                    'size': 3,
                    'rbd-mirroring-mode': 'pool'
                },
                'quota': {'max_bytes': 1024, 'max_objects': 51},
            },
        }
        endpoint_remote.endpoint_name = 'ceph-remote'
        self.endpoint_from_flag.side_effect = [endpoint_local,
                                               endpoint_remote]
        self.crm_charm.eligible_pools.return_value = endpoint_local.pools
        self.crm_charm.mirror_pool_enabled.return_value = False
        self.crm_charm.pool_mirroring_mode.return_value = 'pool'

        handlers.configure_pools()
        self.endpoint_from_flag.assert_has_calls([
            mock.call('ceph-local.available'),
            mock.call('ceph-remote.available'),
        ])
        self.crm_charm.eligible_pools.assert_called_once_with(
            endpoint_local.pools)
        self.crm_charm.pool_mirroring_mode.assert_called_once_with(
            'cinder-ceph', [endpoint_local, endpoint_remote])
        self.crm_charm.mirror_pool_enabled.assert_called_once_with(
            'cinder-ceph', 'pool')
        self.crm_charm.mirror_pool_enable.assert_called_once_with(
            'cinder-ceph', 'pool')
        endpoint_remote.maybe_send_rq.assert_called_once_with(endpoint_local)
