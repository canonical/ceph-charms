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

"""Opt-in Zaza tests of resource rollouts on three disposable OSD hosts.

Never run this disruptive suite against a production or shared model.
See resource-profiles/README.md for the local-snap deployment recipe.
"""

import asyncio
import base64
import json
import logging
import os
from pathlib import Path
import shlex
import time
import unittest
import uuid


LOG = logging.getLogger(__name__)


def generations(markers):
    """Group monitor markers without assuming unit IDs or generation IDs."""
    result = {}
    for key, value in markers.items():
        unit, generation, phase = key.removeprefix('osd-resource_').rsplit(
            '_', 2)
        result.setdefault(generation, {}).setdefault(unit, {})[phase] = value
    return result


def assert_serialized(generation, units):
    """Require every participant to finish before its successor starts."""
    assert set(generation) == set(units), 'Missing/extra rollout participant'
    previous = None
    for unit in units:
        markers = generation[unit]
        assert 'start' in markers and 'done' in markers, (unit, markers)
        start, done = float(markers['start']), float(markers['done'])
        assert start < done, (unit, 'completion precedes start')
        assert previous is None or previous < start, (unit, 'overlapping turn')
        previous = done


def runtime(snapshot):
    """Ignore probe timestamps, not process identity or claims."""
    return {k: snapshot[k] for k in ('boot-id', 'claims', 'osds')}


def restart_windows(journal, since):
    """Extract real stop/start windows, excluding pre-rollout fault injection.

    Initial service starts and refused restarts of an already stopped masked
    service are not stop/start windows. A separate runtime assertion verifies
    recovery of the masked OSD.
    """
    pending, windows = {}, []
    for event in journal:
        stamp = int(event['__REALTIME_TIMESTAMP']) / 1e6
        if stamp < since:
            continue
        message = event.get('MESSAGE', '')
        unit = event.get('UNIT')
        if not unit or not isinstance(message, str):
            continue
        if message.startswith('Stopping ceph-osd@'):
            pending[unit] = stamp
        elif message.startswith(('Started ceph-osd@',
                                 'Failed to start ceph-osd@')):
            if unit in pending:
                windows.append((pending.pop(unit), stamp, unit))
    assert not pending, 'Unfinished systemd restart: {}'.format(pending)
    return windows


async def _bounded_action(model, unit):
    import zaza.model
    return await asyncio.wait_for(zaza.model.async_run_action(
        unit, 'resource-allocation-status', model_name=model,
        action_params={'format': 'json'}, raise_on_failure=True), timeout=90)


