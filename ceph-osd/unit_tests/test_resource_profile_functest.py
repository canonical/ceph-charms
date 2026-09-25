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

"""Test the functional-test oracle without Juju or a live cluster."""

from copy import deepcopy
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import Mock

import jinja2
import yaml

# Avoid colliding with hooks/resource_profiles.py imported by other unit tests.
path = Path(__file__).parents[1] / 'tests/resource_profiles.py'
spec = importlib.util.spec_from_file_location('epa_functest', path)
functest = importlib.util.module_from_spec(spec)
spec.loader.exec_module(functest)


class RolloutEvidenceTest(unittest.TestCase):

    def setUp(self):
        self.units = ['ceph-osd/8', 'ceph-osd/3', 'ceph-osd/12']
        self.generation = {
            unit: {'start': str(i * 10 + 1), 'done': str(i * 10 + 5)}
            for i, unit in enumerate(self.units)}

    def test_group_generations_preserves_failed_marker(self):
        self.assertEqual(functest.generations({
            'osd-resource_ceph-osd/3_abc_failed': 'masked',
            'osd-resource_ceph-osd/3_abc_done': '25',
            'osd-resource_ceph-osd/3_def_start': '30',
        }), {'abc': {'ceph-osd/3': {'failed': 'masked', 'done': '25'}},
             'def': {'ceph-osd/3': {'start': '30'}}})

    def test_serialized_uses_explicit_host_order_not_unit_number(self):
        functest.assert_serialized(self.generation, self.units)

    def test_retained_failure_marker_does_not_hide_recovery(self):
        self.generation[self.units[1]]['failed'] = 'old failure'
        functest.assert_serialized(self.generation, self.units)

    def test_unfinished_middle_is_not_success(self):
        del self.generation[self.units[1]]['done']
        with self.assertRaises(AssertionError):
            functest.assert_serialized(self.generation, self.units)

    def test_missing_noop_participant_is_not_success(self):
        del self.generation[self.units[-1]]
        with self.assertRaises(AssertionError):
            functest.assert_serialized(self.generation, self.units)

    def test_overlap_is_not_success(self):
        self.generation[self.units[1]]['start'] = '4'
        with self.assertRaises(AssertionError):
            functest.assert_serialized(self.generation, self.units)

    def test_backwards_timestamp_is_not_success(self):
        self.generation[self.units[0]]['done'] = '0'
        with self.assertRaises(AssertionError):
            functest.assert_serialized(self.generation, self.units)

    def test_runtime_ignores_only_observation_metadata(self):
        before = {'time': 1, 'hostname': 'host', 'boot-id': 'boot',
                  'claims': ['cpu-claim'], 'osds': {'0': {'pid': 5}}}
        after = deepcopy(before)
        after['time'] = 2
        self.assertEqual(functest.runtime(before), functest.runtime(after))
        for key, value in [('boot-id', 'new-boot'), ('claims', []),
                           ('osds', {'0': {'pid': 6}})]:
            changed = dict(after, **{key: value})
            self.assertNotEqual(functest.runtime(before),
                                functest.runtime(changed))

    @staticmethod
    def event(stamp, message, service='ceph-osd@7.service'):
        return {'__REALTIME_TIMESTAMP': str(stamp * 1000000),
                'MESSAGE': message, 'UNIT': service}

    def test_restart_windows_excludes_initial_start_and_injected_stop(self):
        journal = [
            self.event(1, 'Stopping ceph-osd@7.service - injected fault'),
            self.event(5, 'Started ceph-osd@7.service - recovery'),
            self.event(7, 'Stopping ceph-osd@7.service - rollout'),
            self.event(8, 'Started ceph-osd@7.service - rollout'),
        ]
        self.assertEqual(functest.restart_windows(journal, since=3),
                         [(7, 8, 'ceph-osd@7.service')])

    def test_successful_roll_requires_every_osd_pid_to_change(self):
        case = functest.ResourceProfileRolloutTest()
        case.units = self.units
        old_host = {'osds': {'7': {'systemd': {'MainPID': '50'}}}}
        case._probe = Mock(return_value=old_host)
        case._markers = Mock(side_effect=[{}, {'new': self.generation}])
        case._reset_counters = Mock()
        case._config = Mock()
        case._wait = Mock()
        case._assert_journals = Mock()
        # Markers alone are not enough: every OSD must actually restart.
        case._assert_allocations = Mock(return_value={
            u: deepcopy(old_host) for u in self.units})
        with self.assertRaises(AssertionError):
            case._roll('minimal')
        case._assert_journals.assert_not_called()

    def test_incomplete_restart_is_not_success(self):
        with self.assertRaises(AssertionError):
            functest.restart_windows([
                self.event(7, 'Stopping ceph-osd@7.service')], since=0)

    def test_start_failure_closes_restart_window(self):
        self.assertEqual(functest.restart_windows([
            self.event(7, 'Stopping ceph-osd@7.service'),
            self.event(8, 'Failed to start ceph-osd@7.service'),
        ], since=0), [(7, 8, 'ceph-osd@7.service')])


