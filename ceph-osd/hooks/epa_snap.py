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

"""Installation and CPU pool management for the EPA orchestrator snap.

An EPA orchestrator that is already present on the host is left
completely alone: it is assumed to be managed by the operator or
another charm, so neither its channel nor its ``cpu-pool`` is touched.

When the charm installs the snap itself it also owns the CPU pool.  By
default EPA only offers ``isolated`` CPUs, which is normally empty, so
the charm configures an explicit pool instead: a conservative share of
each NUMA node's logical CPUs, grown as profile demand requires.
"""

import math
import os
import subprocess

from charmhelpers.core.hookenv import (
    log,
    DEBUG,
    INFO,
)

from epa_client import (
    EpaClient,
    EpaUnavailable,
)
from host_topology import (
    format_cpu_list,
    parse_cpu_list,
    physical_cores,
)

SNAP_NAME = 'epa-orchestrator'
DAEMON = '{}.daemon'.format(SNAP_NAME)
POOL_KEY = 'cpu-pool'

# Conservative default share of each NUMA node's logical CPUs, used when
# the charm creates the pool and profile demand is lower.
DEFAULT_POOL_PERCENT = 20
# Never hand more than this share of a node to EPA, so that host and
# non-OSD workloads retain capacity.
MAX_POOL_PERCENT = 75


class SnapError(Exception):
    """Raised when the EPA orchestrator snap cannot be managed."""


def _run(command):
    """Run a command, returning its stdout."""
    log('Running {}'.format(' '.join(command)), level=DEBUG)
    return subprocess.check_output(
        command, stderr=subprocess.STDOUT).decode('UTF-8')


def is_installed(snap_name=SNAP_NAME):
    """Report whether the snap is installed on this host."""
    try:
        _run(['snap', 'list', snap_name])
        return True
    except subprocess.CalledProcessError:
        return False
    except OSError as exc:
        raise SnapError('snapd is unavailable: {}'.format(exc))


def _install_resource(path):
    """Install the snap from a charm resource."""
    try:
        _run(['snap', 'install', '--dangerous', path])
        return
    except subprocess.CalledProcessError as exc:
        log('Installing {} without devmode failed: {}'.format(
            path, exc.output), level=DEBUG)
    _run(['snap', 'install', '--dangerous', '--devmode', path])


def usable_resource(path):
    """Report whether a charm resource file holds an actual snap.

    Juju provides a zero length file when no resource is attached.

    :rtype: bool
    """
    try:
        return bool(path) and os.path.getsize(path) > 0
    except OSError:
        return False


def ensure_installed(resource_path=None, channel='latest/stable'):
    """Ensure the EPA orchestrator snap is present.

    An existing installation is never modified.

    :param resource_path: path to an attached charm resource, if any
    :type resource_path: str or None
    :param channel: store channel used when no resource is attached
    :type channel: str
    :returns: one of ``present``, ``resource`` or ``store``
    :rtype: str
    :raises SnapError: the snap could not be installed
    """
    if is_installed():
        return 'present'
    errors = []
    if usable_resource(resource_path):
        try:
            _install_resource(resource_path)
            log('Installed {} from charm resource'.format(SNAP_NAME),
                level=INFO)
            return 'resource'
        except subprocess.CalledProcessError as exc:
            errors.append(
                'resource install failed: {}'.format(exc.output))
    try:
        _run(['snap', 'install', SNAP_NAME, '--channel', channel])
        log('Installed {} from the snap store ({})'.format(
            SNAP_NAME, channel), level=INFO)
        return 'store'
    except subprocess.CalledProcessError as exc:
        errors.append('store install failed: {}'.format(exc.output))
    except OSError as exc:
        errors.append('snapd is unavailable: {}'.format(exc))
    raise SnapError('cannot install {}: {}'.format(
        SNAP_NAME, '; '.join(errors)))


def get_configured_pool():
    """Return the configured ``cpu-pool`` snap setting.

    :returns: the raw setting, or None when unset
    :rtype: str or None
    """
    try:
        value = _run(['snap', 'get', SNAP_NAME, POOL_KEY]).strip()
    except (subprocess.CalledProcessError, OSError):
        return None
    return value or None


