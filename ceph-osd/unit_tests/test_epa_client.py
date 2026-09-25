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
import socket
import tempfile
import threading
import unittest

import epa_client


class FakeEpaDaemon(object):
    """A minimal stand-in for the EPA orchestrator socket API.

    It mirrors the real daemon's framing: one request per connection,
    a single response, then close.
    """

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []
        self.directory = tempfile.mkdtemp()
        self.path = os.path.join(self.directory, 'epa.sock')
        self._server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server.bind(self.path)
        self._server.listen(1)
        self._thread = threading.Thread(target=self._serve)
        self._thread.daemon = True
        self._stopped = False
        self._thread.start()

    def _serve(self):
        while not self._stopped:
            try:
                conn, _ = self._server.accept()
            except OSError:
                return
            with conn:
                data = conn.recv(4096)
                if not data:
                    continue
                self.requests.append(json.loads(data.decode('UTF-8')))
                if self.responses:
                    response = self.responses.pop(0)
                else:
                    response = {'error': 'no canned response'}
                conn.sendall(json.dumps(response).encode('UTF-8'))

    def stop(self):
        self._stopped = True
        self._server.close()
        shutil.rmtree(self.directory, ignore_errors=True)


class ServiceNameTestCase(unittest.TestCase):

    def test_service_name_for(self):
        self.assertEqual(epa_client.service_name_for(3), 'ceph-osd.3')
        self.assertEqual(epa_client.service_name_for('3'), 'ceph-osd.3')
        self.assertEqual(epa_client.service_name_for('osd.3'), 'ceph-osd.3')

    def test_osd_id_for(self):
        self.assertEqual(epa_client.osd_id_for('ceph-osd.7'), '7')
        self.assertIsNone(epa_client.osd_id_for('nova-compute'))
        self.assertIsNone(epa_client.osd_id_for(None))


class EpaClientTestCase(unittest.TestCase):

    def daemon(self, responses):
        daemon = FakeEpaDaemon(responses)
        self.addCleanup(daemon.stop)
        client = epa_client.EpaClient(socket_path=daemon.path, timeout=5)
        return daemon, client

    def test_list_allocations(self):
        listing = {
            'version': '1.0',
            'supported_cpu_features': ['non-preemptive-allocations'],
            'cpu_pool': {'source': 'configured', 'eligible_cpus': '4-11'},
            'allocations': [
                {'service_name': 'ceph-osd.0', 'allocated_cores': '4-7',
                 'cores_count': 4, 'preemption_policy': 'non-preemptive',
                 'is_explicit': True},
            ],
        }
        daemon, client = self.daemon([listing])
        self.assertEqual(client.list_allocations(), listing)
        self.assertEqual(daemon.requests[0], {
            'version': '1.0',
            'action': 'list_allocations',
            'service_name': 'ceph-osd-charm',
        })

    def test_supports_non_preemptive(self):
        _, client = self.daemon([])
        self.assertTrue(client.supports_non_preemptive(
            {'supported_cpu_features': ['non-preemptive-allocations']}))
        self.assertFalse(client.supports_non_preemptive(
            {'supported_cpu_features': []}))
        self.assertFalse(client.supports_non_preemptive({}))

    def test_allocate_numa_cores(self):
        daemon, client = self.daemon([{
            'version': '1.0',
            'service_name': 'ceph-osd.1',
            'numa_node': 1,
            'num_of_cores': 4,
            'cores_allocated': '12-15',
            'preemption_policy': 'non-preemptive',
        }])
        self.assertEqual(client.allocate_numa_cores('ceph-osd.1', 1, 4),
                         [12, 13, 14, 15])
        self.assertEqual(daemon.requests[0], {
            'version': '1.0',
            'action': 'allocate_numa_cores',
            'service_name': 'ceph-osd.1',
            'numa_node': 1,
            'num_of_cores': 4,
            'preemption_policy': 'non-preemptive',
        })

    def test_allocate_cores(self):
        daemon, client = self.daemon([{
            'version': '1.0',
            'service_name': 'ceph-osd.1',
            'num_of_cores': 2,
            'cores_allocated': 2,
            'allocated_cores': '6,9',
            'preemption_policy': 'non-preemptive',
        }])
        self.assertEqual(client.allocate_cores('ceph-osd.1', 2), [6, 9])
        self.assertEqual(daemon.requests[0]['action'], 'allocate_cores')
        self.assertEqual(daemon.requests[0]['preemption_policy'],
                         'non-preemptive')

    def test_allocation_rejects_unconfirmed_policy(self):
        _, client = self.daemon([{
            'version': '1.0',
            'service_name': 'ceph-osd.1',
            'allocated_cores': '6,9',
        }])
        self.assertRaises(epa_client.EpaUnsupported,
                          client.allocate_cores, 'ceph-osd.1', 2)

    def test_allocation_error_response(self):
        _, client = self.daemon([
            {'version': '1.0', 'error': 'NUMA node 1 only has 3 eligible '
                                        'CPUs, but 12 were requested'},
        ])
        self.assertRaises(epa_client.EpaRequestError,
                          client.allocate_numa_cores, 'ceph-osd.1', 1, 12)

    def test_release(self):
        daemon, client = self.daemon([
            {'version': '1.0', 'service_name': 'ceph-osd.1',
             'num_of_cores': -1, 'cores_allocated': 0,
             'allocated_cores': ''},
            {'version': '1.0', 'service_name': 'ceph-osd.1', 'numa_node': 0,
             'num_of_cores': -1, 'cores_allocated': ''},
        ])
        client.release('ceph-osd.1')
        client.release_numa_node('ceph-osd.1', 0)
        self.assertEqual(daemon.requests[0]['num_of_cores'], -1)
        self.assertNotIn('preemption_policy', daemon.requests[0])
        self.assertEqual(daemon.requests[1]['numa_node'], 0)

    def test_wait_ready_returns_immediately(self):
        daemon, client = self.daemon([{'version': '1.0',
                                       'supported_cpu_features': []}])
        slept = []
        self.assertEqual(
            client.wait_ready(sleep=slept.append)['version'], '1.0')
        self.assertEqual(slept, [])

    def test_wait_ready_retries_until_the_daemon_binds(self):
        # A daemon that is still rebinding its socket refuses connections.
        client = epa_client.EpaClient(
            socket_path='/nonexistent/epa.sock', timeout=1)
        clock = [0]
        attempts = []

        def now():
            return clock[0]

        def sleep(interval):
            clock[0] += interval
            attempts.append(interval)

        self.assertRaises(epa_client.EpaUnavailable, client.wait_ready,
                          timeout=6, interval=2, now=now, sleep=sleep)
        self.assertEqual(attempts, [2, 2, 2])

    def test_socket_unavailable(self):
        client = epa_client.EpaClient(
            socket_path='/nonexistent/epa.sock', timeout=1)
        self.assertRaises(epa_client.EpaUnavailable, client.list_allocations)

    def test_malformed_response(self):
        directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, directory, True)
        path = os.path.join(directory, 'epa.sock')
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(path)
        server.listen(1)
        self.addCleanup(server.close)

        def serve():
            conn, _ = server.accept()
            with conn:
                conn.recv(4096)
                conn.sendall(b'not json')

        thread = threading.Thread(target=serve)
        thread.daemon = True
        thread.start()
        client = epa_client.EpaClient(socket_path=path, timeout=5)
        self.assertRaises(epa_client.EpaRequestError, client.list_allocations)


