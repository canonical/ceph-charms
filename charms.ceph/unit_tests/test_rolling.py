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

import unittest
from unittest.mock import Mock

from charms_ceph.rolling import (
    RollingError, RollingOperation, RollingWait, position,
)


class RollingTestCase(unittest.TestCase):
    def setUp(self):
        self.state = {}
        self.time = 10
        self.operation = RollingOperation(
            'osd-resource', 'generation-1', self.state.get,
            self.state.__setitem__, now=lambda: self.time)
        self.dog = Mock()
        self.cohort = ['host0', 'host1', 'host2']

    def test_host_order_and_noop_handoff(self):
        self.assertTrue(self.operation.check_turn(self.cohort, 'host0', 0))
        with self.assertRaises(RollingWait):
            self.operation.check_turn(self.cohort, 'host1', 0)
        result = self.operation.run('host0', lambda kick: [], self.dog)
        self.assertEqual(result, [])
        self.assertFalse(self.operation.check_turn(self.cohort, 'host0', 0))
        self.assertTrue(self.operation.check_turn(self.cohort, 'host1', 0))
        self.assertEqual(self.state[self.operation.key('host0', 'start')], 10)
        self.assertEqual(self.state[self.operation.key('host0', 'done')], 10)

    def test_failure_stops_successor(self):
        def fail(kick):
            raise ValueError('OSD did not recover')
        with self.assertRaises(ValueError):
            self.operation.run('host0', fail, self.dog)
        self.assertFalse(self.operation.completed('host0'))
        with self.assertRaisesRegex(RollingError, 'OSD did not recover'):
            self.operation.check_turn(self.cohort, 'host1', 0)
        self.time = 10000
        with self.assertRaises(RollingError):
            self.operation.check_turn(self.cohort, 'host1', 0)

    def test_timeout_never_grants_a_turn(self):
        self.time = 1800
        with self.assertRaisesRegex(RollingError, 'timed out'):
            self.operation.check_turn(self.cohort, 'host1', 0)
        self.assertNotIn(self.operation.key('host1', 'start'), self.state)

    def test_start_and_heartbeat_are_not_completion(self):
        self.state[self.operation.key('host0', 'start')] = 5
        self.state[self.operation.key('host0', 'alive')] = 10
        with self.assertRaises(RollingWait):
            self.operation.check_turn(self.cohort, 'host1', 0)

    def test_checks_all_predecessors(self):
        self.state[self.operation.key('host1', 'done')] = 10
        with self.assertRaises(RollingWait):
            self.operation.check_turn(self.cohort, 'host2', 0)

    def test_new_generation_cannot_reuse_completion(self):
        self.operation.run('host0', lambda kick: None, self.dog)
        another = RollingOperation('osd-resource', 'generation-2',
                                   self.state.get, self.state.__setitem__)
        with self.assertRaises(RollingWait):
            another.check_turn(self.cohort, 'host1', another.now())

    def test_read_errors_fail_closed(self):
        self.operation.read = Mock(side_effect=OSError('monitor unreachable'))
        with self.assertRaises(OSError):
            self.operation.check_turn(self.cohort, 'host0', 0)

    def test_failed_completion_write_does_not_admit_successor(self):
        def write(key, value):
            if key.endswith('_done'):
                raise OSError('lost monitor connection')
            self.state[key] = value
        self.operation.write = write
        with self.assertRaises(OSError):
            self.operation.run('host0', lambda kick: None, self.dog)
        with self.assertRaises(RollingWait):
            self.operation.check_turn(self.cohort, 'host1', 0)

    def test_successful_owner_retry_unblocks_handoff(self):
        self.state[self.operation.key('host0', 'failed')] = 'old failure'
        self.operation.run('host0', lambda kick: None, self.dog)
        self.assertTrue(self.operation.check_turn(self.cohort, 'host1', 0))

    def test_invalid_participants_fail_closed(self):
        with self.assertRaises(ValueError):
            position(['host0', 'host0'], 'host0')
        with self.assertRaises(ValueError):
            position(['host0'], 'not-a-participant')
