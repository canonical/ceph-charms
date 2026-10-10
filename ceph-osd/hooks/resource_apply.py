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

"""Apply CPU allocations to running OSDs.

The EPA orchestrator only records ownership, so the charm is
responsible for enforcement.  Enforcement is a systemd drop-in per OSD
instance carrying ``CPUAffinity`` (and, for NUMA-aligned profiles, a
memory ``NUMAPolicy``), which survives reboots and OSD restarts.

Drop-ins take effect on the next start of the unit, so a change is
followed immediately by a restart of the affected OSD.  Ceph's own NUMA
affinity must be disabled while an allocation is applied; otherwise it can
widen the process affinity after systemd has started the daemon.
"""

import json
import os
import subprocess
import time

from charmhelpers.core.hookenv import (
    application_name,
    log,
    DEBUG,
    INFO,
)
from charmhelpers.core.host import service_restart
from charmhelpers.core.unitdata import kv

from host_topology import (
    format_cpu_list,
    parse_cpu_list,
)

DROPIN_DIR = '/etc/systemd/system/ceph-osd@{osd_id}.service.d'
DROPIN_NAME = '50-charm-resource-profile.conf'
PENDING_KEY = 'resource-alloc.pending-restarts'
CLEAR_PENDING_KEY = 'resource-alloc.pending-clears'


def pending_restarts():
    """OSDs whose on-disk policy has not yet been activated successfully."""
    return set(kv().get(PENDING_KEY) or [])


def _pending(osd_id, value):
    pending = pending_restarts()
    if value:
        pending.add(str(osd_id))
    else:
        pending.discard(str(osd_id))
    database = kv()
    database.set(PENDING_KEY, sorted(pending))
    database.flush()


def pending_clears():
    """OSDs whose drop-in removal still needs a systemd reload."""
    return set(kv().get(CLEAR_PENDING_KEY) or [])


def _pending_clear(osd_id, value):
    pending = pending_clears()
    if value:
        pending.add(str(osd_id))
    else:
        pending.discard(str(osd_id))
    database = kv()
    database.set(CLEAR_PENDING_KEY, sorted(pending))
    database.flush()


HEADER = (
    '# Managed by the ceph-osd charm (performance-profile={profile}).\n'
    '# Do not edit: changes are overwritten on the next hook run.\n'
)

# OSD states that mean the daemon is still coming up.
NOT_READY_STATES = frozenset([
    'initializing', 'preboot', 'booting', 'waiting_for_healthy',
])
READY_STATE = 'active'

RESTART_TIMEOUT = 300
RESTART_POLL_INTERVAL = 5


class ApplyError(Exception):
    """Raised when an allocation could not be enforced."""


def dropin_path(osd_id):
    """Return the drop-in path for an OSD instance."""
    return os.path.join(DROPIN_DIR.format(osd_id=osd_id), DROPIN_NAME)


def _application_name():
    """Return the deployed application name, with a unit-test default."""
    try:
        return application_name()
    except KeyError:
        return 'ceph-osd'


def render_dropin(profile, cores, numa_policy=None, numa_node=None):
    """Render the systemd drop-in enforcing one OSD's allocation.

    :param profile: active profile name, recorded as a comment
    :type profile: str
    :param cores: logical CPU IDs the OSD may run on, either as IDs or
        as a range string such as ``4-7``
    :type cores: iterable[int] or str
    :param numa_policy: systemd NUMAPolicy, or None to omit
    :type numa_policy: str or None
    :param numa_node: NUMA node for NUMAMask, or None to omit
    :type numa_node: int or None
    :rtype: str
    """
    if isinstance(cores, str):
        cores = parse_cpu_list(cores)
    lines = [HEADER.format(profile=profile), '[Service]\n']
    # This must beat /etc/ceph/ceph.conf alternatives from co-located charms.
    # CEPH_CONF is consumed by Ceph before its default config-file search.
    lines.append(
        'Environment="CEPH_CONF=/var/lib/charm/{}/ceph.conf"\n'.format(
            _application_name()))
    lines.append('CPUAffinity={}\n'.format(format_cpu_list(cores)))
    if numa_policy and numa_node is not None:
        lines.append('NUMAPolicy={}\n'.format(numa_policy))
        lines.append('NUMAMask={}\n'.format(numa_node))
    return ''.join(lines)


def read_dropin(osd_id):
    """Return the current drop-in content, or None when absent."""
    try:
        with open(dropin_path(osd_id), 'r') as handle:
            return handle.read()
    except (IOError, OSError):
        return None


def write_dropin(osd_id, content):
    """Write an OSD drop-in, returning True when it changed on disk."""
    path = dropin_path(osd_id)
    if read_dropin(osd_id) == content:
        log('Drop-in for osd.{} already current'.format(osd_id), level=DEBUG)
        return False
    directory = os.path.dirname(path)
    if not os.path.isdir(directory):
        os.makedirs(directory)
    with open(path, 'w') as handle:
        handle.write(content)
    os.chmod(path, 0o644)
    log('Wrote CPU allocation drop-in {}'.format(path), level=INFO)
    return True


