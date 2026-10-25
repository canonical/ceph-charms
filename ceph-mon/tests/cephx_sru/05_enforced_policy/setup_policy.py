#!/usr/bin/env python3
"""Prepare administrator/daemon keys on a disposable fresh or older Squid lab."""

import argparse
from contextlib import ExitStack
import json
from pathlib import Path
import shlex
import tempfile
import time
from uuid import uuid4

import zaza
from zaza import model

from helpers import pgs_active_clean, remote
from setup_lab import SOURCE, VERSION, copy_admin_files

OLD_VERSION = "19.2.3-0ubuntu0.24.04.3"
UNITS = ("ceph-mon/0", "ceph-osd/0", "ceph-osd/1", "ceph-fs/0", "ubuntu/0")
PACKAGES = {
    "ceph-mon/0": ("ceph-mon", "ceph-mgr", "ceph-common"),
    "ceph-osd/0": ("ceph-osd", "ceph-common"),
    "ceph-osd/1": ("ceph-osd", "ceph-common"),
    "ceph-fs/0": ("ceph-mds", "ceph-common"),
    "ubuntu/0": ("ceph-common",),
}

# Only entity names, locations and numeric type tags leave the guest.
LOCAL_KEYS = r'''
import base64, configparser, glob, json, pathlib, struct
paths = set(glob.glob('/var/lib/ceph/*/ceph-*/keyring'))
paths.update(glob.glob('/var/lib/ceph/bootstrap-*/*.keyring'))
paths.update(glob.glob('/etc/ceph/*.keyring'))
result = []
for filename in sorted(paths):
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(filename)
    for entity in parser.sections():
        if parser.has_option(entity, 'key'):
            raw = base64.b64decode(parser.get(entity, 'key'), validate=True)
            result.append({'entity': entity, 'path': filename,
                           'type': struct.unpack_from('<H', raw)[0]})
print(json.dumps(result))
'''
AUTH_TYPES = r'''
import base64, json, pathlib, struct, sys
entries = json.loads(pathlib.Path(sys.argv[1]).read_text())['auth_dump']
result = []
for item in entries:
    assert not item.get('pending_key'), 'Unconsumed pending key: ' + item['entity']
    raw = base64.b64decode(item['key'], validate=True)
    result.append({'entity': item['entity'], 'type': struct.unpack_from('<H', raw)[0]})
print(json.dumps(result))
'''
CHECK_REPLACED = r'''
import base64, json, pathlib, struct, sys
before = {x['entity']: x['key'] for x in json.loads(pathlib.Path(sys.argv[1]).read_text())['auth_dump']}
after = {x['entity']: x['key'] for x in json.loads(pathlib.Path(sys.argv[2]).read_text())['auth_dump']}
assert set(before) <= set(after), 'An existing identity disappeared'
for entity, key in after.items():
    assert struct.unpack_from('<H', base64.b64decode(key, validate=True))[0] == 2, 'Non-aes256k identity: ' + entity
    assert entity not in before or key != before[entity], 'Unrotated identity: ' + entity
print('All administrator, daemon and remaining client keys are new aes256k keys')
'''
CHECK_LOCAL = r'''
import configparser, sys
expected = configparser.ConfigParser(interpolation=None)
actual = configparser.ConfigParser(interpolation=None)
expected.read(sys.argv[1]); actual.read(sys.argv[2])
entity = sys.argv[3]
assert actual.get(entity, 'key') == expected.get(entity, 'key'), 'Local/auth database key mismatch: ' + entity
print(entity + ': installed key matches the authoritative rotated key')
'''


