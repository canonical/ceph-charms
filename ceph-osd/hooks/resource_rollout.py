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

"""Juju cohort/generation discovery for the shared Ceph rolling mechanism.

Peer requests identify the application's hosts, including hosts with no OSDs
or no changes to apply. The leader freezes one generation at a time;
Ceph monitor start/alive/done markers perform the same handoff as upgrades.

Do not wait in a hook for peer data: relation writes arrive after the hook
exits. Progress notifications wake peers; update-status only observes stalled
rollouts. A new request never supersedes an unfinished generation.
"""

import errno
import hashlib
import json
import socket
import subprocess
import time
import uuid

from charmhelpers.core import hookenv
from charmhelpers.core.unitdata import kv
from charms_ceph.rolling import RollingError, RollingOperation, RollingWait
from charms_ceph.utils import WatchDog

PEER = 'resource-peers'
REQUEST = 'resource-request'
ROLLOUT = 'resource-rollout'
PROGRESS = 'resource-progress'
LOCAL_REQUEST = 'resource-alloc.rollout-request'
LOCAL_PROGRESS = 'resource-alloc.rollout-progress'
REFRESH = 'resource-alloc.rollout-refresh'
TIMEOUT = 1800


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'))


def _decode(value):
    return json.loads(value) if value else None


def _save(key, value):
    db = kv()
    db.set(key, value)
    db.flush()


def request_refresh():
    """Request a new generation after charm code changes."""
    _save(REFRESH, uuid.uuid4().hex)


def _request(inputs):
    configuration = dict(hookenv.config())
    # A nonce, rather than a profile hash, distinguishes A -> B -> A and keeps
    # previously completed markers from admitting a later rollout.
    fingerprint = hashlib.sha256(_json({
        'config': configuration, 'inputs': inputs,
        'refresh': kv().get(REFRESH),
    }).encode()).hexdigest()
    previous = kv().get(LOCAL_REQUEST)
    if previous and previous['fingerprint'] == fingerprint:
        return previous
    request = {
        'id': uuid.uuid4().hex,
        'fingerprint': fingerprint,
        'config': hashlib.sha256(_json(configuration).encode()).hexdigest(),
        'host': socket.gethostname(),
    }
    _save(LOCAL_REQUEST, request)
    return request


def _monitor_command(*args):
    # Use the existing upgrade credential, explicitly selecting its keyring
    # and this application's config even when co-located with another Ceph
    # charm. ``emit_cephconf`` renders this same application-specific path.
    from utils import _upgrade_keyring
    config_path = '/var/lib/charm/{}/ceph.conf'.format(
        hookenv.application_name())
    command = ['ceph', '--conf', config_path,
               '--id', 'osd-upgrade', '--keyring', _upgrade_keyring,
               'config-key'] + list(args)
    return subprocess.check_output(
        command, stderr=subprocess.PIPE, timeout=30).decode('UTF-8').strip()


def _read_marker(key):
    try:
        return _monitor_command('get', key)
    except subprocess.CalledProcessError as exc:
        if exc.returncode == errno.ENOENT:
            return None
        raise


def _write_marker(key, value):
    _monitor_command('put', key, str(value))


def _operation(rollout):
    return RollingOperation(
        'osd-resource', rollout['id'], _read_marker, _write_marker)


def _participants(rollout):
    requests = rollout['requests']
    # Each unit is an independently serialized writer. Host ordering matches
    # release upgrades, with unit identity breaking ties for co-located units.
    return sorted(requests, key=lambda unit: (requests[unit]['host'], unit))


def _active(rid):
    return _decode(hookenv.relation_get(
        ROLLOUT, rid=rid, app=hookenv.application_name()))


def _requests(rid, mine):
    me = hookenv.local_unit()
    units = set(hookenv.related_units(rid))
    if units != set(hookenv.expected_peer_units()):
        raise RollingWait('waiting for all resource peers to join')
    requests = {me: mine}
    for unit in sorted(units):
        request = _decode(hookenv.relation_get(REQUEST, unit, rid))
        if not request:
            raise RollingWait('waiting for resource request from {}'.format(
                unit))
        requests[unit] = request
    if any(r['config'] != mine['config'] for r in requests.values()):
        raise RollingWait('waiting for peers to observe the new configuration')
    return requests