def remove_dropin(osd_id):
    """Remove an OSD drop-in, returning True when one was removed."""
    path = dropin_path(osd_id)
    if not os.path.exists(path):
        return False
    os.remove(path)
    log('Removed CPU allocation drop-in {}'.format(path), level=INFO)
    directory = os.path.dirname(path)
    try:
        os.rmdir(directory)
    except OSError:
        # The directory may hold unrelated drop-ins.
        pass
    return True


def daemon_reload():
    """Reload systemd so drop-in changes are picked up."""
    subprocess.check_call(['systemctl', 'daemon-reload'])


def _unit_active(osd_id):
    """Report whether the OSD's systemd unit is active."""
    return subprocess.call(
        ['systemctl', 'is-active', '--quiet',
         'ceph-osd@{}.service'.format(osd_id)]) == 0


def _unit_main_pid(osd_id):
    """Return an active OSD unit's main PID, or None."""
    try:
        value = subprocess.check_output([
            'systemctl', 'show', '--property=MainPID', '--value',
            'ceph-osd@{}.service'.format(osd_id)]).decode('UTF-8').strip()
        pid = int(value)
        return pid or None
    except (ValueError, OSError, subprocess.CalledProcessError):
        return None


def _thread_cpu_affinities(pid, proc_root='/proc'):
    """Return effective affinity lists for every thread of ``pid``."""
    task_directory = os.path.join(proc_root, str(pid), 'task')
    try:
        tids = os.listdir(task_directory)
    except OSError:
        return None
    affinities = {}
    for tid in tids:
        try:
            with open(os.path.join(task_directory, tid, 'status')) as status:
                for line in status:
                    if line.startswith('Cpus_allowed_list:'):
                        affinities[tid] = line.split(':', 1)[1].strip()
                        break
        except OSError:
            # Threads can exit while being enumerated. A subsequent verify
            # sees the stable process state; do not mistake that race for a
            # successful check of this sample.
            return None
    return affinities or None


def _daemon_config_value(osd_id, option):
    """Read one effective daemon configuration value from its admin socket."""
    try:
        output = subprocess.check_output([
            'ceph', 'daemon', 'osd.{}'.format(osd_id), 'config', 'get',
            option], stderr=subprocess.DEVNULL).decode('UTF-8').strip()
        value = json.loads(output)
        if isinstance(value, dict):
            value = value.get(option)
        return str(value).lower()
    except (OSError, ValueError, TypeError, subprocess.CalledProcessError):
        return None


def verify_enforcement(osd_id, cores):
    """Raise when Ceph did not retain the CPU boundary after restart."""
    auto = _daemon_config_value(osd_id, 'osd_numa_auto_affinity')
    node = _daemon_config_value(osd_id, 'osd_numa_node')
    if auto not in ('false', '0') or node != '-1':
        raise ApplyError(
            'osd.{} has conflicting Ceph NUMA affinity settings '
            '(osd_numa_auto_affinity={!r}, osd_numa_node={!r})'.format(
                osd_id, auto, node))
    pid = _unit_main_pid(osd_id)
    affinities = _thread_cpu_affinities(pid) if pid else None
    expected_cpus = set(parse_cpu_list(cores) if isinstance(cores, str)
                        else cores)
    expected = format_cpu_list(expected_cpus)
    if not affinities:
        raise ApplyError(
            'cannot read effective CPU affinity for osd.{}'.format(osd_id))
    mismatched = []
    for tid, affinity in affinities.items():
        try:
            actual_cpus = set(parse_cpu_list(affinity))
        except ValueError:
            actual_cpus = set()
        if not actual_cpus or not actual_cpus.issubset(expected_cpus):
            mismatched.append(tid)
    if mismatched:
        raise ApplyError(
            'osd.{} thread affinity escapes {} (threads: {})'.format(
                osd_id, expected, ', '.join(sorted(mismatched))))


def _osd_state(osd_id):
    """Return the OSD's reported state, or None when unavailable."""
    try:
        output = subprocess.check_output(
            ['ceph', 'daemon', 'osd.{}'.format(osd_id), 'status'],
            stderr=subprocess.DEVNULL)
        return json.loads(output.decode('UTF-8')).get('state')
    except (subprocess.CalledProcessError, OSError, ValueError):
        return None


def wait_for_osd(osd_id, timeout=RESTART_TIMEOUT,
                 interval=RESTART_POLL_INTERVAL, now=time.time,
                 sleep=time.sleep):
    """Wait until an OSD is running and out of its boot states.

    :returns: True when the OSD became ready within the timeout
    :rtype: bool
    """
    deadline = now() + timeout
    while True:
        if _unit_active(osd_id):
            state = _osd_state(osd_id)
            if state == READY_STATE:
                return True
            log('osd.{} not ready yet (state={})'.format(osd_id, state),
                level=DEBUG)
        if now() >= deadline:
            return False
        sleep(interval)


