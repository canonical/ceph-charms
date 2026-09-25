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

"""Client for the EPA orchestrator CPU allocation API.

The EPA orchestrator performs *bookkeeping only*: it records which
logical CPUs an owner holds, and never applies CPU affinity, moves
processes or activates kernel isolation.  Applying the returned CPU IDs
is the caller's responsibility.

Every allocation this charm makes uses the ``non-preemptive`` ownership
policy, which is only sent after the daemon has advertised support for
it (per the EPA integration contract).
"""

import json
import socket
import time

from host_topology import parse_cpu_list

API_VERSION = '1.0'
SOCKET_PATH = '/var/snap/epa-orchestrator/current/data/epa.sock'

# The daemon rebinds its socket on (re)start, so it can refuse
# connections for a moment after snapd reports the service as started.
READY_TIMEOUT = 120
READY_INTERVAL = 2

POLICY_NON_PREEMPTIVE = 'non-preemptive'
FEATURE_NON_PREEMPTIVE = 'non-preemptive-allocations'

# Full release of every claim a service owns.
RELEASE_ALL = -1

SERVICE_PREFIX = 'ceph-osd.'


def service_name_for(osd_id):
    """Return the EPA service identity for a local OSD.

    :param osd_id: OSD ID, with or without the ``osd.`` prefix
    :type osd_id: str or int
    :rtype: str
    """
    text = str(osd_id)
    if text.startswith('osd.'):
        text = text[len('osd.'):]
    return '{}{}'.format(SERVICE_PREFIX, text)


def osd_id_for(service):
    """Return the OSD ID owning an EPA service name, if any.

    :rtype: str or None
    """
    if service and service.startswith(SERVICE_PREFIX):
        return service[len(SERVICE_PREFIX):]
    return None


class EpaError(Exception):
    """Base class for EPA orchestrator failures."""


class EpaUnavailable(EpaError):
    """The EPA orchestrator socket could not be reached."""


class EpaRequestError(EpaError):
    """The EPA orchestrator refused a request."""


class EpaUnsupported(EpaError):
    """The EPA orchestrator lacks a capability the charm requires."""


