# Copyright 2026 Canonical Ltd
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

import test_utils

from unittest.mock import patch

import charmhelpers.contrib.hardening.harden as harden_module

harden_module._DISABLE_HARDENING_FOR_UNIT_TEST = True

with patch('charmhelpers.contrib.hardening.harden.harden') as mock_dec:
    mock_dec.side_effect = (lambda *dargs, **dkwargs: lambda f:
                            lambda *args, **kwargs: f(*args, **kwargs))
    import ceph_hooks as hooks

from unittest.mock import MagicMock

import resource_manager
from resource_manager import ResourceStatus

TO_PATCH = [
    'resource_manager',
    'status_set',
    'status_get',
    'log',
]

STATUS_TO_PATCH = TO_PATCH + [
    'config',
    'ceph',
    'relation_ids',
    'get_conf',
    'application_version_set',
    'get_upstream_version',
    'use_vaultlocker',
    'get_devices',
    'get_journal_devices',
]


class ResourceHookTestCase(test_utils.CharmTestCase):

    def setUp(self):
        super(ResourceHookTestCase, self).setUp(hooks, TO_PATCH)

    def test_stop_uses_safe_release_all(self):
        hooks.stop()
        self.resource_manager.safe_release_all.assert_called_once_with()

    def test_stop_runs_the_safe_teardown_contract(self):
        # Keep the hook seam real: stop must use the wrapper that reports a
        # failed release rather than directly dropping state or EPA claims.
        with patch.object(hooks, 'resource_manager', resource_manager), \
                patch.object(resource_manager, 'release_all',
                             return_value=True) as release:
            hooks.stop()
        release.assert_called_once_with(
            client_factory=resource_manager.EpaClient)

    def test_update_status_verifies_without_changing_anything(self):
        hooks.update_status()
        self.resource_manager.safe_verify.assert_called_once_with()
        self.assertFalse(self.resource_manager.safe_reconcile.called)

    def test_peer_progress_advances_rollout(self):
        hooks.resource_rollout_changed()
        self.resource_manager.safe_reconcile.assert_called_once_with()

    def test_post_series_upgrade_reverifies(self):
        with patch.object(hooks, 'clear_unit_paused'), \
                patch.object(hooks, 'clear_unit_upgrading'):
            hooks.post_series_upgrade()
        self.resource_manager.safe_reconcile.assert_called_once_with()


@patch.object(hooks, 'get_mon_hosts', new=MagicMock(return_value=['1.1.1.1']))
@patch.object(hooks, 'check_aa_profile_needs_update',
              new=MagicMock(return_value=False))
@patch.object(hooks, 'get_bdev_enable_discard', new=MagicMock())
@patch.object(hooks, 'ch_context', new=MagicMock())
class ResourceStatusTestCase(test_utils.CharmTestCase):
    """The resource state is folded into the composed unit status.

    NOTE: status_get() does not reflect a status_set() made earlier in the
    same hook execution, so the message has to be composed before it is
    set rather than appended afterwards.
    """

    def setUp(self):
        super(ResourceStatusTestCase, self).setUp(hooks, STATUS_TO_PATCH)
        self.config.side_effect = self.test_config.get
        self.get_upstream_version.return_value = '20.2.0'
        self.use_vaultlocker.return_value = False
        self.relation_ids.return_value = ['mon:1']
        self.get_conf.return_value = 'osd-bootstrap-key'
        self.status_get.return_value = ('active', 'Unit is ready (2 OSD)')
        self.ceph.get_running_osds.return_value = ['0', '1']
        self.get_devices.return_value = []
        self.get_journal_devices.return_value = []

    def test_deviations_are_appended_to_the_ready_message(self):
        self.resource_manager.assess.return_value = ResourceStatus(
            False, 'profile balanced applied with 1 deviation(s), see logs')
        hooks.assess_status()
        self.status_set.assert_called_with(
            'active',
            'Unit is ready (2 OSD), profile balanced applied with '
            '1 deviation(s), see logs')

    def test_nothing_is_appended_without_a_resource_state(self):
        self.resource_manager.assess.return_value = None
        hooks.assess_status()
        self.status_set.assert_called_with(
            'active', 'Unit is ready (2 OSD)')

    def test_pending_rollout_sets_waiting(self):
        self.resource_manager.assess.return_value = ResourceStatus(
            False, 'waiting for host0', True)
        hooks.assess_status()
        self.status_set.assert_called_once_with('waiting', 'waiting for host0')

    def test_resource_errors_block_the_unit(self):
        self.resource_manager.assess.return_value = ResourceStatus(
            True, 'performance-profile performance: osd.7 unaligned')
        hooks.assess_status()
        self.status_set.assert_called_once_with(
            'blocked', 'performance-profile performance: osd.7 unaligned')

    def test_more_fundamental_blockers_win(self):
        self.relation_ids.return_value = []
        self.resource_manager.assess.return_value = ResourceStatus(
            True, 'performance-profile performance: osd.7 unaligned')
        hooks.assess_status()
        self.status_set.assert_called_once_with(
            'blocked', 'Missing relation: monitor')