def _progress(rid, rollout, request, status, message=''):
    """Persist and publish progress, repairing an uncommitted prior write.

    Unitdata is flushed during the hook, while Juju commits relation writes
    only after a successful hook exit.  The local record alone therefore
    cannot prove peers received a completion notification.
    """
    progress = {'rollout': rollout['id'], 'request': request['id'],
                'status': status, 'message': message}
    if kv().get(LOCAL_PROGRESS) != progress:
        _save(LOCAL_PROGRESS, progress)
    published = _decode(hookenv.relation_get(
        PROGRESS, unit=hookenv.local_unit(), rid=rid))
    if published != progress:
        hookenv.relation_set(rid, {PROGRESS: _json(progress)})


def run(inputs, callback, unmanaged=False, check_ready=None):
    """Run local reconciliation only in this unit's serialized turn.

    Return callback state (or None for an already completed participant).
    Failures fence successors. Retrying a failed local callback requires a new
    request (a config/inventory change or charm refresh), not incidental peer
    notifications. Interrupted callbacks may resume on the same unit; the
    callback must durably track incomplete enforcement and verify readiness.
    """
    request = _request(inputs)
    relations = hookenv.relation_ids(PEER)
    if not relations:
        if unmanaged:
            return callback()
        raise RollingWait('waiting for resource peer relation')
    rid = relations[0]
    hookenv.relation_set(rid, {REQUEST: _json(request)})
    rollout = _active(rid)
    me = hookenv.local_unit()
    if not rollout and unmanaged:
        return callback()

    if rollout:
        operation = _operation(rollout)
        finished = all(operation.completed(u) for u in _participants(rollout))
    else:
        finished = True
    # A monitor marker is durable before Juju commits relation writes. Repair
    # any missing completion notification using the frozen generation request,
    # even if this is the last participant or this unit has since queued a
    # different request.
    if rollout and me in rollout['requests'] and operation.completed(me):
        _progress(rid, rollout, rollout['requests'][me], 'done')

    if finished:
        requests = _requests(rid, request)
        if rollout and requests == rollout['requests']:
            return None
        # The default unmanaged profile needs neither an allocator nor a
        # monitor credential. An outstanding managed generation is still
        # drained in order before this fast path is reached.
        if unmanaged:
            return callback()
        if not hookenv.is_leader():
            raise RollingWait('waiting for leader to publish resource rollout')
        rollout = {'id': uuid.uuid4().hex, 'created': time.time(),
                   'requests': requests}
        hookenv.relation_set(rid, {ROLLOUT: _json(rollout)}, app=True)
        hookenv.flush(hookenv.application_name())
        operation = _operation(rollout)

    if me not in rollout['requests']:
        raise RollingWait('waiting for the previous resource cohort to finish')
    if not operation.check_turn(_participants(rollout), me,
                                rollout['created'], TIMEOUT):
        if rollout['requests'][me] != request:
            raise RollingWait('waiting for the previous resource rollout '
                              'before applying the new request')
        return None

    previous = kv().get(LOCAL_PROGRESS) or {}
    if (previous.get('rollout') == rollout['id'] and
            previous.get('request') == request['id'] and
            previous.get('status') == 'failed'):
        raise RollingError(previous['message'])

    def apply(kick):
        kick()
        state = callback()
        if state.get('errors'):
            raise RollingError('; '.join(state['errors']))
        # Also check an unmanaged/no-op participant draining an older
        # generation: changing config must not hide a down predecessor.
        if check_ready is not None:
            check_ready()
        return state

    _progress(rid, rollout, request, 'running')
    try:
        state = operation.run(me, apply, WatchDog)
    except Exception as exc:
        _progress(rid, rollout, request, 'failed', str(exc))
        raise
    _progress(rid, rollout, request, 'done')
    return state


def observe():
    """Read-only timeout/failure observation for update-status.

    No admission, marker writes, allocation or restart occurs here.
    """
    relations = hookenv.relation_ids(PEER)
    if not relations:
        return
    rollout = _active(relations[0])
    if not rollout or hookenv.local_unit() not in rollout['requests']:
        return
    previous = kv().get(LOCAL_PROGRESS) or {}
    if (previous.get('rollout') == rollout['id'] and
            previous.get('status') == 'failed'):
        raise RollingError(previous['message'])
    _operation(rollout).check_turn(
        _participants(rollout), hookenv.local_unit(), rollout['created'],
        TIMEOUT)