def main(arguments=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--environment', type=Path, required=True)
    parser.add_argument('--origin', choices=('fresh', 'upgraded'), required=True)
    parser.add_argument('--disposable', action='store_true', required=True,
                        help='Acknowledge permanent package/key changes and downtime')
    args = parser.parse_args(arguments)
    expected = json.loads(args.environment.read_text())
    target = expected['model']
    if not target.split(':')[-1].startswith('cephx-sru-'):
        parser.error('Only explicitly named cephx-sru-* disposable models are accepted')
    if expected.get('SRU_POLICY_ORIGIN'):
        parser.error('Policy preparation is one-shot; deploy a new model for another run')

    def guest(unit, command, timeout=180):
        return remote(target, unit, command, timeout=timeout)

    def run(unit, *command, timeout=180):
        return guest(unit, shlex.join(str(x) for x in command), timeout)

    def python(unit, program, *arguments):
        return run(unit, 'python3', '-c', program, *arguments)

    def ceph(*arguments):
        return run('ceph-mon/0', 'ceph', *arguments)

    def versions(wanted):
        for unit, packages in PACKAGES.items():
            actual = run(unit, 'dpkg-query', '-W', '-f=${Version}\n', *packages)
            if not actual.splitlines() or any(x != wanted for x in actual.splitlines()):
                raise RuntimeError('Wrong pinned Ceph packages on ' + unit)
        actual = json.loads(ceph('versions', '-f', 'json'))
        for daemon in ('mon', 'mgr', 'osd', 'mds'):
            if not actual.get(daemon):
                raise RuntimeError('Missing running daemon version: ' + daemon)
        prefix = 'ceph version ' + wanted.split('-')[0] + ' '
        if not actual['overall'] or not all(x.startswith(prefix) for x in actual['overall']):
            raise RuntimeError('Running daemon versions do not match installed packages')
        return actual

    def ready(replaced_mds=None):
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            pgs = json.loads(ceph('pg', 'ls', '-f', 'json'))['pg_stats']
            fs = json.loads(ceph('fs', 'get', expected['SRU_CEPHFS'], '-f', 'json'))['mdsmap']
            status = json.loads(ceph('status', '-f', 'json'))
            replacement_seen = replaced_mds is None or any(
                item['name'] == replaced_mds[0] and item['gid'] != replaced_mds[1]
                for item in fs['info'].values())
            if (replacement_seen and pgs_active_clean(pgs) and status['mgrmap']['available']
                    and any(x['state'] == 'up:active' and not x.get('laggy_since')
                            for x in fs['info'].values())):
                return
            time.sleep(3)
        raise RuntimeError('Cluster did not regain active/clean PGs, manager and active MDS')

    fsid = ceph('fsid').strip()
    if fsid != expected['SRU_EXPECTED_FSID']:
        raise RuntimeError('Wrong cluster FSID')
    initial = VERSION if args.origin == 'fresh' else OLD_VERSION
    before_versions = versions(initial)
    # Cloud-init's original model pin distinguishes a fresh SRU deployment from
    # an already upgraded cluster. The original lab record predates this field.
    async def original_pin():
        lab = await model.get_model(target)
        config = await lab.get_config()
        return config['cloudinit-userdata'].value
    pin = zaza.sync_wrapper(original_pin)()
    if 'Pin: version ' + initial not in pin:
        raise RuntimeError('Original model cloud-init does not pin the claimed deployment version')
    if args.origin == 'upgraded':
        expected['SRU_POLICY_BEFORE'] = {
            'package': OLD_VERSION, 'fsid': fsid, 'versions': before_versions,
        }
        # Same-major patch upgrades are not performed by the charm source
        # upgrade handler; upgrade pinned packages explicitly, one host at a time.
        for unit in UNITS:
            guest(unit, f'''export DEBIAN_FRONTEND=noninteractive
printf '%s\\n' 'Package: src:ceph:any' 'Pin: version {VERSION}' 'Pin-Priority: 1001' '' 'Package: src:ceph:any' 'Pin: version *' 'Pin-Priority: -1' > /etc/apt/preferences.d/cephx-sru.pref
add-apt-repository -y {SOURCE}
apt-get update
apt-get install -y --no-install-recommends --no-remove {' '.join(package + '=' + VERSION for package in PACKAGES[unit])}
''', timeout=900)
            if unit != 'ubuntu/0':
                guest(unit, 'systemctl restart ceph.target', timeout=300)
        ready()
        versions(VERSION)
        expected['SRU_CEPH_PACKAGE'] = VERSION
        if ceph('fsid').strip() != fsid:
            raise RuntimeError('The upgrade replaced the cluster rather than retaining its FSID')
    ceph('mon', 'set', 'auth_allowed_ciphers', 'aes,aes256k')
    ceph('mon', 'set', 'auth_preferred_cipher', 'aes256k')
    ceph('config', 'set', 'mon', 'mon_auth_allow_insecure_key', 'true')
    # Inventory actual daemon data directories, not guessed hostnames or auth DB
    # entries alone; every running daemon must have an installed rotated key.
    locations = {unit: json.loads(python(unit, LOCAL_KEYS)) for unit in UNITS}
    monmap = json.loads(ceph('mon', 'dump', '-f', 'json'))
    osds = json.loads(ceph('osd', 'ls', '-f', 'json'))
    mgrmap = json.loads(ceph('mgr', 'dump', '-f', 'json'))
    fsmap = json.loads(ceph('fs', 'dump', '-f', 'json'))
    daemon_names = {'mgr.' + mgrmap['active_name']}
    daemon_names.update('mgr.' + item['name'] for item in mgrmap.get('standbys', []))
    daemon_names.update('osd.' + str(osd) for osd in osds)
    daemon_names.update('mds.' + item['name'] for fs in fsmap['filesystems']
                        for item in fs['mdsmap']['info'].values())
    daemon_names.update('mds.' + item['name'] for item in fsmap.get('standbys', []))
    mds_gids = {item['name']: item['gid'] for fs in fsmap['filesystems']
                for item in fs['mdsmap']['info'].values()}
    for entity in daemon_names | {'mon.'}:
        matches = [(unit, item) for unit, items in locations.items() for item in items
                   if item['entity'] == entity and '/var/lib/ceph/' in item['path']]
        if len(matches) != 1:
            raise RuntimeError('Expected exactly one installed daemon keyring for ' + entity)
    if len(monmap['mons']) != 1:
        raise RuntimeError('This disposable setup supports the pinned single-monitor bundle only')

    stage = '/home/ubuntu/.cephx-policy-' + uuid4().hex
    with ExitStack() as cleanup, tempfile.TemporaryDirectory(prefix='cephx-policy-') as directory:
        for unit in UNITS:
            run(unit, 'install', '-d', '-o', 'ubuntu', '-g', 'ubuntu', '-m', '700', stage)
            cleanup.callback(run, unit, 'rm', '-rf', '--', stage)
        private = stage + '/auth-before.json'
        guest('ceph-mon/0', 'umask 077; ceph auth ls -f json > ' + shlex.quote(private))
        entries = json.loads(python('ceph-mon/0', AUTH_TYPES, private))
        if args.origin == 'upgraded' and any(item['type'] != 1 for item in entries):
            raise RuntimeError('The older deployment did not retain legacy credentials')
        entities = {item['entity'] for item in entries} | {'mon.'}
        if not daemon_names | {'client.admin'} <= entities:
            raise RuntimeError('Auth database is missing an administrator or running daemon')
        # Keep a separate secure administrator until the new client.admin file
        # is installed and tested, as required by the upstream migration guide.
        backup = 'client.sru-policy-admin-' + uuid4().hex
        backup_file = '/etc/ceph/ceph.' + backup + '.keyring'
        run('ceph-mon/0', 'install', '-m', '600', '/dev/null', backup_file)
        ceph('auth', 'get-or-create', backup, '--key-type', 'aes256k',
             'mon', 'allow *', 'mgr', 'allow *', 'osd', 'allow *',
             'mds', 'allow *', '-o', backup_file)
        run('ceph-mon/0', 'ceph', '-n', backup, '-k', backup_file, 'status', '-f', 'json')

        def transfer(source, unit):
            host_file = Path(directory) / 'keyring'
            run('ceph-mon/0', 'chown', 'ubuntu:ubuntu', source)
            run('ceph-mon/0', 'chmod', '600', source)
            model.scp_from_unit('ceph-mon/0', source, str(host_file), model_name=target)
            host_file.chmod(0o600)
            destination = stage + '/import.keyring'
            model.scp_to_unit(unit, str(host_file), destination, model_name=target)
            run(unit, 'chmod', '600', destination)
            return destination

        # mon. first, client.admin last; the secure backup handles admin rotation.
        ordered = ['mon.'] + sorted(entities - {'mon.', 'client.admin'}) + ['client.admin']
        for entity in ordered:
            matches = [(unit, item['path']) for unit, items in locations.items()
                       for item in items if item['entity'] == entity]
            service = None
            if entity in daemon_names:
                daemon_unit, path = next((unit, path) for unit, path in matches
                                         if '/var/lib/ceph/' in path)
                kind, name = entity.split('.', 1)
                service = 'ceph-' + kind + '@' + name + '.service'
                previous_invocation = run(daemon_unit, 'systemctl', 'show', service,
                                          '--property=InvocationID', '--value').strip()
                run(daemon_unit, 'systemctl', 'stop', service)
                if kind == 'osd':
                    ceph('osd', 'down', name)
            rotated = stage + '/rotated.keyring'
            run('ceph-mon/0', 'install', '-m', '600', '/dev/null', rotated)
            run('ceph-mon/0', 'ceph', '-n', backup, '-k', backup_file,
                'auth', 'rotate', '--key-type', 'aes256k', entity, '-o', rotated)
            for unit, path in matches:
                imported = transfer(rotated, unit)
                run(unit, 'ceph-authtool', '--import-keyring', imported, path)
                python(unit, CHECK_LOCAL, imported, path, entity)
                if entity.startswith('osd.') and '/var/lib/ceph/osd/' in path:
                    # The pinned charm uses ceph-volume LVM BlueStore OSDs.
                    devices = json.loads(python(unit, (
                        "import json, subprocess, sys; "
                        "items = json.loads(subprocess.check_output("
                        "['ceph-volume', 'lvm', 'list', sys.argv[1], '--format', 'json'], text=True)); "
                        "print(json.dumps([x['lv_path'] for x in items.get(sys.argv[1], []) "
                        "if x.get('type') == 'block']))"
                    ), entity[4:]))
                    if len(devices) != 1:
                        raise RuntimeError('Missing unique BlueStore block LV for ' + entity)
                    # A status hook may start the OSD during SCP/import.
                    # Stop it and update its label in one unit execution.
                    guest(unit, shlex.join(('systemctl', 'stop', service)) + '\n' +
                          shlex.join(('ceph-bluestore-tool', '--dev', devices[0],
                                      'set-label-key', '--key', 'osd_key', '-v', imported)))
            if entity == 'mon.':
                mon_service = 'ceph-mon@' + monmap['mons'][0]['name'] + '.service'
                run('ceph-mon/0', 'systemctl', 'reset-failed', mon_service)
                run('ceph-mon/0', 'systemctl', 'restart', mon_service)
            elif service:
                # A status hook can start an inactive daemon before key import.
                # An explicit restart is required to load the new credential.
                # Planned maintenance can reach Ceph's three-starts/30min limit.
                run(daemon_unit, 'systemctl', 'reset-failed', service)
                run(daemon_unit, 'systemctl', 'restart', service)
                invocation = run(daemon_unit, 'systemctl', 'show', service,
                                 '--property=InvocationID', '--value').strip()
                if not invocation or invocation == previous_invocation:
                    raise RuntimeError('Daemon did not start a new invocation: ' + entity)
                # A replacement MDS must authenticate and receive a new global
                # ID, not merely inherit the monitor's stale active map entry.
                replaced_mds = (name, mds_gids[name]) if kind == 'mds' else None
                ready(replaced_mds)
        ceph('auth', 'del', backup)
        run('ceph-mon/0', 'rm', '-f', '--', backup_file)
        after = stage + '/auth-after.json'
        guest('ceph-mon/0', 'umask 077; ceph auth ls -f json > ' + shlex.quote(after))
        python('ceph-mon/0', CHECK_REPLACED, private, after)
        for unit in UNITS:
            for item in json.loads(python(unit, LOCAL_KEYS)):
                if item['type'] != 2:
                    raise RuntimeError('Non-aes256k installed key: ' + unit + ':' + item['entity'])
        ceph('mon', 'set', 'auth_service_cipher', 'aes256k')
        # Explicitly destructive disposable-lab acceleration; never production.
        ceph('auth', 'wipe-rotating-service-keys')
        ready()
        checks = json.loads(ceph('health', 'detail', '-f', 'json'))['checks']
        for check in ('AUTH_INSECURE_SERVICE_KEY_TYPE', 'AUTH_INSECURE_SERVICE_TICKETS',
                      'AUTH_INSECURE_ROTATING_SERVICE_KEY_TYPE'):
            if check in checks:
                raise RuntimeError('Unresolved authentication health check: ' + check)
    copy_admin_files(target)
    versions(VERSION)
    expected['SRU_POLICY_ORIGIN'] = args.origin
    expected['SRU_POLICY_ROTATED'] = True
    args.environment.write_text(json.dumps(expected, indent=2) + '\n')
    print('Prepared ' + args.origin + ' cluster; run EnforcedPolicyTest to enforce aes256k-only')


if __name__ == '__main__':
    try:
        main()
    finally:
        zaza.clean_up_libjuju_thread()