def stop_osd(osd_id, timeout=RESTART_TIMEOUT,
             interval=RESTART_POLL_INTERVAL, now=time.time,
             sleep=time.sleep):
    """Stop an OSD and prove it no longer runs on its current claim."""
    log('Stopping osd.{} before replacing its CPU allocation'.format(osd_id),
        level=INFO)
    if subprocess.call(['systemctl', 'stop',
                        'ceph-osd@{}.service'.format(osd_id)]) != 0:
        raise ApplyError('systemd refused to stop osd.{}'.format(osd_id))
    deadline = now() + timeout
    while _unit_active(osd_id):
        if now() >= deadline:
            raise ApplyError(
                'osd.{} did not stop within {}s before replacing its CPU '
                'allocation'.format(osd_id, timeout))
        sleep(interval)


def restart_osd(osd_id, timeout=RESTART_TIMEOUT):
    """Restart a single OSD and wait for it to come back.

    :raises ApplyError: the OSD did not become ready in time
    """
    log('Restarting osd.{} to apply its CPU allocation'.format(osd_id),
        level=INFO)
    if not service_restart('ceph-osd@{}'.format(osd_id)):
        raise ApplyError('systemd refused to restart osd.{}'.format(osd_id))
    if not wait_for_osd(osd_id, timeout=timeout):
        raise ApplyError(
            'osd.{} did not become ready within {}s after restarting for '
            'its CPU allocation'.format(osd_id, timeout))


def apply_allocations(profile, allocations, restart_timeout=RESTART_TIMEOUT,
                      force_osd_ids=(), verify_unchanged=False):
    """Enforce allocations, restarting only the OSDs that changed.

    OSDs are restarted one at a time, waiting for each to come back
    before touching the next one.

    :param profile: active profile name
    :type profile: str
    :param allocations: per-OSD allocation dicts, each with ``osd-id``,
        ``cores``, ``numa-policy`` and ``numa-node`` keys
    :type allocations: list[dict]
    :param force_osd_ids: OSD IDs requiring restart even with matching bytes
    :type force_osd_ids: iterable[str]
    :param verify_unchanged: verify unchanged running OSDs without restart
    :type verify_unchanged: bool
    :returns: the OSD IDs that were restarted
    :rtype: list[str]
    :raises ApplyError: an OSD failed to come back after its restart
    """
    changed = []
    force_osd_ids = set(str(osd_id) for osd_id in force_osd_ids)
    allocation_by_id = dict(
        (str(allocation['osd-id']), allocation) for allocation in allocations)
    for allocation in allocations:
        osd_id = str(allocation['osd-id'])
        content = render_dropin(
            profile,
            allocation['cores'],
            numa_policy=allocation.get('numa-policy'),
            numa_node=allocation.get('numa-node'))
        if (read_dropin(osd_id) != content or
                osd_id in pending_restarts() or osd_id in force_osd_ids):
            # Persist before writing: a failed restart or interrupted hook
            # must not turn identical drop-in bytes into a false success on
            # retry and release the next host in the rolling operation.
            _pending(osd_id, True)
            write_dropin(osd_id, content)
            changed.append(osd_id)
    if not changed:
        if verify_unchanged:
            for allocation in allocations:
                verify_enforcement(
                    allocation['osd-id'], allocation['cores'])
        return []
    daemon_reload()
    restarted = []
    for osd_id in changed:
        restart_osd(osd_id, timeout=restart_timeout)
        verify_enforcement(osd_id, allocation_by_id[osd_id]['cores'])
        _pending(osd_id, False)
        restarted.append(osd_id)
    return restarted


def clear_allocation(osd_id, restart=False, restart_timeout=RESTART_TIMEOUT):
    """Drop an OSD's enforcement, optionally restarting it.

    Drop-in removal and the following daemon reload are one durable boundary:
    a crash or reload failure after removal must retry the reload before any
    caller can release the corresponding EPA claim.  Cleanup markers are
    deliberately separate from restart markers, because teardown must never
    make reconciliation restart an OSD that was deliberately stopped.

    :returns: True when enforcement was or remains pending removal
    :rtype: bool
    :raises ApplyError: the removal or daemon reload could not be completed
    """
    osd_id = str(osd_id)
    has_dropin = read_dropin(osd_id) is not None
    pending_clear = osd_id in pending_clears()
    if (not has_dropin and not pending_clear and
            osd_id not in pending_restarts()):
        return False
    if restart:
        _pending(osd_id, True)
    _pending_clear(osd_id, True)
    try:
        remove_dropin(osd_id)
        daemon_reload()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ApplyError(
            'cannot clear CPU allocation for osd.{}: {}'.format(
                osd_id, exc))
    _pending_clear(osd_id, False)
    if restart:
        # Do not swallow a failed unpin restart: that would release the next
        # host while this one has not recovered.
        restart_osd(osd_id, timeout=restart_timeout)
    _pending(osd_id, False)
    return True
