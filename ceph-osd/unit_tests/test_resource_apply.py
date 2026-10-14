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

import json
import os
import shutil
import subprocess
import tempfile
import unittest

from unittest.mock import patch, MagicMock

import resource_apply


class DropinTestCase(unittest.TestCase):

    def setUp(self):
        super(DropinTestCase, self).setUp()
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)
        patcher = patch.object(
            resource_apply, 'DROPIN_DIR',
            os.path.join(self.root, 'ceph-osd@{osd_id}.service.d'))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_render_dropin_with_numa_policy(self):
        content = resource_apply.render_dropin(
            'performance', [4, 5, 6, 7], numa_policy='bind', numa_node=1)
        self.assertIn('performance-profile=performance', content)
        self.assertIn('[Service]\n', content)
        self.assertIn('CPUAffinity=4-7\n', content)
        self.assertIn('NUMAPolicy=bind\n', content)
        self.assertIn('NUMAMask=1\n', content)

    def test_render_dropin_without_numa_policy(self):
        content = resource_apply.render_dropin('minimal', [2, 9])
        self.assertIn('CPUAffinity=2,9\n', content)
        self.assertNotIn('NUMAPolicy', content)

    @patch.object(resource_apply, '_application_name',
                  return_value='ceph-osd-hdd')
    def test_render_dropin_selects_the_application_config(self, app_name):
        content = resource_apply.render_dropin('minimal', [2, 9])
        self.assertIn(
            'Environment="CEPH_CONF=/var/lib/charm/ceph-osd-hdd/ceph.conf"',
            content)
        app_name.assert_called_once_with()
        self.assertNotIn('NUMAMask', content)

    def test_render_dropin_accepts_a_range_string(self):
        # State records CPU sets as range strings.
        content = resource_apply.render_dropin('balanced', '4-7')
        self.assertIn('CPUAffinity=4-7\n', content)

    def test_write_read_and_remove(self):
        content = resource_apply.render_dropin('minimal', [1, 2])
        self.assertTrue(resource_apply.write_dropin('3', content))
        self.assertEqual(resource_apply.read_dropin('3'), content)
        self.assertEqual(
            oct(os.stat(resource_apply.dropin_path('3')).st_mode & 0o777),
            oct(0o644))
        # Writing the same content again is a no-op.
        self.assertFalse(resource_apply.write_dropin('3', content))
        self.assertTrue(resource_apply.remove_dropin('3'))
        self.assertIsNone(resource_apply.read_dropin('3'))
        self.assertFalse(resource_apply.remove_dropin('3'))

    def test_remove_keeps_directory_with_other_dropins(self):
        resource_apply.write_dropin('3', 'x')
        other = os.path.join(
            os.path.dirname(resource_apply.dropin_path('3')), '99-other.conf')
        with open(other, 'w') as handle:
            handle.write('unrelated')
        self.assertTrue(resource_apply.remove_dropin('3'))
        self.assertTrue(os.path.exists(other))

    def test_write_updates_changed_content(self):
        resource_apply.write_dropin('3', 'first')
        self.assertTrue(resource_apply.write_dropin('3', 'second'))
        self.assertEqual(resource_apply.read_dropin('3'), 'second')


class EnforcementVerificationTestCase(unittest.TestCase):

    @patch.object(resource_apply, '_thread_cpu_affinities',
                  return_value={'10': '4-5', '11': '6-7'})
    @patch.object(resource_apply, '_unit_main_pid', return_value=123)
    @patch.object(resource_apply, '_daemon_config_value')
    def test_verify_enforcement_checks_ceph_settings_and_every_thread(
            self, config_value, main_pid, affinities):
        config_value.side_effect = ['false', '-1']
        resource_apply.verify_enforcement('0', [4, 5, 6, 7])
        main_pid.assert_called_once_with('0')
        affinities.assert_called_once_with(123)

    @patch.object(resource_apply, '_thread_cpu_affinities',
                  return_value={'10': '0-15'})
    @patch.object(resource_apply, '_unit_main_pid', return_value=123)
    @patch.object(resource_apply, '_daemon_config_value')
    def test_verify_enforcement_rejects_numa_or_thread_widening(
            self, config_value, main_pid, affinities):
        config_value.side_effect = ['false', '-1']
        self.assertRaises(
            resource_apply.ApplyError,
            resource_apply.verify_enforcement, '0', [4, 5, 6, 7])
        affinities.assert_called_once_with(123)


