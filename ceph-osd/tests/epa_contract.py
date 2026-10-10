#!/usr/bin/env python3
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

"""Exercise the charm client against an EPA source checkout over a Unix socket.

Run with the upstream checkout on PYTHONPATH and pydantic >= 2 installed.
Only topology is simulated; dispatch, allocation and persistence are upstream
code. All sockets and state files live in temporary directories.
"""

import json
import os
from pathlib import Path
import socketserver
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'hooks'))
import epa_client


class EpaContractTest(unittest.TestCase):

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        env = patch.dict(os.environ, SNAP_DATA=directory.name)
        env.start()
        self.addCleanup(env.stop)
        # Import only after redirecting even the upstream singleton's state.
        from epa_orchestrator import allocations_db, cpu_pool, daemon_handler
        from epa_orchestrator.state_store import StateStore
        self.cpu_pool = cpu_pool
        self.handler = daemon_handler
        self.db = allocations_db
        allocations_db.allocations_db._state_store = StateStore()
        allocations_db.allocations_db.clear_all_allocations()
        self.online = self.root / 'online'
        self.online.write_text('0-15')
        isolated = self.root / 'isolated'
        isolated.write_text('8-11')
        present = self.root / 'present'
        present.write_text('0-15')
        topology = {0: {0, 1, 2, 3, 8, 9}, 1: {4, 5, 6, 7, 10, 11}}
        for target, value in (
                ('epa_orchestrator.cpu_pool.ONLINE_CPUS_PATH',
                 str(self.online)),
                ('epa_orchestrator.cpu_pinning.ISOLATED_CPUS_PATH',
                 str(isolated)),
                ('epa_orchestrator.cpu_pinning.PRESENT_CPUS_PATH',
                 str(present)),
                ('epa_orchestrator.utils.get_numa_node_cpus',
                 lambda: topology),
                ('epa_orchestrator.daemon_handler.get_numa_node_cpus',
                 lambda: topology),
                ('epa_orchestrator.allocations_db.get_thread_siblings_map',
                 lambda cpus: {cpu: {cpu} for cpu in cpus})):
            patcher = patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.pools = cpu_pool.CpuPools('2-5')
        self.requests = []
        owner = self

        class Handler(socketserver.BaseRequestHandler):
            def handle(self):
                payload = self.request.recv(65536)
                owner.requests.append(json.loads(payload))
                self.request.sendall(daemon_handler.handle_daemon_request(
                    payload, owner.pools))

        path = str(self.root / 'epa.sock')
        server = socketserver.UnixStreamServer(path, Handler)
        thread = threading.Thread(target=server.serve_forever,
                                  kwargs={'poll_interval': 0.01}, daemon=True)
        thread.start()

        def stop():
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()

        self.addCleanup(stop)
        self.client = epa_client.EpaClient(socket_path=path, timeout=5)

    def legacy_request(self, **fields):
        payload = dict(version='1.0', service_name='nova', **fields)
        return json.loads(self.handler.handle_daemon_request(
            json.dumps(payload).encode(), self.pools))

    def test_claim_resize_restart_and_release_preserve_isolated_owner(self):
        legacy = self.legacy_request(action='allocate_cores', num_of_cores=2)
        self.assertEqual(legacy['allocated_cores'], '8-9')
        self.assertEqual(self.client.allocate_numa_cores('ceph-osd.0', 0, 2),
                         [2, 3])
        self.assertEqual(self.client.allocate_numa_cores('ceph-osd.0', 1, 2),
                         [4, 5])
        self.client.release_numa_node('ceph-osd.0', 0)
        self.assertEqual(epa_client.allocations_by_service(
            self.client.list_allocations())['ceph-osd.0']['cores'], [4, 5])
        # Reload both daemon configuration and the persisted allocation DB.
        self.pools = self.cpu_pool.CpuPools('2-5')
        with patch.object(self.handler, 'allocations_db',
                          self.db.AllocationsDB()):
            self.assertEqual(self.client.allocate_cores('ceph-osd.0', 1), [4])
            self.client.release('ceph-osd.0')
        self.assertEqual(self.client.list_allocations()['allocations'], [])
        listing = self.legacy_request(action='list_allocations')
        self.assertEqual(listing['allocations'][0]['allocated_cores'], '8-9')
        self.assertNotIn('pool', self.requests[0])  # capability discovery
        self.assertTrue(all(r['pool'] == 'general' for r in self.requests[1:]))

    def test_bootstrap_discovers_before_general_pool_exists(self):
        self.pools = self.cpu_pool.CpuPools()
        self.assertEqual(self.client.wait_ready(discover=True)['pool'],
                         'isolated')
        with self.assertRaises(epa_client.EpaRequestError):
            self.client.list_allocations()
        self.pools = self.cpu_pool.CpuPools('2-5')
        self.assertEqual(epa_client.eligible_cpus(self.client.wait_ready()),
                         [2, 3, 4, 5])

    def test_oversized_request_preserves_foreign_general_claim(self):
        self.client.allocate_cores('other-owner', 3)
        before = self.client.list_allocations()
        with self.assertRaises(epa_client.EpaRequestError):
            self.client.allocate_cores('ceph-osd.0', 2)
        self.assertEqual(self.client.list_allocations(), before)

    def test_release_survives_unreadable_online_topology(self):
        self.client.allocate_cores('ceph-osd.0', 2)
        self.online.unlink()
        self.assertEqual(epa_client.eligible_cpus(
            self.client.list_allocations()), [])
        self.client.release('ceph-osd.0')
        self.assertEqual(self.client.list_allocations()['allocations'], [])

    def test_failed_commit_is_not_a_successful_claim(self):
        self.client.discover()
        with patch('epa_orchestrator.state_store.os.replace',
                   side_effect=OSError('injected persistence failure')):
            with self.assertRaises(epa_client.EpaRequestError):
                self.client.allocate_cores('ceph-osd.0', 2)
        self.assertEqual(self.client.list_allocations()['allocations'], [])


if __name__ == '__main__':
    unittest.main()