class EpaClient(object):
    """Minimal synchronous client for the EPA orchestrator socket."""

    def __init__(self, socket_path=SOCKET_PATH, timeout=30):
        self.socket_path = socket_path
        self.timeout = timeout

    def _request(self, payload):
        """Send one JSON request and return the decoded response.

        :param payload: request body, ``version`` is added automatically
        :type payload: dict
        :rtype: dict
        :raises EpaUnavailable: the daemon socket is not reachable
        :raises EpaRequestError: the daemon returned an error
        """
        body = dict(payload)
        body['version'] = API_VERSION
        encoded = json.dumps(body).encode('UTF-8')
        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(self.timeout)
            try:
                sock.connect(self.socket_path)
                sock.sendall(encoded)
                chunks = []
                while True:
                    chunk = sock.recv(65536)
                    if not chunk:
                        break
                    chunks.append(chunk)
            finally:
                sock.close()
        except (socket.error, OSError) as exc:
            raise EpaUnavailable(
                'EPA orchestrator socket {} unavailable: {}'.format(
                    self.socket_path, exc))

        raw = b''.join(chunks)
        if not raw:
            raise EpaUnavailable(
                'EPA orchestrator returned an empty response')
        try:
            response = json.loads(raw.decode('UTF-8'))
        except ValueError as exc:
            raise EpaRequestError(
                'malformed EPA response: {}'.format(exc))
        if 'error' in response:
            raise EpaRequestError(response['error'])
        return response

    def list_allocations(self):
        """Return the daemon's current allocation and pool state.

        :rtype: dict
        """
        return self._request({
            'action': 'list_allocations',
            'service_name': 'ceph-osd-charm',
        })

    def wait_ready(self, timeout=READY_TIMEOUT, interval=READY_INTERVAL,
                   now=time.time, sleep=time.sleep):
        """Wait for the daemon to answer, and return its state.

        A freshly installed or restarted daemon rebinds its socket, so a
        request issued immediately after ``snap install``/``snap
        restart`` can be refused even though the daemon is healthy.

        :rtype: dict
        :raises EpaUnavailable: the daemon never answered
        """
        deadline = now() + timeout
        while True:
            try:
                return self.list_allocations()
            except EpaUnavailable as exc:
                failure = exc
            if now() >= deadline:
                raise failure
            sleep(interval)

    def supports_non_preemptive(self, listing=None):
        """Report whether protected ownership is supported.

        Older daemons silently ignore unknown request fields, so the
        advertised capability is the only safe signal.

        :rtype: bool
        """
        if listing is None:
            listing = self.list_allocations()
        features = listing.get('supported_cpu_features') or []
        return FEATURE_NON_PREEMPTIVE in features

    def _check_policy(self, response):
        """Verify the daemon confirmed protected ownership."""
        if response.get('preemption_policy') != POLICY_NON_PREEMPTIVE:
            raise EpaUnsupported(
                'EPA orchestrator did not confirm the {} policy '
                '(got {!r})'.format(
                    POLICY_NON_PREEMPTIVE,
                    response.get('preemption_policy')))
        return response

    def allocate_numa_cores(self, service, numa_node, count):
        """Claim exactly ``count`` logical CPUs from one NUMA node.

        :returns: the claimed logical CPU IDs
        :rtype: list[int]
        """
        response = self._check_policy(self._request({
            'action': 'allocate_numa_cores',
            'service_name': service,
            'numa_node': numa_node,
            'num_of_cores': count,
            'preemption_policy': POLICY_NON_PREEMPTIVE,
        }))
        return parse_cpu_list(response.get('cores_allocated') or '')

    def allocate_cores(self, service, count):
        """Claim exactly ``count`` logical CPUs, ignoring NUMA locality.

        :returns: the claimed logical CPU IDs
        :rtype: list[int]
        """
        response = self._check_policy(self._request({
            'action': 'allocate_cores',
            'service_name': service,
            'num_of_cores': count,
            'preemption_policy': POLICY_NON_PREEMPTIVE,
        }))
        return parse_cpu_list(response.get('allocated_cores') or '')

    def release_numa_node(self, service, numa_node):
        """Release the CPUs a service holds on a single NUMA node."""
        self._request({
            'action': 'allocate_numa_cores',
            'service_name': service,
            'numa_node': numa_node,
            'num_of_cores': RELEASE_ALL,
        })

    def release(self, service):
        """Release every CPU claim a service holds."""
        self._request({
            'action': 'allocate_cores',
            'service_name': service,
            'num_of_cores': RELEASE_ALL,
        })


def allocations_by_service(listing):
    """Index a ``list_allocations`` response by service name.

    :rtype: dict[str, dict]
    """
    result = {}
    for entry in listing.get('allocations') or []:
        name = entry.get('service_name')
        if not name:
            continue
        result[name] = {
            'cores': parse_cpu_list(entry.get('allocated_cores') or ''),
            'preemption_policy': entry.get('preemption_policy'),
            'is_explicit': bool(entry.get('is_explicit')),
        }
    return result


def eligible_cpus(listing):
    """Return the logical CPUs the daemon may currently grant.

    :rtype: list[int]
    """
    pool = listing.get('cpu_pool') or {}
    return parse_cpu_list(pool.get('eligible_cpus') or '')


def pool_source(listing):
    """Return the active pool source (``isolated`` or ``configured``)."""
    pool = listing.get('cpu_pool') or {}
    return pool.get('source')


def claimed_by_others(listing, own_services):
    """Return the CPUs claimed by owners other than this charm.

    :param listing: a ``list_allocations`` response
    :type listing: dict
    :param own_services: EPA service names owned by this charm
    :type own_services: iterable[str]
    :rtype: set[int]
    """
    own = set(own_services)
    claimed = set()
    for name, entry in allocations_by_service(listing).items():
        if name in own:
            continue
        claimed.update(entry['cores'])
    return claimed


def free_cpus_for(listing, own_services):
    """Return CPUs available to the charm, per the daemon's own state.

    CPUs already claimed by the charm's own OSD services count as
    available, because re-requesting them is an override rather than a
    new claim.

    :param listing: a ``list_allocations`` response
    :type listing: dict
    :param own_services: EPA service names owned by this charm
    :type own_services: iterable[str]
    :rtype: set[int]
    """
    own = set(own_services)
    free = set(eligible_cpus(listing))
    for name, entry in allocations_by_service(listing).items():
        if name in own:
            continue
        free.difference_update(entry['cores'])
    return free
