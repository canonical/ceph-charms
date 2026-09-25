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

"""Read-only guest probes for the opt-in resource-profile functional test.

Uses only the guest's standard library and system commands, not charm modules.
"""

import glob
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time


def command(*args):
    """Run a bounded command, failing rather than hiding missing evidence."""
    return subprocess.check_output(args, text=True, timeout=30)


def allocations():
    """Read EPA's protected claims directly from its Unix socket."""
    path = '/var/snap/epa-orchestrator/current/data/epa.sock'
    if not Path(path).exists():
        return None
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(15)
        sock.connect(path)
        sock.sendall(json.dumps({
            'version': '1.0', 'service_name': 'ceph-profile-functest',
            'action': 'list_allocations',
        }).encode())
        chunks = []
        while True:
            data = sock.recv(65536)
            if not data:
                break
            chunks.append(data)
    result = json.loads(b''.join(chunks))
    return sorted(result['allocations'], key=lambda a: a['service_name'])


def snapshot():
    """Capture process identity, claims, drop-ins and thread affinity."""
    result = {'hostname': socket.gethostname(), 'time': time.time(),
              'boot-id': Path('/proc/sys/kernel/random/boot_id').read_text(),
              'claims': allocations(), 'osds': {}}
    for directory in sorted(glob.glob('/var/lib/ceph/osd/ceph-*')):
        osd = directory.rsplit('-', 1)[1]
        service = 'ceph-osd@{}.service'.format(osd)
        properties = dict(line.split('=', 1) for line in command(
            'systemctl', 'show', service,
            '--property=MainPID,ActiveState,LoadState,NUMAPolicy,NUMAMask,'
            'ExecMainStartTimestampMonotonic').splitlines())
        dropin = Path('/etc/systemd/system/{}.d/'
                      '50-charm-resource-profile.conf'.format(service))
        pid = int(properties['MainPID'])
        affinities = set()
        for task in glob.glob('/proc/{}/task/*'.format(pid)) if pid else []:
            try:
                affinities.add(tuple(sorted(os.sched_getaffinity(
                    int(task.rsplit('/', 1)[1])))))
            except ProcessLookupError:
                pass  # A thread may exit while its siblings are inspected.
        result['osds'][osd] = {
            'systemd': properties,
            'dropin': dropin.read_text() if dropin.exists() else None,
            'affinities': sorted(affinities),
        }
    return result


def markers():
    """Read the rollout handoff keys from a MON unit."""
    # One bounded command, independent of how many old generations exist.
    keys = json.loads(command('ceph', 'config-key', 'dump'))
    return {key: value for key, value in keys.items()
            if key.startswith('osd-resource_')}


def journal(since):
    """Keep systemd lifecycle messages (not the daemon's verbose log)."""
    text = command('journalctl', '-u', 'ceph-osd@*', '--since', '@' + since,
                   '--no-pager', '-o', 'json')
    return [entry for entry in map(json.loads, text.splitlines())
            if entry.get('SYSLOG_IDENTIFIER') == 'systemd']


if __name__ == '__main__':
    if sys.argv[1] == 'snapshot':
        output = snapshot()
    elif sys.argv[1] == 'markers':
        output = markers()
    elif sys.argv[1] == 'journal':
        output = journal(sys.argv[2])
    else:
        raise ValueError('Unknown probe: ' + sys.argv[1])
    print(json.dumps(output, sort_keys=True))