class ListingHelperTestCase(unittest.TestCase):

    LISTING = {
        'supported_cpu_features': ['non-preemptive-allocations'],
        'cpu_pool': {'source': 'configured', 'eligible_cpus': '4-11'},
        'allocations': [
            {'service_name': 'ceph-osd.0', 'allocated_cores': '4-5',
             'preemption_policy': 'non-preemptive', 'is_explicit': True},
            {'service_name': 'nova-compute', 'allocated_cores': '8-9',
             'preemption_policy': 'legacy', 'is_explicit': False},
        ],
    }

    def test_allocations_by_service(self):
        indexed = epa_client.allocations_by_service(self.LISTING)
        self.assertEqual(sorted(indexed), ['ceph-osd.0', 'nova-compute'])
        self.assertEqual(indexed['ceph-osd.0']['cores'], [4, 5])
        self.assertEqual(indexed['nova-compute']['preemption_policy'],
                         'legacy')

    def test_eligible_cpus_and_source(self):
        self.assertEqual(epa_client.eligible_cpus(self.LISTING),
                         [4, 5, 6, 7, 8, 9, 10, 11])
        self.assertEqual(epa_client.pool_source(self.LISTING), 'configured')
        self.assertEqual(epa_client.eligible_cpus({}), [])
        self.assertIsNone(epa_client.pool_source({}))

    def test_claimed_by_others(self):
        self.assertEqual(
            epa_client.claimed_by_others(self.LISTING, ['ceph-osd.0']),
            set([8, 9]))
        self.assertEqual(
            epa_client.claimed_by_others(self.LISTING, []),
            set([4, 5, 8, 9]))

    def test_free_cpus_excludes_foreign_owners(self):
        self.assertEqual(
            epa_client.free_cpus_for(self.LISTING, ['ceph-osd.0']),
            set([4, 5, 6, 7, 10, 11]))

    def test_free_cpus_without_own_services(self):
        self.assertEqual(
            epa_client.free_cpus_for(self.LISTING, []),
            set([6, 7, 10, 11]))