class ReadinessTestCase(unittest.TestCase):

    def setUp(self):
        super(ReadinessTestCase, self).setUp()
        self.slept = []

    def sleep(self, interval):
        self.slept.append(interval)

    @patch.object(resource_apply.subprocess, 'check_output')
    @patch.object(resource_apply.subprocess, 'call')
    def test_wait_for_osd_ready(self, call, check_output):
        call.return_value = 0
        check_output.return_value = json.dumps({'state': 'active'}).encode()
        self.assertTrue(resource_apply.wait_for_osd(
            '1', timeout=30, interval=1, sleep=self.sleep))
        self.assertEqual(self.slept, [])

    @patch.object(resource_apply.subprocess, 'check_output')
    @patch.object(resource_apply.subprocess, 'call')
    def test_wait_for_osd_becomes_ready(self, call, check_output):
        call.return_value = 0
        check_output.side_effect = [
            json.dumps({'state': 'booting'}).encode(),
            json.dumps({'state': 'active'}).encode(),
        ]
        clock = [0]

        def now():
            return clock[0]

        def sleep(interval):
            clock[0] += interval
            self.slept.append(interval)

        self.assertTrue(resource_apply.wait_for_osd(
            '1', timeout=30, interval=5, now=now, sleep=sleep))
        self.assertEqual(self.slept, [5])

    @patch.object(resource_apply.subprocess, 'check_output')
    @patch.object(resource_apply.subprocess, 'call')
    def test_wait_for_osd_times_out(self, call, check_output):
        call.return_value = 0
        check_output.return_value = json.dumps({'state': 'booting'}).encode()
        clock = [0]

        def now():
            return clock[0]

        def sleep(interval):
            clock[0] += interval

        self.assertFalse(resource_apply.wait_for_osd(
            '1', timeout=10, interval=5, now=now, sleep=sleep))

    @patch.object(resource_apply.subprocess, 'check_output')
    @patch.object(resource_apply.subprocess, 'call')
    def test_wait_for_osd_unit_inactive(self, call, check_output):
        call.return_value = 3
        clock = [0]

        def now():
            return clock[0]

        def sleep(interval):
            clock[0] += interval

        self.assertFalse(resource_apply.wait_for_osd(
            '1', timeout=5, interval=5, now=now, sleep=sleep))
        self.assertFalse(check_output.called)

    @patch.object(resource_apply.subprocess, 'check_output')
    @patch.object(resource_apply.subprocess, 'call')
    def test_wait_for_osd_admin_socket_failure(self, call, check_output):
        call.return_value = 0
        check_output.side_effect = subprocess.CalledProcessError(1, 'ceph')
        clock = [0]

        def now():
            return clock[0]

        def sleep(interval):
            clock[0] += interval

        self.assertFalse(resource_apply.wait_for_osd(
            '1', timeout=5, interval=5, now=now, sleep=sleep))


