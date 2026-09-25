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

import os
import shutil
import subprocess
import tempfile
import unittest

from unittest.mock import patch

import epa_snap


def failure(command, output=b'boom'):
    return subprocess.CalledProcessError(1, command, output=output)


class InstallTestCase(unittest.TestCase):

    def setUp(self):
        super(InstallTestCase, self).setUp()
        patcher = patch.object(epa_snap, '_run')
        self.run = patcher.start()
        self.addCleanup(patcher.stop)
        self.directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.directory, True)

    def resource(self, size=1024):
        path = os.path.join(self.directory, 'epa-orchestrator.snap')
        with open(path, 'wb') as handle:
            handle.write(b'x' * size)
        return path

    def test_is_installed(self):
        self.assertTrue(epa_snap.is_installed())
        self.run.side_effect = failure(['snap', 'list'])
        self.assertFalse(epa_snap.is_installed())

    def test_is_installed_without_snapd(self):
        self.run.side_effect = OSError('no snap command')
        self.assertRaises(epa_snap.SnapError, epa_snap.is_installed)

    def test_existing_installation_is_left_alone(self):
        self.assertEqual(epa_snap.ensure_installed(self.resource()),
                         'present')
        self.assertEqual(self.run.call_args_list[0][0][0],
                         ['snap', 'list', 'epa-orchestrator'])
        self.assertEqual(len(self.run.call_args_list), 1)

    def test_install_from_resource(self):
        path = self.resource()
        self.run.side_effect = [failure(['snap', 'list']), '']
        self.assertEqual(epa_snap.ensure_installed(path), 'resource')
        self.assertEqual(self.run.call_args_list[-1][0][0],
                         ['snap', 'install', '--dangerous', path])

    def test_install_from_resource_retries_with_devmode(self):
        path = self.resource()
        self.run.side_effect = [
            failure(['snap', 'list']),
            failure(['snap', 'install']),
            '',
        ]
        self.assertEqual(epa_snap.ensure_installed(path), 'resource')
        self.assertEqual(
            self.run.call_args_list[-1][0][0],
            ['snap', 'install', '--dangerous', '--devmode', path])

    def test_empty_resource_falls_back_to_the_store(self):
        path = self.resource(size=0)
        self.run.side_effect = [failure(['snap', 'list']), '']
        self.assertEqual(
            epa_snap.ensure_installed(path, channel='latest/edge'), 'store')
        self.assertEqual(
            self.run.call_args_list[-1][0][0],
            ['snap', 'install', 'epa-orchestrator', '--channel',
             'latest/edge'])

    def test_missing_resource_falls_back_to_the_store(self):
        self.run.side_effect = [failure(['snap', 'list']), '']
        self.assertEqual(epa_snap.ensure_installed(None), 'store')

    def test_failed_resource_install_falls_back_to_the_store(self):
        path = self.resource()
        self.run.side_effect = [
            failure(['snap', 'list']),
            failure(['snap', 'install']),
            failure(['snap', 'install']),
            '',
        ]
        self.assertEqual(epa_snap.ensure_installed(path), 'store')

    def test_install_failure_raises(self):
        self.run.side_effect = [
            failure(['snap', 'list']),
            failure(['snap', 'install'], output=b'no such snap'),
        ]
        with self.assertRaises(epa_snap.SnapError) as caught:
            epa_snap.ensure_installed(None)
        self.assertIn('no such snap', str(caught.exception))

    def test_usable_resource(self):
        self.assertTrue(epa_snap.usable_resource(self.resource()))
        self.assertFalse(epa_snap.usable_resource(self.resource(size=0)))
        self.assertFalse(epa_snap.usable_resource(None))
        self.assertFalse(epa_snap.usable_resource('/nonexistent'))


