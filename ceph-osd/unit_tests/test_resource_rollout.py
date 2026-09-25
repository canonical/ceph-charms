# Copyright 2026 Canonical Ltd
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
# http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import errno
import json
import subprocess
import unittest
from unittest.mock import Mock, patch

import resource_rollout as rollout
from test_resource_manager import FakeKV


class PeerRolloutTestCase(unittest.TestCase):
    """Three serialized unit writers; relation updates commit at hook exit."""

    def setUp(self):
        self.units = ['ceph-osd/0', 'ceph-osd/1', 'ceph-osd/2']
        self.leader = self.units[0]
        self.unit = self.leader
        self.databases = {u: FakeKV() for u in self.units}
        self.bags = {u: {} for u in self.units + ['ceph-osd']}
        self.configs = {
            u: {'performance-profile': 'minimal'} for u in self.units}
        self.inputs = {u: {'osds': [u]} for u in self.units}
        self.markers = {}
        self.writes = {}
        self.calls = []
        self.failure = None
        self.check_ready = Mock()
        self.api = Mock()
        self.api.config.side_effect = lambda: self.configs[self.unit]
        self.api.local_unit.side_effect = lambda: self.unit
        self.api.application_name.return_value = 'ceph-osd'
        self.api.is_leader.side_effect = lambda: self.unit == self.leader
        self.api.relation_ids.return_value = ['resource-peers:0']
        self.api.related_units.side_effect = lambda rid: [
            u for u in self.units if u != self.unit]
        self.api.expected_peer_units.side_effect = lambda: [
            u for u in self.units if u != self.unit]
        self.api.relation_get.side_effect = self.get
        self.api.relation_set.side_effect = self.set
        for name, value in [('hookenv', self.api),
                            ('kv', lambda: self.databases[self.unit]),
                            ('_read_marker', self.markers.get),
                            ('_write_marker', self.markers.__setitem__)]:
            p = patch.object(rollout, name, value)
            p.start()
            self.addCleanup(p.stop)
        p = patch.object(rollout.socket, 'gethostname',
                         side_effect=lambda: 'host-' + self.unit[-1])
        p.start()
        self.addCleanup(p.stop)

    def get(self, attribute, unit=None, rid=None, app=None):
        return self.bags[app or unit].get(attribute)

    def set(self, rid, values, app=False):
        bag = 'ceph-osd' if app else self.unit
        if app:
            self.assertEqual(self.unit, self.leader)
        self.writes.setdefault(bag, {}).update(values)

    def active(self):
        return json.loads(self.bags['ceph-osd'][rollout.ROLLOUT])

    def callback(self):
        if self.unit == self.failure:
            return {'errors': ['OSD failed to recover']}
        self.calls.append((self.unit, self.configs[self.unit].copy()))
        return {'errors': [], 'osds': {}}

    def step(self, unit):
        self.unit = unit
        self.writes = {}
        try:
            return rollout.run(self.inputs[unit], self.callback,
                               unmanaged=self.configs[unit][
                                   'performance-profile'] == 'unmanaged',
                               check_ready=self.check_ready)
        except (rollout.RollingWait, rollout.RollingError) as exc:
            return exc
        finally:
            for bag, values in self.writes.items():
                self.bags[bag].update(values)

    def drain(self):
        for _ in range(6):
            for unit in self.units:
                self.step(unit)

    def set_profile(self, profile):
        for config in self.configs.values():
            config['performance-profile'] = profile

    def test_initial_requests_then_ordered_noop_participation(self):
        self.inputs[self.units[1]] = {'osds': []}
        for unit in self.units:
            self.assertIsInstance(self.step(unit), rollout.RollingWait)
        self.assertEqual(self.calls, [])
        self.step(self.units[0])
        self.assertIsInstance(self.step(self.units[2]), rollout.RollingWait)
        self.step(self.units[1])
        self.step(self.units[2])
        self.assertEqual([u for u, _ in self.calls], self.units)
        self.assertEqual(set(self.active()['requests']), set(self.units))
        self.drain()
        self.assertEqual(len(self.calls), 3)

    def test_a_b_a_gets_three_unique_generations(self):
        ids = []
        for profile in ['minimal', 'balanced', 'minimal']:
            self.set_profile(profile)
            self.drain()
            ids.append(self.active()['id'])
        self.assertEqual(len(set(ids)), 3)
        self.assertEqual([u for u, _ in self.calls], self.units * 3)

    def test_inventory_change_also_rolls_all_participants(self):
        self.drain()
        before = self.active()['id']
        self.inputs[self.units[2]]['osds'].append('new-osd')
        self.drain()
        self.assertNotEqual(before, self.active()['id'])
        self.assertEqual([u for u, _ in self.calls], self.units * 2)

    def test_waits_for_configuration_convergence(self):
        self.drain()
        before = self.active()['id']
        self.configs[self.units[0]]['performance-profile'] = 'balanced'
        self.drain()
        self.assertEqual(before, self.active()['id'])
        self.assertEqual(len(self.calls), 3)

    def test_new_request_cannot_supersede_an_unfinished_generation(self):
        for unit in self.units:
            self.step(unit)
        self.step(self.units[0])
        first = self.active()['id']
        self.set_profile('balanced')
        self.step(self.units[0])
        self.assertEqual(self.active()['id'], first)
        self.assertIsInstance(self.step(self.units[2]), rollout.RollingWait)
        self.assertEqual([u for u, _ in self.calls], [self.units[0]])
        self.drain()
        self.assertNotEqual(self.active()['id'], first)
        self.assertEqual([u for u, _ in self.calls], self.units * 2)

    def test_failure_fences_successors_and_needs_explicit_retry(self):
        self.failure = self.units[1]
        self.drain()
        generation = self.active()['id']
        self.assertEqual([u for u, _ in self.calls], [self.units[0]])
        self.failure = None
        self.drain()
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.active()['id'], generation)
        # A config change explicitly retries the failed participant in the
        # existing generation, not a concurrent replacement generation.
        for config in self.configs.values():
            config['suppress-profile-warnings'] = True
        self.step(self.units[1])
        self.assertEqual(self.active()['id'], generation)
        self.step(self.units[2])
        self.assertEqual([u for u, _ in self.calls], self.units)
        self.drain()
        self.assertNotEqual(self.active()['id'], generation)

    def test_leadership_change_keeps_inflight_generation(self):
        for unit in self.units:
            self.step(unit)
        self.step(self.units[0])
        first = self.active()['id']
        self.leader = self.units[2]
        self.step(self.units[2])
        self.assertEqual(first, self.active()['id'])
        self.step(self.units[1])
        self.step(self.units[2])
        self.assertEqual([u for u, _ in self.calls], self.units)

    def test_missing_peer_is_not_implicitly_skipped(self):
        self.api.expected_peer_units.side_effect = lambda: [
            u for u in self.units if u != self.unit] + ['ceph-osd/3']
        self.drain()
        self.assertEqual(self.calls, [])
        self.assertNotIn(rollout.ROLLOUT, self.bags['ceph-osd'])

    def test_unmanaged_default_needs_no_monitor_access(self):
        self.set_profile('unmanaged')
        with patch.object(rollout, '_read_marker') as read:
            self.drain()
        read.assert_not_called()
        self.assertNotIn(rollout.ROLLOUT, self.bags['ceph-osd'])

    def test_unmanaged_cannot_bypass_failed_generation(self):
        self.failure = self.units[1]
        self.drain()
        first = self.active()['id']
        self.set_profile('unmanaged')
        self.drain()
        self.assertEqual(first, self.active()['id'])
        self.assertNotIn(self.units[2], [u for u, _ in self.calls])

    def test_unmanaged_noop_must_be_ready_before_handoff(self):
        self.failure = self.units[1]
        self.drain()
        generation = self.active()['id']
        self.failure = None
        self.set_profile('unmanaged')
        self.check_ready.side_effect = rollout.RollingError('OSD still down')
        result = self.step(self.units[1])
        self.assertIsInstance(result, rollout.RollingError)
        operation = rollout._operation(self.active())
        self.assertFalse(operation.completed(self.units[1]))
        self.assertEqual(generation, self.active()['id'])
        self.assertIsInstance(self.step(self.units[2]), rollout.RollingError)

    def test_charm_refresh_gets_a_new_request(self):
        self.drain()
        first = self.active()['id']
        rollout.request_refresh()
        self.drain()
        self.assertNotEqual(first, self.active()['id'])

    def test_observe_is_read_only_and_never_runs_callback(self):
        for unit in self.units:
            self.step(unit)
        self.step(self.units[0])
        self.unit = self.units[2]
        before = self.markers.copy()
        with self.assertRaises(rollout.RollingWait):
            rollout.observe()
        self.assertEqual(before, self.markers)
        self.assertEqual(len(self.calls), 1)

    def _discard_done_write(self, unit):
        """Run a participant but discard its hook-buffered relation writes."""
        self.unit = unit
        self.writes = {}
        self.assertEqual(rollout.run(
            self.inputs[unit], self.callback, check_ready=self.check_ready),
            {'errors': [], 'osds': {}})
        self.assertTrue(rollout._operation(self.active()).completed(unit))
        self.assertEqual(
            self.databases[unit].get(rollout.LOCAL_PROGRESS)['status'], 'done')
        self.assertNotIn(rollout.PROGRESS, self.bags[unit])

    def test_retry_republishes_done_after_interrupted_relation_write(self):
        for unit in self.units:
            self.step(unit)
        self.step(self.units[0])

        # Simulate interruption after the monitor's durable done marker and
        # unitdata flush, but before Juju commits this hook's relation writes.
        unit = self.units[1]
        self._discard_done_write(unit)

        # The retry must compare against the committed unit bag, not merely
        # the local record, and repair the missing successor notification.
        self.step(unit)
        progress = json.loads(self.bags[unit][rollout.PROGRESS])
        self.assertEqual(progress['status'], 'done')
        self.assertEqual(progress['rollout'], self.active()['id'])
        self.assertEqual([u for u, _ in self.calls], self.units[:2])

    def test_last_participant_repairs_a_discarded_done_write(self):
        for unit in self.units:
            self.step(unit)
        self.step(self.units[0])
        self.step(self.units[1])
        self._discard_done_write(self.units[2])
        self.assertTrue(all(rollout._operation(self.active()).completed(unit)
                            for unit in self.units))

        # All monitor markers are already complete, so this exercises the
        # completed-generation fast path rather than a successor handoff.
        self.step(self.units[2])
        progress = json.loads(self.bags[self.units[2]][rollout.PROGRESS])
        self.assertEqual(progress['status'], 'done')
        self.assertEqual(progress['request'],
                         self.active()['requests'][self.units[2]]['id'])

    def test_completed_participant_repairs_the_frozen_request_only(self):
        for unit in self.units:
            self.step(unit)
        self.step(self.units[0])
        first = self.active()
        old_request = first['requests'][self.units[0]]['id']
        self.bags[self.units[0]].pop(rollout.PROGRESS)

        # This unit has queued a new configuration while its old generation
        # remains unfinished. It must still wait, and any repair must publish
        # the frozen generation's request identity rather than the queued one.
        self.configs[self.units[0]]['performance-profile'] = 'balanced'
        result = self.step(self.units[0])
        self.assertIsInstance(result, rollout.RollingWait)
        progress = json.loads(self.bags[self.units[0]][rollout.PROGRESS])
        self.assertEqual(progress['status'], 'done')
        self.assertEqual(progress['request'], old_request)
        self.assertNotEqual(
            self.databases[self.units[0]].get(rollout.LOCAL_REQUEST)['id'],
            old_request)


class MonitorTransportTestCase(unittest.TestCase):
    def test_monitor_command_uses_the_deployed_application_config(self):
        hookenv = Mock()
        hookenv.application_name.return_value = 'ceph-osd-hdd'
        with patch.object(rollout, 'hookenv', hookenv), \
                patch.object(rollout.subprocess, 'check_output',
                             return_value=b'') as command:
            rollout._monitor_command('get', 'marker')
        self.assertEqual(
            command.call_args[0][0][:3],
            ['ceph', '--conf', '/var/lib/charm/ceph-osd-hdd/ceph.conf'])

    @patch.object(rollout, '_monitor_command')
    def test_only_enoent_is_an_absent_marker(self, command):
        command.side_effect = subprocess.CalledProcessError(errno.ENOENT, [])
        self.assertIsNone(rollout._read_marker('key'))
        for code in (1, errno.EACCES, errno.ETIMEDOUT):
            command.side_effect = subprocess.CalledProcessError(code, [])
            self.assertRaises(subprocess.CalledProcessError,
                              rollout._read_marker, 'key')