def set_pool(cpus):
    """Configure the EPA CPU pool and restart its daemon.

    The pool is fixed at daemon startup, so a restart is required for a
    new pool to take effect.  Existing claims are persisted and survive
    the restart.

    :param cpus: logical CPU IDs to offer to EPA
    :type cpus: iterable[int]
    :returns: the pool string that was configured
    :rtype: str
    """
    pool = format_cpu_list(cpus)
    if not pool:
        raise SnapError('refusing to configure an empty EPA CPU pool')
    try:
        _run(['snap', 'set', SNAP_NAME, '{}={}'.format(POOL_KEY, pool)])
        _run(['snap', 'restart', DAEMON])
    except (subprocess.CalledProcessError, OSError) as exc:
        output = getattr(exc, 'output', exc)
        raise SnapError('cannot configure EPA CPU pool {}: {}'.format(
            pool, output))
    # The restarted daemon rebinds its socket, so wait for it to answer
    # before the caller relies on the new pool.
    try:
        EpaClient().wait_ready()
    except EpaUnavailable as exc:
        raise SnapError(
            'EPA orchestrator did not come back after configuring CPU pool '
            '{}: {}'.format(pool, exc))
    log('Configured EPA CPU pool {}'.format(pool), level=INFO)
    return pool


def _select_node_cpus(cpus, want, root='/'):
    """Pick ``want`` logical CPUs from a node, preferring whole cores.

    CPU 0 and its SMT siblings are never selected: they are left for
    host housekeeping.  Selection starts from the highest numbered
    physical core, which keeps the low CPUs free for the OS.

    :rtype: list[int]
    """
    groups = physical_cores(cpus, root=root)
    groups = [g for g in groups if 0 not in g]
    selected = []
    for group in reversed(groups):
        if len(selected) >= want:
            break
        missing = want - len(selected)
        selected.extend(group[:missing] if missing < len(group) else group)
    return sorted(selected)


def compute_pool(nodes, demand_by_node=None, unbound_demand=0,
                 floor_percent=DEFAULT_POOL_PERCENT,
                 cap_percent=MAX_POOL_PERCENT, root='/'):
    """Compute the CPU pool the charm should offer to EPA.

    Every NUMA node contributes CPUs, so that NUMA-aligned requests can
    be served wherever an OSD's device lives.  Per node the size is the
    profile demand, floored at ``floor_percent`` and capped at
    ``cap_percent`` of that node's logical CPUs.

    :param nodes: NUMA node to logical CPU mapping
    :type nodes: dict[int, list[int]]
    :param demand_by_node: NUMA-bound demand per node
    :type demand_by_node: dict[int, int] or None
    :param unbound_demand: demand without NUMA locality, spread evenly
    :type unbound_demand: int
    :returns: logical CPU IDs for the pool
    :rtype: list[int]
    """
    demand_by_node = demand_by_node or {}
    node_count = len(nodes) or 1
    share = int(math.ceil(float(unbound_demand) / node_count))
    pool = []
    for node, cpus in sorted(nodes.items()):
        if not cpus:
            continue
        demand = demand_by_node.get(node, 0) + share
        floor = int(math.ceil(len(cpus) * floor_percent / 100.0))
        cap = int(math.floor(len(cpus) * cap_percent / 100.0))
        want = min(max(demand, floor), max(cap, 1))
        pool.extend(_select_node_cpus(cpus, want, root=root))
    return sorted(pool)


def grow_pool(current, desired):
    """Merge a desired pool into the current one without shrinking.

    EPA rejects pool changes that exclude CPUs it has already granted,
    and shrinking requires coordinated workload migration, so the charm
    only ever grows a pool it owns.

    :param current: current pool setting, or None
    :type current: str or None
    :param desired: desired logical CPU IDs
    :type desired: iterable[int]
    :returns: tuple of (merged CPU IDs, whether a change is needed)
    :rtype: tuple[list[int], bool]
    """
    existing = set(parse_cpu_list(current or ''))
    wanted = set(desired)
    merged = sorted(existing | wanted)
    return merged, merged != sorted(existing)