class ApplyTestCase(unittest.TestCase):

    def setUp(self):
        super(ApplyTestCase, self).setUp()
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)
        for name, replacement in (
                ('DROPIN_DIR',
                 os.path.join(self.root, 'ceph-osd@{osd_id}.service.d')),):
            patcher = patch.object(resource_apply, name, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.calls = []
        self.pending = {}
        database = MagicMock()
        database.get.side_effect = lambda key: self.pending.get(key)
        database.set.side_effect = self.pending.__setitem__
        patcher = patch.object(resource_apply, 'kv', return_value=database)
        patcher.start()
        self.addCleanup(patcher.stop)
        for name in ('daemon_reload',):
            patcher = patch.object(resource_apply, name)
            mock = patcher.start()
            mock.side_effect = lambda: self.calls.append('daemon-reload')
            self.addCleanup(patcher.stop)
        patcher = patch.object(resource_apply, 'service_restart')
        self.service_restart = patcher.start()
        self.service_restart.side_effect = (
            lambda unit: self.calls.append(unit) or True)
        self.addCleanup(patcher.stop)
        patcher = patch.object(resource_apply, 'wait_for_osd')
        self.wait_for_osd = patcher.start()
        self.wait_for_osd.return_value = True
        self.addCleanup(patcher.stop)
        patcher = patch.object(resource_apply, 'verify_enforcement')
        self.verify_enforcement = patcher.start()
        self.addCleanup(patcher.stop)

    def allocation(self, osd_id, cores, node=None, policy=None):
        return {'osd-id': osd_id, 'cores': cores, 'numa-node': node,
                'numa-policy': policy}

    def test_apply_writes_and_restarts_sequentially(self):
        restarted = resource_apply.apply_allocations('performance', [
            self.allocation('0', [4, 5], node=0, policy='bind'),
            self.allocation('1', [6, 7], node=0, policy='bind'),
        ])
        self.assertEqual(restarted, ['0', '1'])
        self.assertEqual(self.calls, ['daemon-reload', 'ceph-osd@0',
                                      'ceph-osd@1'])
        self.assertIn('NUMAMask=0',
                      resource_apply.read_dropin('0'))
        self.verify_enforcement.assert_has_calls([
            unittest.mock.call('0', [4, 5]),
            unittest.mock.call('1', [6, 7]),
        ])

    def test_apply_is_idempotent(self):
        allocations = [self.allocation('0', [4, 5])]
        resource_apply.apply_allocations('minimal', allocations)
        self.calls = []
        self.assertEqual(
            resource_apply.apply_allocations('minimal', allocations), [])
        self.assertEqual(self.calls, [])

    def test_apply_only_restarts_changed_osds(self):
        resource_apply.apply_allocations('minimal', [
            self.allocation('0', [4, 5]),
            self.allocation('1', [6, 7]),
        ])
        self.calls = []
        restarted = resource_apply.apply_allocations('minimal', [
            self.allocation('0', [4, 5]),
            self.allocation('1', [8, 9]),
        ])
        self.assertEqual(restarted, ['1'])
        self.assertEqual(self.calls, ['daemon-reload', 'ceph-osd@1'])

    def test_apply_raises_when_an_osd_does_not_come_back(self):
        self.wait_for_osd.return_value = False
        self.assertRaises(
            resource_apply.ApplyError,
            resource_apply.apply_allocations,
            'performance', [self.allocation('0', [4, 5])])

    def test_apply_stops_at_the_first_failed_restart(self):
        self.wait_for_osd.side_effect = [False, True]
        self.assertRaises(
            resource_apply.ApplyError,
            resource_apply.apply_allocations, 'minimal',
            [self.allocation('0', [4]), self.allocation('1', [5])])
        self.assertEqual(self.calls, ['daemon-reload', 'ceph-osd@0'])

    def test_clear_allocation(self):
        resource_apply.apply_allocations(
            'minimal', [self.allocation('0', [4, 5])])
        self.calls = []
        self.assertTrue(resource_apply.clear_allocation('0'))
        self.assertIsNone(resource_apply.read_dropin('0'))
        self.assertEqual(self.calls, ['daemon-reload'])
        self.assertFalse(resource_apply.clear_allocation('0'))

    def test_clear_reload_failure_is_retried_without_restart(self):
        # Use the real file removal path: daemon-reload can fail after the
        # drop-in is gone, so retry must still reload systemd before EPA can
        # release the CPUs.
        resource_apply.write_dropin('0', 'CPUAllocation')
        self.service_restart.reset_mock()
        with patch.object(resource_apply, 'daemon_reload') as reload:
            reload.side_effect = subprocess.CalledProcessError(
                1, 'systemctl daemon-reload')
            self.assertRaises(resource_apply.ApplyError,
                              resource_apply.clear_allocation, '0')
            self.assertIsNone(resource_apply.read_dropin('0'))
            self.assertEqual(resource_apply.pending_clears(), {'0'})
            self.assertEqual(resource_apply.pending_restarts(), set())
            self.service_restart.assert_not_called()

            reload.side_effect = None
            self.assertTrue(resource_apply.clear_allocation('0'))
            self.assertEqual(reload.call_count, 2)
        self.assertEqual(resource_apply.pending_clears(), set())
        self.assertEqual(resource_apply.pending_restarts(), set())
        self.service_restart.assert_not_called()

    def test_clear_allocation_with_restart(self):
        resource_apply.apply_allocations(
            'minimal', [self.allocation('0', [4, 5])])
        self.calls = []
        self.assertTrue(resource_apply.clear_allocation('0', restart=True))
        self.assertEqual(self.calls, ['daemon-reload', 'ceph-osd@0'])

    def test_clear_allocation_propagates_restart_failure(self):
        resource_apply.apply_allocations(
            'minimal', [self.allocation('0', [4, 5])])
        self.wait_for_osd.return_value = False
        self.assertRaises(resource_apply.ApplyError,
                          resource_apply.clear_allocation, '0', restart=True)
        self.assertEqual(resource_apply.pending_restarts(), {'0'})

    def test_failed_start_is_immediately_reported(self):
        self.service_restart.side_effect = None
        self.service_restart.return_value = False
        self.assertRaises(resource_apply.ApplyError,
                          resource_apply.restart_osd, '0')
        self.wait_for_osd.assert_not_called()

    def test_retry_restarts_identical_dropins_after_partial_failure(self):
        allocations = [self.allocation('0', [4]), self.allocation('1', [5])]
        self.wait_for_osd.side_effect = [True, False]
        self.assertRaises(resource_apply.ApplyError,
                          resource_apply.apply_allocations,
                          'minimal', allocations)
        self.assertEqual(resource_apply.pending_restarts(), {'1'})
        self.calls = []
        self.wait_for_osd.side_effect = None
        self.wait_for_osd.return_value = True
        self.assertEqual(resource_apply.apply_allocations(
            'minimal', allocations), ['1'])
        self.assertEqual(self.calls, ['daemon-reload', 'ceph-osd@1'])
        self.assertEqual(resource_apply.pending_restarts(), set())

    def test_failed_unpin_is_retried_even_without_dropin(self):
        resource_apply.apply_allocations(
            'minimal', [self.allocation('0', [4, 5])])
        self.wait_for_osd.return_value = False
        self.assertRaises(resource_apply.ApplyError,
                          resource_apply.clear_allocation, '0', restart=True)
        self.assertIsNone(resource_apply.read_dropin('0'))
        self.wait_for_osd.return_value = True
        self.assertTrue(resource_apply.clear_allocation('0', restart=True))
        self.assertFalse(resource_apply.pending_restarts())