class ResourceProfileRolloutTest(unittest.TestCase):
    """One dependent scenario, not order-dependent individual test methods."""

    timeout = 600

    def setUp(self):
        """Verify topology and register repair before making any changes."""
        import zaza.model
        self.zaza = zaza.model
        self.model = self.zaza.get_juju_model()
        self.units = []
        self.masked = None
        self.changed = False
        self.artifacts = Path(os.environ.get(
            'TEST_EPA_ARTIFACTS', '/tmp/ceph-epa-functest')) / uuid.uuid4().hex
        self.artifacts.mkdir(parents=True)
        LOG.info('EPA functional-test evidence: %s', self.artifacts)
        status = self.zaza.get_status(model_name=self.model)
        osds = status.applications['ceph-osd'].units
        self.assertEqual(len(osds), 3, 'Requires exactly three OSD units')
        self.assertEqual(len({u.machine for u in osds.values()}), 3,
                         'Requires distinct OSD machines')
        self.mon = sorted(status.applications['ceph-mon'].units)[0]
        initial = {unit: self._probe(unit, 'snapshot') for unit in osds}
        self.assertEqual(len({s['hostname'] for s in initial.values()}), 3)
        self.units = sorted(initial, key=lambda u: (initial[u]['hostname'], u))
        self.assertTrue(all(s['osds'] for s in initial.values()),
                        'Every participant must have a real OSD')
        self.original = self.zaza.get_application_config(
            'ceph-osd', model_name=self.model)
        self.addCleanup(self._restore)
        self.addCleanup(self._final_evidence)
        self._wait('initial healthy OSDs', lambda: self._active(
            self.original['performance-profile']['value']))
        self._reset_counters()
        self.changed = True
        self._config(**{'performance-profile': 'balanced'})
        self._wait('balanced baseline', lambda: self._active('balanced'))
        self._snapshot('baseline')
        self._assert_allocations()

    def _save(self, name, value):
        (self.artifacts / (name + '.json')).write_text(
            json.dumps(value, indent=2, sort_keys=True))

    def _run(self, unit, command):
        result = self.zaza.run_on_unit(
            unit, command, model_name=self.model, timeout=90)
        self.assertEqual(str(result['Code']), '0', (unit, command, result))
        return result['Stdout']

    def _probe(self, unit, mode, *args):
        source = Path(__file__).with_name('resource_profile_probe.py')
        code = 'import base64; exec(base64.b64decode({!r}))'.format(
            base64.b64encode(source.read_bytes()).decode())
        command = 'python3 -c {} {}'.format(
            shlex.quote(code), shlex.join([mode, *args]))
        return json.loads(self._run(unit, command))

    def _markers(self):
        return generations(self._probe(self.mon, 'markers'))

    def _status(self):
        status = self.zaza.get_status(model_name=self.model)
        rows = {unit: {'workload': u.workload_status.status,
                       'message': u.workload_status.info,
                       'agent': u.agent_status.status}
                for unit, u in status.applications['ceph-osd'].units.items()}
        self._save('last-status', rows)
        self.assertFalse(any(r['workload'] == 'error' or r['agent'] == 'error'
                             for r in rows.values()), rows)
        return rows

    def _active(self, profile):
        rows = self._status()
        return set(rows) == set(self.units) and all(
            r['workload'] == 'active' and r['agent'] == 'idle'
            and (profile == 'unmanaged'
                 or 'profile {} applied'.format(profile) in r['message'])
            for r in rows.values())

    def _wait(self, description, predicate):
        LOG.info('Waiting for %s', description)
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(5)
        self.fail('Timed out waiting for {}; evidence: {}'.format(
            description, self.artifacts))

    def _config(self, **values):
        self.zaza.set_application_config(
            'ceph-osd', {k: str(v).lower() if isinstance(v, bool) else str(v)
                         for k, v in values.items()}, model_name=self.model)

    def _snapshot(self, name):
        data = {u: self._probe(u, 'snapshot') for u in self.units}
        self._save(name + '-hosts', data)
        self._save(name + '-markers', self._markers())
        self._save(name + '-status', self._status())
        return data

    def _reset_counters(self):
        for unit in self.units:
            self._run(unit, 'for d in /var/lib/ceph/osd/ceph-*; do '
                      'systemctl reset-failed "ceph-osd@${d##*-}.service" '
                      '|| exit; done')

    def _allocation_status(self, unit):
        action = self.zaza.sync_wrapper(_bounded_action)(self.model, unit)
        return json.loads(action.data['results']['message'])

    def _assert_allocations(self):
        snapshots = {u: self._probe(u, 'snapshot') for u in self.units}
        for unit, snapshot in snapshots.items():
            status = self._allocation_status(unit)
            self._save(unit.replace('/', '-') + '-allocation', status)
            self.assertFalse(status['errors'], status)
            self.assertFalse(status['drift'], status)
            self.assertFalse(status['warnings'], status)
            self.assertEqual(status['rollout']['status'], 'complete')
            claims = {a['service_name']: a for a in snapshot['claims']}
            used = set()
            for osd, data in snapshot['osds'].items():
                claim = claims['ceph-osd.' + osd]
                self.assertEqual(claim['preemption_policy'], 'non-preemptive')
                self.assertEqual(data['systemd']['ActiveState'], 'active')
                self.assertTrue(data['affinities'], data)
                cpu_sets = data['affinities']
                self.assertEqual(len(cpu_sets), 1, data)
                cpus = set(cpu_sets[0])
                granted = set()
                for part in claim['allocated_cores'].split(','):
                    lo, _, hi = part.partition('-')
                    granted.update(range(int(lo), int(hi or lo) + 1))
                self.assertEqual(cpus, granted)
                self.assertFalse(used & cpus, 'Overlapping OSD allocations')
                used.update(cpus)
                self.assertIn('CPUAffinity=' + claim['allocated_cores'],
                              data['dropin'])
        return snapshots

    def _completed_after(self, before, profile):
        markers = self._markers()
        new = set(markers) - set(before)
        return new and all(
            set(markers[g]) == set(self.units)
            and all('done' in v for v in markers[g].values()) for g in new
        ) and self._active(profile)

    def _roll(self, profile):
        before = self._markers()
        hosts = {u: self._probe(u, 'snapshot') for u in self.units}
        self._reset_counters()
        self._config(**{'performance-profile': profile})
        self._wait(profile + ' rollout',
                   lambda: self._completed_after(before, profile))
        after = self._markers()
        self.assertEqual(len(set(after) - set(before)), 1)
        generation = after[(set(after) - set(before)).pop()]
        assert_serialized(generation, self.units)
        applied = self._assert_allocations()
        for unit in self.units:
            self.assertEqual(set(hosts[unit]['osds']),
                             set(applied[unit]['osds']))
            for osd, previous in hosts[unit]['osds'].items():
                current = applied[unit]['osds'][osd]
                self.assertNotEqual(previous['systemd']['MainPID'],
                                    current['systemd']['MainPID'])
        self._assert_journals(hosts, float(generation[self.units[0]]['start']),
                              last_noop=False)
        return generation

    def _fenced(self, gen, queued=False):
        first, middle, last = self.units
        markers = self._markers()
        rows = self._status()
        turn = markers.get(gen, {})
        return ('done' in turn.get(first, {})
                and 'failed' in turn.get(middle, {})
                and 'done' not in turn.get(middle, {}) and last not in turn
                and all(r['agent'] == 'idle' for r in rows.values())
                and rows[first]['workload'] == (
                    'waiting' if queued else 'active')
                and rows[middle]['workload'] == 'blocked'
                and rows[last]['workload'] == 'blocked'
                and 'rollout stopped at ' + middle in rows[last]['message'])

    def test_rollout_and_failed_middle_host(self):
        """Roll A-B-A, fence a failed middle turn, then drain queued work."""
        minimal = self._roll('minimal')
        self._snapshot('minimal')
        self._roll('balanced')
        self._reset_counters()  # Test the mask, not the 3-starts/30min limit.
        before = self._snapshot('before-fault')
        old = self._markers()
        first, middle, last = self.units
        osd = min(before[middle]['osds'], key=int)
        service = 'ceph-osd@{}.service'.format(osd)
        self.masked = (middle, service)  # Register repair BEFORE masking.
        self._run(middle, 'systemctl mask --now ' + shlex.quote(service))
        self._config(**{'performance-profile': 'minimal'})
        self._wait('new failure generation',
                   lambda: bool(set(self._markers()) - set(old)))
        current = self._markers()
        self.assertEqual(len(set(current) - set(old)), 1)
        gen = (set(current) - set(old)).pop()
        self._wait('middle host fenced', lambda: self._fenced(gen))
        failed = self._snapshot('fenced')
        turn = self._markers()[gen]
        self.assertIn('systemd refused to restart osd.' + osd,
                      turn[middle]['failed'])
        self.assertLess(float(turn[first]['done']),
                        float(turn[middle]['start']))
        self.assertNotEqual(turn[first]['start'], minimal[first]['start'])
        self.assertEqual(
            failed[middle]['osds'][osd]['systemd']['MainPID'], '0')
        self.assertEqual(runtime(before[last]), runtime(failed[last]))
        for key in before[first]['osds']:
            self.assertNotEqual(
                before[first]['osds'][key]['systemd']['MainPID'],
                failed[first]['osds'][key]['systemd']['MainPID'])

        self._config(**{'performance-profile': 'balanced'})
        self._wait('queued request remains fenced',
                   lambda: self._fenced(gen, queued=True))
        queued = self._snapshot('queued')
        queued_markers = self._markers()
        self.assertEqual(set(queued_markers), set(current))
        self.assertEqual(runtime(failed[first]), runtime(queued[first]))
        self.assertEqual(runtime(before[last]), runtime(queued[last]))

        self._unmask()
        self._retry()  # Do NOT manually start the stopped OSD.
        self._wait('old generation and queued request drain',
                   lambda: self._completed_after(queued_markers, 'balanced'))
        recovered = self._snapshot('recovered')
        markers = self._markers()
        new = set(markers) - set(queued_markers)
        self.assertEqual(len(new), 1)
        successor = markers[new.pop()]
        assert_serialized(markers[gen], self.units)
        assert_serialized(successor, self.units)
        self.assertEqual(markers[gen][first], queued_markers[gen][first])
        self.assertLess(float(markers[gen][last]['done']),
                        float(successor[first]['start']))
        self.assertEqual(runtime(before[last]), runtime(recovered[last]))
        self.assertEqual(queued[middle]['osds'][osd]['dropin'],
                         recovered[middle]['osds'][osd]['dropin'])
        self.assertNotEqual(
            recovered[middle]['osds'][osd]['systemd']['MainPID'], '0')
        self._assert_allocations()
        self._assert_journals(before, float(turn[first]['start']))
        self._wait('Ceph HEALTH_OK', lambda: json.loads(self._run(
            self.mon, 'ceph status --format=json'))['health']['status']
            == 'HEALTH_OK')

    def _assert_journals(self, before, since, last_noop=True):
        windows = []
        for unit in self.units:
            journal = self._probe(unit, 'journal', str(before[unit]['time']))
            self._save('{}-journal-{}'.format(
                unit.replace('/', '-'), int(since)), journal)
            local = restart_windows(journal, since)
            if last_noop and unit == self.units[-1]:
                self.assertFalse(local, 'Fenced/no-op last host restarted')
            elif not last_noop:
                self.assertEqual({w[2] for w in local}, {
                    'ceph-osd@{}.service'.format(osd)
                    for osd in before[unit]['osds']})
            windows.extend((a, b, unit, service) for a, b, service in local)
        self.assertTrue(windows, 'No actual restart evidence captured')
        windows.sort()
        for i, a in enumerate(windows):
            for b in windows[i + 1:]:
                if a[2] != b[2]:
                    self.assertLessEqual(
                        a[1], b[0], 'Cross-host restart overlap')
        self._save('restart-windows-{}'.format(int(since)), windows)

    def _unmask(self):
        if self.masked:
            unit, service = self.masked
            self._run(unit, 'systemctl unmask {0} && '
                      'systemctl daemon-reload && '
                      'systemctl reset-failed {0}'.format(
                          shlex.quote(service)))
            self.masked = None

    def _retry(self):
        config = self.zaza.get_application_config(
            'ceph-osd', model_name=self.model)
        self._config(**{
            'performance-profile': 'balanced',
            'suppress-profile-warnings': not config[
                'suppress-profile-warnings']['value'],
        })

    def _final_evidence(self):
        # unittest cleanups run LIFO: capture failures BEFORE repairing them.
        try:
            self._snapshot('before-cleanup')
        except Exception:
            LOG.exception('Could not capture final evidence at %s',
                          self.artifacts)

    def _restore(self):
        """Repair failed tests; never hide a cleanup failure with a pass."""
        self._unmask()
        if not self.changed:
            return
        self._reset_counters()
        before = self._markers()
        self._retry()
        # A fresh completion barrier prevents a stale active status from
        # letting restoration overtake an unfinished recovery generation.
        self._wait('cleanup recovery',
                   lambda: self._completed_after(before, 'balanced'))
        current = self.zaza.get_application_config(
            'ceph-osd', model_name=self.model)
        restore = {k: self.original[k]['value'] for k in (
            'performance-profile', 'suppress-profile-warnings')}
        if all(current[k]['value'] == v for k, v in restore.items()):
            return
        before = self._markers()
        self._config(**restore)
        profile = restore['performance-profile']
        if profile == 'unmanaged':
            # Unmanaged does not create a monitor generation. Observe the
            # recorded local state as well as status to avoid a stale read.
            self._wait('unmanaged restored', lambda: (
                self._active(profile) and all(
                    self._allocation_status(u)['requested-profile'] == profile
                    for u in self.units)))
        else:
            self._wait('original profile restored',
                       lambda: self._completed_after(before, profile))