class BundleTemplateTest(unittest.TestCase):

    def setUp(self):
        path = Path(__file__).parents[1] / (
            'tests/resource-profiles/bundles/noble-resource-profiles.yaml.j2')
        self.template = jinja2.Environment(
            undefined=jinja2.StrictUndefined).from_string(path.read_text())
        self.artifacts = {
            'TEST_EPA_OSD_CHARM': '/tmp/ceph-osd.charm',
            'TEST_EPA_SNAP': '/tmp/epa.snap',
        }

    def test_each_local_artifact_is_mandatory(self):
        for missing in self.artifacts:
            with self.subTest(missing=missing):
                values = dict(self.artifacts)
                del values[missing]
                with self.assertRaises(jinja2.UndefinedError):
                    self.template.render(**values)

    def test_local_artifacts_and_three_distinct_osd_hosts(self):
        bundle = yaml.safe_load(self.template.render(**self.artifacts))
        mon = bundle['applications']['ceph-mon']
        osd = bundle['applications']['ceph-osd']
        self.assertEqual(mon['charm'], 'ch:ceph-mon')
        self.assertEqual(mon['channel'], 'tentacle/edge')
        self.assertEqual(osd['charm'], '/tmp/ceph-osd.charm')
        self.assertEqual(osd['resources'],
                         {'epa-orchestrator': '/tmp/epa.snap'})
        self.assertEqual(osd['num_units'], 3)
        self.assertEqual(len(set(osd['to'])), 3)
        self.assertFalse(bundle['local_overlay_enabled'])


class FailureCleanupTest(unittest.TestCase):

    def setUp(self):
        self.case = functest.ResourceProfileRolloutTest()
        self.case.masked = ('ceph-osd/3', 'ceph-osd@7.service')
        self.case._run = Mock()

    def test_unmask_repairs_without_manually_starting_osd(self):
        self.case._unmask()
        self.case._run.assert_called_once_with(
            'ceph-osd/3', 'systemctl unmask ceph-osd@7.service && '
            'systemctl daemon-reload && '
            'systemctl reset-failed ceph-osd@7.service')
        self.assertIsNone(self.case.masked)

    def test_failed_repair_retains_mask_identity_for_cleanup_retry(self):
        self.case._run.side_effect = RuntimeError('temporary agent failure')
        with self.assertRaises(RuntimeError):
            self.case._unmask()
        self.assertEqual(self.case.masked,
                         ('ceph-osd/3', 'ceph-osd@7.service'))

    def test_restore_unmasks_before_reconciling_and_restoring_config(self):
        calls = Mock()
        self.case.changed = True
        self.case.original = {
            'performance-profile': {'value': 'unmanaged'},
            'suppress-profile-warnings': {'value': False},
        }
        self.case._markers = Mock(return_value={})
        self.case.model = 'test-model'
        self.case.zaza = Mock()
        self.case.zaza.get_application_config.return_value = {
            'performance-profile': {'value': 'balanced'},
            'suppress-profile-warnings': {'value': True},
        }
        for name in ['_unmask', '_reset_counters', '_retry', '_wait',
                     '_config']:
            method = Mock()
            setattr(self.case, name, method)
            calls.attach_mock(method, name)
        self.case._restore()
        names = [call[0] for call in calls.mock_calls]
        self.assertEqual(names, ['_unmask', '_reset_counters', '_retry',
                                 '_wait', '_config', '_wait'])
        self.case._config.assert_called_once_with(**{
            'performance-profile': 'unmanaged',
            'suppress-profile-warnings': False})
        # Unmanaged retains the applied balanced allocation. Restoration must
        # observe the requested profile, and reject stale pre-config state.
        self.case.units = ['ceph-osd/3']
        self.case._active = Mock(return_value=True)
        self.case._allocation_status = Mock(return_value={
            'profile': 'balanced', 'requested-profile': 'balanced'})
        predicate = self.case._wait.call_args_list[-1].args[1]
        self.assertFalse(predicate())
        self.case._allocation_status.return_value['requested-profile'] = (
            'unmanaged')
        self.assertTrue(predicate())