class PoolConfigurationTestCase(unittest.TestCase):

    def setUp(self):
        super(PoolConfigurationTestCase, self).setUp()
        patcher = patch.object(epa_snap, '_run')
        self.run = patcher.start()
        self.addCleanup(patcher.stop)

    def test_get_configured_pool(self):
        self.run.return_value = '2-15\n'
        self.assertEqual(epa_snap.get_configured_pool(), '2-15')

    def test_get_configured_pool_unset(self):
        self.run.side_effect = failure(['snap', 'get'])
        self.assertIsNone(epa_snap.get_configured_pool())

    def test_get_configured_pool_empty(self):
        self.run.return_value = '\n'
        self.assertIsNone(epa_snap.get_configured_pool())

    def test_set_pool_restarts_the_daemon(self):
        with patch.object(epa_snap, 'EpaClient') as client:
            self.assertEqual(epa_snap.set_pool([4, 5, 6, 8]), '4-6,8')
        client.return_value.wait_ready.assert_called_once_with()
        self.assertEqual(
            [call[0][0] for call in self.run.call_args_list],
            [['snap', 'set', 'epa-orchestrator', 'cpu-pool=4-6,8'],
             ['snap', 'restart', 'epa-orchestrator.daemon']])

    def test_set_pool_fails_when_the_daemon_stays_down(self):
        with patch.object(epa_snap, 'EpaClient') as client:
            client.return_value.wait_ready.side_effect = (
                epa_snap.EpaUnavailable('socket missing'))
            with self.assertRaises(epa_snap.SnapError) as caught:
                epa_snap.set_pool([4])
        self.assertIn('did not come back', str(caught.exception))

    def test_set_pool_rejects_empty(self):
        self.assertRaises(epa_snap.SnapError, epa_snap.set_pool, [])

    def test_set_pool_failure(self):
        self.run.side_effect = failure(['snap', 'set'], output=b'rejected')
        with self.assertRaises(epa_snap.SnapError) as caught:
            epa_snap.set_pool([1])
        self.assertIn('rejected', str(caught.exception))


class PoolComputationTestCase(unittest.TestCase):
    """CPU pool sizing.

    All tests use a fixture-free topology, so ``physical_cores`` finds no
    SMT topology and treats every logical CPU as its own core unless the
    test patches it.
    """

    def setUp(self):
        super(PoolComputationTestCase, self).setUp()
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)

    def test_conservative_default_share(self):
        # 20% of 16 CPUs per node, taken from the top, skipping CPU 0.
        nodes = {0: list(range(0, 16)), 1: list(range(16, 32))}
        pool = epa_snap.compute_pool(nodes, root=self.root)
        self.assertEqual(len(pool), 8)
        self.assertEqual(pool, [12, 13, 14, 15, 28, 29, 30, 31])

    def test_demand_grows_the_pool(self):
        nodes = {0: list(range(0, 16)), 1: list(range(16, 32))}
        pool = epa_snap.compute_pool(
            nodes, demand_by_node={0: 10}, root=self.root)
        self.assertEqual(len(set(pool) & set(nodes[0])), 10)
        self.assertEqual(len(set(pool) & set(nodes[1])), 4)

    def test_demand_is_capped(self):
        nodes = {0: list(range(0, 16))}
        pool = epa_snap.compute_pool(
            nodes, demand_by_node={0: 100}, root=self.root)
        # 75% of 16 CPUs.
        self.assertEqual(len(pool), 12)

    def test_unbound_demand_is_spread_across_nodes(self):
        nodes = {0: list(range(0, 16)), 1: list(range(16, 32))}
        pool = epa_snap.compute_pool(
            nodes, unbound_demand=14, root=self.root)
        self.assertEqual(len(set(pool) & set(nodes[0])), 7)
        self.assertEqual(len(set(pool) & set(nodes[1])), 7)

    def test_cpu_zero_core_is_never_offered(self):
        nodes = {0: [0, 1, 2, 3]}
        pool = epa_snap.compute_pool(
            nodes, demand_by_node={0: 4}, root=self.root)
        self.assertNotIn(0, pool)

    def test_whole_physical_cores_are_preferred(self):
        nodes = {0: [0, 1, 2, 3, 4, 5, 6, 7]}
        siblings = {0: [0, 4], 1: [1, 5], 2: [2, 6], 3: [3, 7],
                    4: [0, 4], 5: [1, 5], 6: [2, 6], 7: [3, 7]}
        with patch.object(epa_snap, 'physical_cores') as cores:
            cores.return_value = [[0, 4], [1, 5], [2, 6], [3, 7]]
            pool = epa_snap.compute_pool(
                nodes, demand_by_node={0: 2}, root=self.root)
        self.assertEqual(pool, [3, 7])
        self.assertEqual(sorted(siblings), sorted(nodes[0]))

    def test_empty_node_is_skipped(self):
        pool = epa_snap.compute_pool(
            {0: [], 1: [8, 9, 10, 11]}, root=self.root)
        self.assertEqual(pool, [11])

    def test_grow_pool_from_unset(self):
        merged, changed = epa_snap.grow_pool(None, [4, 5])
        self.assertEqual((merged, changed), ([4, 5], True))

    def test_grow_pool_no_change(self):
        merged, changed = epa_snap.grow_pool('4-5', [4, 5])
        self.assertEqual((merged, changed), ([4, 5], False))

    def test_grow_pool_never_shrinks(self):
        merged, changed = epa_snap.grow_pool('4-7', [4, 5])
        self.assertEqual((merged, changed), ([4, 5, 6, 7], False))

    def test_grow_pool_extends(self):
        merged, changed = epa_snap.grow_pool('4-5', [6, 7])
        self.assertEqual((merged, changed), ([4, 5, 6, 7], True))
