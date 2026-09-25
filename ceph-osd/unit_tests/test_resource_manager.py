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

import copy
import subprocess
import tempfile
import unittest

from unittest.mock import patch

import epa_client
import resource_manager
import resource_profiles as rp

from host_topology import format_cpu_list, parse_cpu_list


class FakeKV(object):
    """In-memory stand-in for the charm's unit database."""

    def __init__(self):
        self.data = {}

    def get(self, key, default=None):
        return copy.deepcopy(self.data.get(key, default))

    def set(self, key, value):
        self.data[key] = copy.deepcopy(value)

    def flush(self):
        pass


class FakeEpa(object):
    """A faithful-enough stand-in for the EPA orchestrator."""

    FEATURES = ['non-preemptive-allocations']

    def __init__(self, eligible, nodes, features=None, foreign=None,
                 source='configured'):
        self.eligible = list(eligible)
        self.nodes = nodes
        self.features = list(self.FEATURES if features is None else features)
        self.claims = dict(foreign or {})
        self.source = source
        self.calls = []

    def _free(self, service=None):
        used = set()
        for name, cores in self.claims.items():
            if name == service:
                continue
            used.update(cores)
        return [cpu for cpu in self.eligible if cpu not in used]

    def list_allocations(self):
        self.calls.append(('list',))
        return {
            'supported_cpu_features': self.features,
            'cpu_pool': {
                'source': self.source,
                'configured_cpus': format_cpu_list(self.eligible),
                'eligible_cpus': format_cpu_list(self.eligible),
                'unavailable_allocated_cpus': '',
            },
            'allocations': [
                {'service_name': name,
                 'allocated_cores': format_cpu_list(cores),
                 'cores_count': len(cores),
                 'preemption_policy': 'non-preemptive',
                 'is_explicit': True}
                for name, cores in sorted(self.claims.items())
            ],
        }

    def wait_ready(self, **kwargs):
        return self.list_allocations()

    def supports_non_preemptive(self, listing=None):
        return 'non-preemptive-allocations' in self.features

    def allocate_numa_cores(self, service, numa_node, count):
        self.calls.append(('numa', service, numa_node, count))
        if count == -1:
            self.claims.pop(service, None)
            return []
        node_cpus = set(self.nodes.get(numa_node, []))
        candidates = [c for c in self._free(service) if c in node_cpus]
        if len(candidates) < count:
            raise epa_client.EpaRequestError(
                'NUMA node {} only has {} eligible CPUs, but {} were '
                'requested'.format(numa_node, len(candidates), count))
        current = set(self.claims.get(service, []))
        own = [c for c in current if c in candidates]
        chosen = list(own[:count])
        for cpu in candidates:
            if len(chosen) >= count:
                break
            if cpu not in chosen:
                chosen.append(cpu)
        # EPA replaces only this service's CPUs on the requested NUMA node;
        # claims on other nodes remain owned by the service.
        self.claims[service] = sorted(
            (current - node_cpus) | set(chosen))
        return sorted(chosen)

    def allocate_cores(self, service, count):
        self.calls.append(('cores', service, count))
        if count == -1:
            self.claims.pop(service, None)
            return []
        candidates = self._free(service)
        if len(candidates) < count:
            raise epa_client.EpaRequestError(
                'Insufficient CPUs available. Requested: {}, Available: '
                '{}'.format(count, len(candidates)))
        chosen = sorted(candidates)[:count]
        self.claims[service] = chosen
        return chosen

    def release(self, service):
        self.calls.append(('release', service))
        self.claims.pop(service, None)

    def release_numa_node(self, service, numa_node):
        self.calls.append(('release-numa', service, numa_node))


def osd(osd_id, tier=rp.TIER_NVME, node=0, aux_nodes=None, device=None):
    return rp.OsdFacts(
        osd_id=str(osd_id),
        data_device=device or '/dev/nvme{}n1'.format(osd_id),
        aux_devices=sorted(aux_nodes or {}),
        tier=tier,
        data_node=node,
        aux_nodes=aux_nodes or {},
    )


TWO_NODES = {0: list(range(0, 16)), 1: list(range(16, 32))}


class ManagerTestCase(unittest.TestCase):
    """Common wiring: fake unit database, config, snap and enforcement."""

    def setUp(self):
        super(ManagerTestCase, self).setUp()
        self.kv = FakeKV()
        self.config = {
            'performance-profile': rp.PROFILE_UNMANAGED,
            'suppress-profile-warnings': False,
            'epa-orchestrator-channel': 'latest/stable',
        }
        self.applied = []
        self.restarted = []
        self.cleared = []
        self.pool = None
        self.pools_set = []
        self.resource_path = None
        self.epa = None

        patcher = patch.object(resource_manager.resource_apply,
                               'wait_for_osd', return_value=True)
        self.wait_for_osd = patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch.object(resource_manager.resource_apply, 'stop_osd')
        self.stop_osd = patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch.object(resource_manager.resource_apply,
                               'pending_restarts', return_value=set())
        self.pending_restarts = patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch.object(resource_manager.resource_apply,
                               'verify_enforcement')
        self.verify_enforcement = patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch.object(resource_manager, '_rollout_inputs',
                               return_value={})
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch.object(resource_manager.resource_rollout, 'run',
                               side_effect=lambda inputs, callback, **kw:
                               callback())
        self.rollout_run = patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch.object(resource_manager.resource_rollout, 'observe')
        self.rollout_observe = patcher.start()
        self.addCleanup(patcher.stop)

        for name, replacement in (
                ('kv', lambda: self.kv),
                ('config', lambda key: self.config.get(key)),
                ('resource_get', lambda name: self.resource_path)):
            patcher = patch.object(resource_manager, name, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)

        patcher = patch.object(resource_manager.epa_snap, 'ensure_installed')
        self.ensure_installed = patcher.start()
        self.ensure_installed.return_value = 'present'
        self.addCleanup(patcher.stop)

        patcher = patch.object(resource_manager.epa_snap,
                               'get_configured_pool')
        self.get_configured_pool = patcher.start()
        self.get_configured_pool.side_effect = lambda: self.pool
        self.addCleanup(patcher.stop)

        patcher = patch.object(resource_manager.epa_snap, 'set_pool')
        self.set_pool = patcher.start()
        self.set_pool.side_effect = self._set_pool
        self.addCleanup(patcher.stop)

        patcher = patch.object(resource_manager.resource_apply,
                               'apply_allocations')
        self.apply_allocations = patcher.start()
        self.apply_allocations.side_effect = self._apply
        self.addCleanup(patcher.stop)

        self.real_clear_allocation = resource_manager.resource_apply.\
            clear_allocation
        patcher = patch.object(resource_manager.resource_apply,
                               'clear_allocation')
        self.clear_allocation = patcher.start()
        self.clear_allocation.side_effect = (
            lambda osd_id, **kwargs: self.cleared.append(osd_id) or True)
        self.addCleanup(patcher.stop)

    def _set_pool(self, cpus):
        self.pool = format_cpu_list(cpus)
        self.pools_set.append(self.pool)
        if self.epa is not None:
            # A configured pool is exactly what the daemon can grant.
            self.epa.eligible = parse_cpu_list(self.pool)
        return self.pool

    def _apply(self, profile, allocations, **kwargs):
        self.applied.append((profile, allocations))
        return [a['osd-id'] for a in allocations]

    def reconcile(self, profile, osds, epa, nodes=None, nic_nodes=(0,),
                  interface='eth0'):
        """Run a reconcile against fixed host facts."""
        self.config['performance-profile'] = profile
        self.epa = epa
        host = rp.HostFacts(nodes=nodes or TWO_NODES,
                            nic_interface=interface,
                            nic_nodes=set(nic_nodes))
        with patch.object(resource_manager, 'collect_osd_facts',
                          return_value=osds), \
                patch.object(resource_manager, 'collect_host_facts',
                             return_value=host):
            return resource_manager.reconcile(client_factory=lambda: epa)


class ProfileConfigTestCase(ManagerTestCase):

    def test_default_profile_is_unmanaged(self):
        self.assertEqual(resource_manager.configured_profile(),
                         rp.PROFILE_UNMANAGED)

    def test_invalid_profile_is_rejected(self):
        self.config['performance-profile'] = 'frugal'
        self.assertRaises(resource_manager.ResourceError,
                          resource_manager.configured_profile)

    def test_invalid_profile_blocks_without_allocating(self):
        epa = FakeEpa(range(0, 16), TWO_NODES)
        state = self.reconcile('frugal', [osd(0)], epa)
        self.assertIn('invalid performance-profile', state['errors'][0])
        self.assertEqual(epa.claims, {})
        self.assertEqual(self.applied, [])

    def test_affinity_protection_covers_requested_and_retained_profiles(self):
        self.assertFalse(resource_manager.affinity_protection_required())
        self.config['performance-profile'] = rp.PROFILE_MINIMAL
        self.assertTrue(resource_manager.affinity_protection_required())
        self.config['performance-profile'] = rp.PROFILE_UNMANAGED
        self.kv.set(resource_manager.STATE_KEY, {
            'profile': rp.PROFILE_BALANCED, 'osds': {'0': {}},
        })
        self.assertTrue(resource_manager.affinity_protection_required())

    def test_warnings_suppressed_config(self):
        self.assertFalse(resource_manager.warnings_suppressed())
        self.config['suppress-profile-warnings'] = True
        self.assertTrue(resource_manager.warnings_suppressed())


class UnmanagedTestCase(ManagerTestCase):

    def test_unmanaged_keeps_the_current_allocation(self):
        previous = {
            'version': 1,
            'profile': rp.PROFILE_BALANCED,
            'osds': {'0': {'osd-id': '0', 'cores': '4-11', 'mode': 'numa',
                           'numa-node': 0, 'numa-policy': 'preferred'}},
            'epa': {'charm-managed': True},
        }
        self.kv.set(resource_manager.STATE_KEY, previous)
        epa = FakeEpa(range(0, 16), TWO_NODES,
                      foreign={'ceph-osd.0': list(range(4, 12))})
        state = self.reconcile(rp.PROFILE_UNMANAGED, [osd(0)], epa)
        self.assertEqual(state['profile'], rp.PROFILE_BALANCED)
        self.assertEqual(state['requested-profile'], rp.PROFILE_UNMANAGED)
        self.assertEqual(state['osds'], previous['osds'])
        self.assertEqual(epa.claims, {'ceph-osd.0': list(range(4, 12))})
        # A legacy retained allocation receives the one-time NUMA-protection
        # activation restart even though unmanaged remains the request.
        self.assertEqual(len(self.applied), 1)
        self.assertEqual(self.applied[0][0], rp.PROFILE_BALANCED)
        self.assertEqual(self.cleared, [])
        self.assertEqual(epa.calls, [])

    def test_unmanaged_does_not_hide_an_unfinished_restart(self):
        self.pending_restarts.return_value = {'0'}
        state = self.reconcile(rp.PROFILE_UNMANAGED, [osd(0)],
                               FakeEpa(range(16), TWO_NODES))
        self.assertIn('unfinished resource restarts', state['errors'][0])
        self.assertEqual(self.applied, [])


class PerformanceReconcileTestCase(ManagerTestCase):

    def test_applies_aligned_allocations(self):
        epa = FakeEpa(range(0, 16), TWO_NODES)
        osds = [osd(0), osd(1, tier=rp.TIER_HDD, device='/dev/sdb')]
        state = self.reconcile(rp.PROFILE_PERFORMANCE, osds, epa)
        self.assertEqual(state['errors'], [])
        self.assertEqual(sorted(epa.claims), ['ceph-osd.0', 'ceph-osd.1'])
        self.assertEqual(len(epa.claims['ceph-osd.0']), 12)
        self.assertEqual(len(epa.claims['ceph-osd.1']), 4)
        self.assertEqual(state['osds']['0']['cores'], '0-11')
        self.assertEqual(state['osds']['0']['numa-policy'], 'bind')
        self.assertEqual(state['osds']['0']['data-device'], '/dev/nvme0n1')
        self.assertEqual(state['restarted'], ['0', '1'])
        profile, allocations = self.applied[0]
        self.assertEqual(profile, rp.PROFILE_PERFORMANCE)
        self.assertEqual(allocations[0]['numa-node'], 0)

    def test_refuses_a_plan_that_does_not_fit(self):
        # Two NVMe OSDs need 24 CPUs but only 16 are eligible on node 0.
        epa = FakeEpa(range(0, 16), TWO_NODES)
        osds = [osd(0), osd(1)]
        state = self.reconcile(rp.PROFILE_PERFORMANCE, osds, epa)
        self.assertEqual(len(state['errors']), 1)
        self.assertIn('needs more CPUs than the allocator can grant',
                      state['errors'][0])
        # Nothing was claimed and nothing was enforced.
        self.assertEqual(epa.claims, {})
        self.assertEqual(self.applied, [])
        self.assertEqual(state['osds'], {})

    def test_refuses_a_plan_that_is_not_numa_aligned(self):
        epa = FakeEpa(range(0, 32), TWO_NODES)
        state = self.reconcile(rp.PROFILE_PERFORMANCE, [osd(7, node=1)], epa,
                               nic_nodes=(0,))
        self.assertIn('osd.7', state['errors'][0])
        self.assertEqual(epa.claims, {})
        self.assertEqual(self.applied, [])

    def test_does_not_enforce_when_a_claim_fails(self):
        epa = FakeEpa(range(0, 32), TWO_NODES)

        def refuse(service, numa_node, count):
            raise epa_client.EpaRequestError('transient failure')

        epa.allocate_numa_cores = refuse
        state = self.reconcile(rp.PROFILE_PERFORMANCE, [osd(0)], epa)
        self.assertIn('transient failure', state['errors'][0])
        self.assertEqual(self.applied, [])

    def test_short_grant_is_an_error(self):
        epa = FakeEpa(range(0, 32), TWO_NODES)
        original = epa.allocate_numa_cores
        epa.allocate_numa_cores = (
            lambda service, node, count: original(service, node, count - 1))
        state = self.reconcile(rp.PROFILE_PERFORMANCE, [osd(0)], epa)
        self.assertIn('granted 11 of 12', state['errors'][0])
        self.assertEqual(self.applied, [])

    def test_missing_non_preemptive_support_blocks(self):
        epa = FakeEpa(range(0, 32), TWO_NODES, features=[])
        state = self.reconcile(rp.PROFILE_PERFORMANCE, [osd(0)], epa)
        self.assertIn('non-preemptive', state['errors'][0])
        self.assertEqual(epa.claims, {})

    def test_unreachable_allocator_blocks(self):
        class Unreachable(object):
            def wait_ready(self, **kwargs):
                raise epa_client.EpaUnavailable('socket missing')

        state = self.reconcile(rp.PROFILE_PERFORMANCE, [osd(0)],
                               Unreachable())
        self.assertIn('socket missing', state['errors'][0])
        self.assertEqual(self.applied, [])

    def test_foreign_claims_are_never_taken(self):
        epa = FakeEpa(range(0, 16), TWO_NODES,
                      foreign={'nova-compute': [0, 1, 2, 3, 4]})
        state = self.reconcile(rp.PROFILE_PERFORMANCE, [osd(0)], epa)
        self.assertIn('needs more CPUs', state['errors'][0])
        self.assertEqual(epa.claims, {'nova-compute': [0, 1, 2, 3, 4]})

    def test_no_local_osds_is_a_no_op(self):
        epa = FakeEpa(range(0, 16), TWO_NODES)
        state = self.reconcile(rp.PROFILE_PERFORMANCE, [], epa)
        self.assertEqual(state['osds'], {})
        self.assertEqual(state['errors'], [])
        self.assertEqual(self.applied, [])

    def test_refusal_preserves_the_last_applied_allocation(self):
        epa = FakeEpa(range(0, 32), TWO_NODES)
        applied = self.reconcile(rp.PROFILE_BALANCED, [osd(0)], epa)
        expected = resource_manager.resource_apply.render_dropin(
            applied['profile'], applied['osds']['0']['cores'],
            numa_policy=applied['osds']['0']['numa-policy'],
            numa_node=applied['osds']['0']['numa-node'])

        # Performance is impossible because the data and network NUMA nodes
        # differ. The OSD's balanced allocation remains live and claimable
        # only by its original EPA service.
        refused = self.reconcile(
            rp.PROFILE_PERFORMANCE, [osd(0, node=1)], epa, nic_nodes=(0,))
        self.assertTrue(refused['errors'])
        self.assertEqual(refused['profile'], rp.PROFILE_BALANCED)
        self.assertEqual(refused['requested-profile'], rp.PROFILE_PERFORMANCE)
        self.assertEqual(refused['osds'], applied['osds'])
        with patch.object(resource_manager.resource_apply, 'read_dropin',
                          return_value=expected):
            self.assertEqual(resource_manager.verify(lambda: epa), [])


class BalancedReconcileTestCase(ManagerTestCase):

    def test_degrades_to_any_node(self):
        # Nothing eligible on node 1, where the OSD's device lives.
        epa = FakeEpa(range(0, 16), TWO_NODES)
        state = self.reconcile(rp.PROFILE_BALANCED, [osd(0, node=1)], epa)
        self.assertEqual(state['errors'], [])
        self.assertEqual(len(epa.claims['ceph-osd.0']), 8)
        self.assertEqual(state['osds']['0']['mode'], rp.MODE_ANY)
        self.assertIsNone(state['osds']['0']['numa-policy'])
        self.assertTrue(any('retrying without NUMA locality' in warning
                            for warning in state['warnings']))
        self.assertEqual(state['restarted'], ['0'])

    def test_degrades_to_a_smaller_allocation(self):
        epa = FakeEpa([0, 1, 2], {0: [0, 1, 2], 1: []})
        state = self.reconcile(rp.PROFILE_BALANCED, [osd(0, node=1)], epa,
                               nodes={0: [0, 1, 2], 1: []})
        self.assertEqual(state['errors'], [])
        self.assertEqual(epa.claims['ceph-osd.0'], [0, 1, 2])
        self.assertEqual(state['osds']['0']['count'], 3)
        self.assertTrue(any('allocated 3 of 8' in warning
                            for warning in state['warnings']))

    def test_leaves_an_osd_unpinned_without_capacity(self):
        epa = FakeEpa([], {0: [], 1: []}, foreign={})
        state = self.reconcile(rp.PROFILE_BALANCED, [osd(0, node=0)], epa,
                               nodes={0: [], 1: []})
        self.assertEqual(state['errors'], [])
        self.assertEqual(state['osds'], {})
        self.assertTrue(any('leaving this OSD unpinned' in warning
                            for warning in state['warnings']))
        self.assertEqual(self.applied, [])
        # Stale enforcement is dropped so the OSD is not pinned to CPUs
        # the charm no longer owns.
        self.assertEqual(self.cleared, ['0'])

    def test_records_planner_deviations(self):
        epa = FakeEpa(range(0, 32), TWO_NODES)
        osds = [osd(0, node=0, aux_nodes={'/dev/nvme9n1': 1})]
        state = self.reconcile(rp.PROFILE_BALANCED, osds, epa)
        self.assertTrue(any('/dev/nvme9n1' in warning
                            for warning in state['warnings']))
        self.assertEqual(len(epa.claims['ceph-osd.0']), 8)

    def test_capacity_shortfall_is_only_a_warning(self):
        epa = FakeEpa(range(0, 8), {0: list(range(0, 8)), 1: []})
        osds = [osd(0), osd(1)]
        state = self.reconcile(rp.PROFILE_BALANCED, osds, epa,
                               nodes={0: list(range(0, 8)), 1: []})
        self.assertEqual(state['errors'], [])
        self.assertTrue(any('needs 16 logical CPUs' in warning
                            for warning in state['warnings']))
        self.assertEqual(len(epa.claims['ceph-osd.0']), 8)
        # The second OSD gets whatever is left, which is nothing.
        self.assertNotIn('ceph-osd.1', epa.claims)


class MinimalReconcileTestCase(ManagerTestCase):

    def test_allocates_two_cpus_without_numa(self):
        epa = FakeEpa(range(0, 16), TWO_NODES)
        osds = [osd(0), osd(1, tier=rp.TIER_HDD, node=1, device='/dev/sdb')]
        state = self.reconcile(rp.PROFILE_MINIMAL, osds, epa)
        self.assertEqual(state['errors'], [])
        self.assertEqual(len(epa.claims['ceph-osd.0']), 2)
        self.assertEqual(len(epa.claims['ceph-osd.1']), 2)
        for record in state['osds'].values():
            self.assertEqual(record['mode'], rp.MODE_ANY)
            self.assertIsNone(record['numa-node'])
            self.assertIsNone(record['numa-policy'])
        self.assertEqual(state['warnings'], [])


class ClaimLifecycleTestCase(ManagerTestCase):

    def test_stale_claims_are_released(self):
        epa = FakeEpa(range(0, 16), TWO_NODES,
                      foreign={'ceph-osd.9': [14, 15],
                               'nova-compute': [13]})
        self.reconcile(rp.PROFILE_MINIMAL, [osd(0)], epa)
        self.assertNotIn('ceph-osd.9', epa.claims)
        self.assertIn('nova-compute', epa.claims)
        self.assertEqual(self.cleared, ['9'])

    def test_changed_placement_stops_before_releasing_the_old_claim(self):
        self.kv.set(resource_manager.STATE_KEY, {
            'version': 1,
            'profile': rp.PROFILE_PERFORMANCE,
            'osds': {'0': {'osd-id': '0', 'mode': rp.MODE_NUMA,
                           'numa-node': 0, 'cores': '0-11'}},
        })
        epa = FakeEpa(range(0, 32), TWO_NODES,
                      foreign={'ceph-osd.0': list(range(0, 12))})
        state = self.reconcile(rp.PROFILE_PERFORMANCE, [osd(0, node=1)], epa,
                               nic_nodes=(1,))
        self.stop_osd.assert_called_once_with('0')
        self.assertIn(('release', 'ceph-osd.0'), epa.calls)
        self.assertEqual(epa.claims['ceph-osd.0'], list(range(16, 28)))
        self.assertEqual(state['osds']['0']['numa-node'], 1)

    def test_strict_failure_stops_before_releasing_live_claims_and_recovers(
            self):
        self.kv.set(resource_manager.STATE_KEY, {
            'version': 1, 'profile': rp.PROFILE_PERFORMANCE,
            'requested-profile': rp.PROFILE_PERFORMANCE,
            'osds': {
                '0': {'osd-id': '0', 'mode': rp.MODE_NUMA, 'numa-node': 0,
                      'count': 12, 'cores': '0-11'},
                '1': {'osd-id': '1', 'mode': rp.MODE_NUMA, 'numa-node': 1,
                      'count': 4, 'cores': '16-19'},
            },
        })
        epa = FakeEpa(range(0, 32), TWO_NODES, foreign={
            'ceph-osd.0': list(range(0, 12)),
            'ceph-osd.1': list(range(16, 20)),
        })
        allocate = epa.allocate_numa_cores

        def fail_second(service, node, count):
            if service == 'ceph-osd.1':
                raise epa_client.EpaRequestError('second claim failed')
            return allocate(service, node, count)

        epa.allocate_numa_cores = fail_second
        transition_osds = [
            osd(0, node=1),
            osd(1, tier=rp.TIER_HDD, node=1, device='/dev/sdb'),
        ]
        state = self.reconcile(rp.PROFILE_PERFORMANCE, transition_osds,
                               epa, nic_nodes=(1,))
        self.assertIn('second claim failed', state['errors'][0])
        self.stop_osd.assert_called_once_with('0')
        self.assertIn(('release', 'ceph-osd.0'), epa.calls)
        self.assertTrue(set(range(0, 12)).isdisjoint(
            epa.claims['ceph-osd.0']))
        # The first OSD completed its stop/release/claim/restart sequence
        # before the second claim failed, so it is truthfully recorded on the
        # new CPUs and is no longer a pending transition.
        self.assertEqual(state['osds']['0']['cores'], '20-31')
        self.assertNotIn('0', state['pending-transitions'])
        # The second OSD did not mutate ownership, and a retry completes the
        # remaining strict work without stopping the migrated OSD again.
        epa.allocate_numa_cores = allocate
        recovered = self.reconcile(
            rp.PROFILE_PERFORMANCE, transition_osds, epa, nic_nodes=(1,))
        self.assertEqual(recovered['errors'], [])
        self.assertNotIn('pending-transitions', recovered)
        self.stop_osd.assert_called_once_with('0')

    def test_first_strict_claim_is_applied_before_a_later_failure(self):
        epa = FakeEpa(range(0, 16), {0: list(range(0, 16)), 1: []})
        allocate = epa.allocate_numa_cores

        def fail_second(service, node, count):
            if service == 'ceph-osd.1':
                raise epa_client.EpaRequestError('second claim failed')
            return allocate(service, node, count)

        epa.allocate_numa_cores = fail_second
        state = self.reconcile(
            rp.PROFILE_PERFORMANCE,
            [osd(0), osd(1, tier=rp.TIER_HDD)], epa,
            nodes={0: list(range(0, 16)), 1: []})
        self.assertIn('second claim failed', state['errors'][0])
        # The first EPA reservation is no longer orphaned: it was enforced
        # and durably recorded before attempting the second mutation.
        self.assertEqual(state['osds']['0']['cores'], '0-11')
        self.assertEqual(epa.claims['ceph-osd.0'], list(range(0, 12)))
        self.assertNotIn('ceph-osd.1', epa.claims)
        self.assertEqual(len(self.applied), 1)

    def test_first_strict_short_claim_is_durably_cleaned_up(self):
        epa = FakeEpa(range(0, 16), {0: list(range(0, 16)), 1: []})
        allocate = epa.allocate_numa_cores
        epa.allocate_numa_cores = (
            lambda service, node, count: allocate(service, node, count - 1))
        state = self.reconcile(
            rp.PROFILE_PERFORMANCE, [osd(0)], epa,
            nodes={0: list(range(0, 16)), 1: []})
        self.assertIn('allocator granted 11 of 12', state['errors'][0])
        self.assertNotIn('ceph-osd.0', epa.claims)
        self.assertEqual(state['pending-transitions'], {})
        self.assertEqual(self.applied, [])

    def test_flexible_first_claim_recovers_before_changed_numa_retry(self):
        epa = FakeEpa(range(0, 32), TWO_NODES)
        self.apply_allocations.side_effect = resource_manager.resource_apply.\
            ApplyError('restart failed')
        failed = self.reconcile(rp.PROFILE_BALANCED, [osd(0, node=0)], epa)
        self.assertTrue(failed['errors'])
        self.assertEqual(epa.claims['ceph-osd.0'], list(range(0, 8)))
        self.assertEqual(
            failed['pending-transitions']['0']['status'], 'allocated')

        self.apply_allocations.side_effect = self._apply
        recovered = self.reconcile(
            rp.PROFILE_BALANCED, [osd(0, node=1)], epa, nic_nodes=(1,))
        self.assertEqual(recovered['errors'], [])
        self.assertEqual(epa.claims['ceph-osd.0'], list(range(16, 24)))
        self.assertEqual(recovered['osds']['0']['cores'], '16-23')
        self.assertNotIn('pending-transitions', recovered)

    def test_resumed_live_flexible_claim_stops_before_fallback_release(self):
        self.kv.set(resource_manager.STATE_KEY, {
            'version': 1, 'profile': rp.PROFILE_UNMANAGED, 'osds': {},
            'pending-transitions': {
                '0': {'new': True, 'status': 'allocated',
                      'mode': rp.MODE_NUMA, 'numa-node': 0,
                      'requested': 8, 'cores': '0-7',
                      'profile': rp.PROFILE_BALANCED},
            },
        })
        epa = FakeEpa(range(32), TWO_NODES,
                      foreign={'ceph-osd.0': list(range(8))})
        events = []
        running = [True]
        release = epa.release

        def stop(osd_id):
            running[0] = False
            events.append(('stop', osd_id))

        def release_after_stop(service):
            events.append(('release', service, running[0]))
            release(service)
            epa.claims['competing-workload'] = list(range(8))

        def fail_numa(*args):
            raise epa_client.EpaRequestError('transient NUMA error')

        self.stop_osd.side_effect = stop
        epa.release = release_after_stop
        epa.allocate_numa_cores = fail_numa
        with patch.object(resource_manager.resource_apply, 'read_dropin',
                          return_value='enforced'):
            state = self.reconcile(rp.PROFILE_BALANCED, [osd(0)], epa)

        self.assertEqual(state['errors'], [])
        self.assertEqual(events[0], ('stop', '0'))
        self.assertEqual(events[1], ('release', 'ceph-osd.0', False))
        self.assertEqual(epa.claims['ceph-osd.0'], list(range(8, 16)))

    def test_resumed_live_flexible_stop_and_release_failures_keep_journal(
            self):
        cases = (
            ('stop', resource_manager.resource_apply.ApplyError('stop failed'),
             'stopping', 0),
            ('release', epa_client.EpaError('release failed'), 'stopped', 1),
        )
        for failure, error, status, releases in cases:
            with self.subTest(failure=failure):
                self.stop_osd.reset_mock()
                self.kv.set(resource_manager.STATE_KEY, {
                    'version': 1, 'profile': rp.PROFILE_UNMANAGED, 'osds': {},
                    'pending-transitions': {
                        '0': {'new': True, 'status': 'allocated',
                              'mode': rp.MODE_NUMA, 'numa-node': 0,
                              'requested': 8, 'cores': '0-7'},
                    },
                })
                epa = FakeEpa(range(32), TWO_NODES,
                              foreign={'ceph-osd.0': list(range(8))})
                epa.allocate_numa_cores = lambda *args: (_ for _ in ()).throw(
                    epa_client.EpaRequestError('transient NUMA error'))
                events = []
                if failure == 'stop':
                    self.stop_osd.side_effect = error
                else:
                    self.stop_osd.side_effect = lambda osd_id: events.append(
                        ('stop', osd_id))
                    epa.release = lambda service: (
                        events.append(('release', service)),
                        (_ for _ in ()).throw(error))[1]
                with patch.object(resource_manager.resource_apply,
                                  'read_dropin', return_value='enforced'):
                    state = self.reconcile(rp.PROFILE_BALANCED, [osd(0)], epa)

                durable = self.kv.get(resource_manager.STATE_KEY)
                self.assertTrue(state['errors'])
                self.assertEqual(
                    durable['pending-transitions']['0']['status'], status)
                self.assertEqual(
                    durable['pending-transitions']['0']['source']['cores'],
                    '0-7')
                self.assertEqual(
                    len([event for event in events
                         if event[0] == 'release']), releases)
                self.assertEqual(epa.claims['ceph-osd.0'], list(range(8)))

                # Re-load the durable snapshot before retrying so references
                # held by the first reconciliation cannot hide a missed save.
                self.kv.set(resource_manager.STATE_KEY, durable)
                self.stop_osd.side_effect = None
                if failure == 'release':
                    del epa.release
                with patch.object(resource_manager.resource_apply,
                                  'read_dropin', return_value='enforced'):
                    recovered = self.reconcile(
                        rp.PROFILE_BALANCED, [osd(0)], epa)
                self.assertEqual(recovered['errors'], [])
                self.assertNotIn('pending-transitions', recovered)

    def test_flexible_fallback_failure_retains_source_for_changed_retry(self):
        self.kv.set(resource_manager.STATE_KEY, {
            'version': 1, 'profile': rp.PROFILE_UNMANAGED, 'osds': {},
            'pending-transitions': {
                '0': {'new': True, 'status': 'allocated',
                      'mode': rp.MODE_NUMA, 'numa-node': 0,
                      'requested': 8, 'cores': '0-7',
                      'profile': rp.PROFILE_BALANCED},
            },
        })
        epa = FakeEpa(range(32), TWO_NODES,
                      foreign={'ceph-osd.0': list(range(8))})
        allocate_numa = epa.allocate_numa_cores
        allocate_any = epa.allocate_cores

        def fail_allocation(*args):
            raise epa_client.EpaRequestError('allocator unavailable')

        epa.allocate_numa_cores = fail_allocation
        epa.allocate_cores = fail_allocation
        with patch.object(resource_manager.resource_apply, 'read_dropin',
                          return_value='enforced'):
            failed = self.reconcile(rp.PROFILE_BALANCED, [osd(0)], epa)
        self.assertEqual(failed['errors'], [])
        durable = self.kv.get(resource_manager.STATE_KEY)
        self.assertEqual(
            durable['pending-transitions']['0']['status'], 'unallocated')
        self.assertEqual(
            durable['pending-transitions']['0']['source']['numa-node'], 0)

        # This is a serialized durable snapshot, not the mutable state object
        # returned by the prior reconcile turn.
        self.kv.set(resource_manager.STATE_KEY, durable)
        epa.allocate_numa_cores = allocate_numa
        epa.allocate_cores = allocate_any
        recovered = self.reconcile(
            rp.PROFILE_BALANCED, [osd(0, node=1)], epa, nic_nodes=(1,))
        self.assertEqual(recovered['errors'], [])
        self.assertEqual(epa.claims['ceph-osd.0'], list(range(16, 24)))
        self.assertNotIn('pending-transitions', recovered)

    def test_never_enforced_flexible_claim_is_cleaned_up_without_stop(self):
        epa = FakeEpa(range(32), TWO_NODES)

        def fail_allocation(*args):
            raise epa_client.EpaRequestError('allocator unavailable')

        epa.allocate_numa_cores = fail_allocation
        epa.allocate_cores = fail_allocation
        state = self.reconcile(rp.PROFILE_BALANCED, [osd(0)], epa)

        self.assertEqual(state['errors'], [])
        self.stop_osd.assert_not_called()
        self.assertIn(('release', 'ceph-osd.0'), epa.calls)
        self.assertNotIn('ceph-osd.0', epa.claims)
        self.assertNotIn('pending-transitions', self.kv.get(
            resource_manager.STATE_KEY))

    def test_strict_allocated_journal_without_dropin_stops_before_recovery(
            self):
        self.kv.set(resource_manager.STATE_KEY, {
            'version': 1, 'profile': rp.PROFILE_UNMANAGED, 'osds': {},
            'pending-transitions': {
                '0': {'new': True, 'status': 'allocated',
                      'mode': rp.MODE_NUMA, 'numa-node': 0,
                      'requested': 12, 'cores': '0-11',
                      'profile': rp.PROFILE_PERFORMANCE},
            },
        })
        epa = FakeEpa(range(32), TWO_NODES,
                      foreign={'ceph-osd.0': list(range(12))})
        events = []
        running = [True]
        release = epa.release

        def stop(osd_id):
            running[0] = False
            events.append(('stop', osd_id))

        def release_after_stop(service):
            events.append(('release', service, running[0]))
            release(service)

        self.stop_osd.side_effect = stop
        epa.release = release_after_stop
        with patch.object(resource_manager.resource_apply, 'read_dropin',
                          return_value=None):
            state = self.reconcile(rp.PROFILE_PERFORMANCE, [osd(0)], epa)

        self.assertEqual(state['errors'], [])
        self.assertEqual(events[0], ('stop', '0'))
        self.assertEqual(events[1], ('release', 'ceph-osd.0', False))
        self.assertEqual(epa.claims['ceph-osd.0'], list(range(12)))
        self.assertNotIn('pending-transitions', state)

    def test_strict_allocated_journal_without_dropin_failures_are_closed(self):
        cases = (
            ('stop', resource_manager.resource_apply.ApplyError('stop failed'),
             'stopping', 0),
            ('release', epa_client.EpaError('release failed'), 'stopped', 1),
        )
        for failure, error, status, releases in cases:
            with self.subTest(failure=failure):
                self.stop_osd.reset_mock()
                self.kv.set(resource_manager.STATE_KEY, {
                    'version': 1, 'profile': rp.PROFILE_UNMANAGED, 'osds': {},
                    'pending-transitions': {
                        '0': {'new': True, 'status': 'allocated',
                              'mode': rp.MODE_NUMA, 'numa-node': 0,
                              'requested': 12, 'cores': '0-11'},
                    },
                })
                epa = FakeEpa(range(32), TWO_NODES,
                              foreign={'ceph-osd.0': list(range(12))})
                events = []
                if failure == 'stop':
                    self.stop_osd.side_effect = error
                else:
                    self.stop_osd.side_effect = lambda osd_id: events.append(
                        ('stop', osd_id))
                    epa.release = lambda service: (
                        events.append(('release', service)),
                        (_ for _ in ()).throw(error))[1]
                with patch.object(resource_manager.resource_apply,
                                  'read_dropin', return_value=None):
                    state = self.reconcile(
                        rp.PROFILE_PERFORMANCE, [osd(0)], epa)

                durable = self.kv.get(resource_manager.STATE_KEY)
                self.assertTrue(state['errors'])
                self.assertEqual(
                    durable['pending-transitions']['0']['status'], status)
                self.assertEqual(
                    durable['pending-transitions']['0']['source']['cores'],
                    '0-11')
                self.assertEqual(
                    len([event for event in events
                         if event[0] == 'release']), releases)
                self.assertEqual(epa.claims['ceph-osd.0'], list(range(12)))
                self.stop_osd.side_effect = None

    def test_resumed_live_strict_claim_is_not_released_on_request_error(self):
        self.kv.set(resource_manager.STATE_KEY, {
            'version': 1, 'profile': rp.PROFILE_UNMANAGED,
            'pending-transitions': {
                '0': {'new': True, 'status': 'allocated', 'mode': rp.MODE_NUMA,
                      'numa-node': 0, 'requested': 12, 'cores': '0-11'},
            },
        })
        epa = FakeEpa(range(0, 16), {0: list(range(0, 16)), 1: []},
                      foreign={'ceph-osd.0': list(range(0, 12))})
        epa.allocate_numa_cores = lambda *args: (_ for _ in ()).throw(
            epa_client.EpaRequestError('transient request error'))
        with patch.object(resource_manager.resource_apply, 'read_dropin',
                          return_value='enforced'):
            state = self.reconcile(
                rp.PROFILE_PERFORMANCE, [osd(0)], epa,
                nodes={0: list(range(0, 16)), 1: []})
        self.assertIn('refusing to release', state['errors'][0])
        self.assertEqual(epa.claims['ceph-osd.0'], list(range(0, 12)))
        self.stop_osd.assert_not_called()

    def test_legacy_matching_dropin_forces_one_affinity_restart(self):
        self.kv.set(resource_manager.STATE_KEY, {
            'version': 1, 'profile': rp.PROFILE_PERFORMANCE,
            'osds': {'0': {'osd-id': '0', 'mode': rp.MODE_NUMA,
                           'numa-node': 0, 'count': 12, 'cores': '0-11'}},
        })
        epa = FakeEpa(range(0, 16), {0: list(range(0, 16)), 1: []},
                      foreign={'ceph-osd.0': list(range(0, 12))})
        state = self.reconcile(
            rp.PROFILE_PERFORMANCE, [osd(0)], epa,
            nodes={0: list(range(0, 16)), 1: []})
        self.assertEqual(state['affinity-protection-version'],
                         resource_manager.AFFINITY_PROTECTION_VERSION)
        self.apply_allocations.assert_called_once()
        self.assertEqual(
            self.apply_allocations.call_args[1]['force_osd_ids'], ('0',))
        self.assertTrue(
            self.apply_allocations.call_args[1]['verify_unchanged'])

    def test_unchanged_placement_keeps_the_claim(self):
        self.kv.set(resource_manager.STATE_KEY, {
            'version': 1,
            'profile': rp.PROFILE_PERFORMANCE,
            'osds': {'0': {'osd-id': '0', 'mode': rp.MODE_NUMA,
                           'numa-node': 0, 'cores': '0-11'}},
        })
        epa = FakeEpa(range(0, 32), TWO_NODES,
                      foreign={'ceph-osd.0': list(range(0, 12))})
        self.reconcile(rp.PROFILE_PERFORMANCE, [osd(0, node=0)], epa)
        self.assertNotIn(('release', 'ceph-osd.0'), epa.calls)
        self.assertEqual(epa.claims['ceph-osd.0'], list(range(0, 12)))

    def test_epa_only_teardown_survives_reconcile_and_unavailable_retry(self):
        epa = FakeEpa(range(0, 16), TWO_NODES,
                      foreign={'ceph-osd.0': [0, 1]})
        original_release = epa.release
        epa.release = lambda service: (_ for _ in ()).throw(
            epa_client.EpaUnavailable('down'))

        with self.assertRaises(resource_manager.ResourceError):
            resource_manager.release_osd('0', client_factory=lambda: epa)
        failed = self.kv.get(resource_manager.STATE_KEY)
        self.assertEqual(failed['pending-teardowns']['0']['status'],
                         'release-failed')
        self.assertNotIn('osds', failed)

        blocked = self.reconcile(rp.PROFILE_MINIMAL, [osd(0)], epa)
        self.assertIn('unfinished OSD resource teardown', blocked['errors'][0])
        self.assertEqual(blocked['pending-teardowns'],
                         failed['pending-teardowns'])
        self.apply_allocations.assert_not_called()

        class Unavailable(object):
            def release(self, service):
                raise epa_client.EpaUnavailable('still down')

        self.assertFalse(resource_manager.safe_release_osd(
            '0', client_factory=Unavailable))
        self.assertEqual(
            self.kv.get(resource_manager.STATE_KEY)
            ['pending-teardowns']['0']['status'], 'release-failed')

        epa.release = original_release
        resource_manager.release_osd('0', client_factory=lambda: epa)
        self.assertNotIn('ceph-osd.0', epa.claims)
        self.assertNotIn('pending-teardowns',
                         self.kv.get(resource_manager.STATE_KEY))

    def test_pending_teardown_blocks_managed_and_unmanaged_restarts(self):
        self.kv.set(resource_manager.STATE_KEY, {
            'version': 1, 'profile': rp.PROFILE_MINIMAL,
            'osds': {'0': {'osd-id': '0', 'mode': rp.MODE_NUMA,
                           'numa-node': 0, 'count': 8, 'cores': '0-7'}},
            'pending-teardowns': {'0': {'status': 'release-failed',
                                        'error': 'EPA is down'}},
        })
        snapshot = self.kv.get(resource_manager.STATE_KEY)
        epa = FakeEpa(range(0, 16), TWO_NODES,
                      foreign={'ceph-osd.0': list(range(8))})

        for profile in (rp.PROFILE_PERFORMANCE, rp.PROFILE_UNMANAGED):
            state = self.reconcile(profile, [osd(0)], epa)
            self.assertIn('unfinished OSD resource teardown',
                          state['errors'][0])
            self.assertEqual(state['osds'], snapshot['osds'])
            self.assertEqual(state['pending-teardowns'],
                             snapshot['pending-teardowns'])

        self.apply_allocations.assert_not_called()
        self.stop_osd.assert_not_called()

    def test_release_osd_stops_clears_and_releases_in_order(self):
        self.kv.set(resource_manager.STATE_KEY, {
            'version': 1, 'profile': rp.PROFILE_MINIMAL,
            'osds': {'0': {'osd-id': '0'}, '1': {'osd-id': '1'}},
        })
        epa = FakeEpa(range(0, 16), TWO_NODES,
                      foreign={'ceph-osd.0': [0, 1], 'ceph-osd.1': [2, 3]})
        events = []
        self.stop_osd.side_effect = lambda osd_id: events.append(
            ('stop', osd_id))
        self.clear_allocation.side_effect = lambda osd_id, **kwargs: (
            events.append(('clear', osd_id)), self.cleared.append(osd_id))[1]
        original_release = epa.release
        epa.release = lambda service: (events.append(('release', service)),
                                       original_release(service))[1]

        resource_manager.release_osd('osd.0', client_factory=lambda: epa)

        self.assertEqual(events, [
            ('stop', '0'), ('clear', '0'), ('release', 'ceph-osd.0')])
        self.assertEqual(sorted(epa.claims), ['ceph-osd.1'])
        self.assertEqual(self.cleared, ['0'])
        self.assertEqual(
            sorted(self.kv.get(resource_manager.STATE_KEY)['osds']), ['1'])

    def test_release_osd_stop_failure_retains_claim_and_state(self):
        self.kv.set(resource_manager.STATE_KEY, {
            'version': 1, 'profile': rp.PROFILE_MINIMAL,
            'osds': {'0': {'osd-id': '0'}},
        })
        epa = FakeEpa(range(0, 16), TWO_NODES,
                      foreign={'ceph-osd.0': [0, 1]})
        self.stop_osd.side_effect = resource_manager.resource_apply.ApplyError(
            'still active')

        with self.assertRaises(resource_manager.ResourceError):
            resource_manager.release_osd('0', client_factory=lambda: epa)

        state = self.kv.get(resource_manager.STATE_KEY)
        self.assertEqual(epa.claims['ceph-osd.0'], [0, 1])
        self.assertEqual(state['osds']['0']['osd-id'], '0')
        self.assertEqual(state['pending-teardowns']['0']['status'],
                         'stop-failed')
        self.assertEqual(self.cleared, [])

    def test_release_osd_clear_failure_retains_epa_claim(self):
        self.kv.set(resource_manager.STATE_KEY, {
            'version': 1, 'profile': rp.PROFILE_MINIMAL,
            'osds': {'0': {'osd-id': '0'}},
        })
        epa = FakeEpa(range(0, 16), TWO_NODES,
                      foreign={'ceph-osd.0': [0, 1]})
        self.clear_allocation.side_effect = resource_manager.resource_apply.\
            ApplyError('cannot remove drop-in')

        with self.assertRaises(resource_manager.ResourceError):
            resource_manager.release_osd('0', client_factory=lambda: epa)

        state = self.kv.get(resource_manager.STATE_KEY)
        self.assertEqual(epa.claims['ceph-osd.0'], [0, 1])
        self.assertNotIn(('release', 'ceph-osd.0'), epa.calls)
        self.assertIn('0', state['osds'])
        self.assertEqual(state['pending-teardowns']['0']['status'],
                         'clear-failed')

    def test_real_clear_reload_failure_retries_before_epa_release(self):
        self.kv.set(resource_manager.STATE_KEY, {
            'version': 1, 'profile': rp.PROFILE_MINIMAL,
            'osds': {'0': {'osd-id': '0'}},
        })
        epa = FakeEpa(range(0, 16), TWO_NODES,
                      foreign={'ceph-osd.0': [0, 1]})
        with tempfile.TemporaryDirectory() as root, \
                patch.object(resource_manager.resource_apply, 'DROPIN_DIR',
                             root + '/ceph-osd@{osd_id}.service.d'), \
                patch.object(resource_manager.resource_apply, 'kv',
                             return_value=self.kv), \
                patch.object(resource_manager.resource_apply,
                             'clear_allocation',
                             new=self.real_clear_allocation), \
                patch.object(resource_manager.resource_apply,
                             'daemon_reload') as reload:
            resource_manager.resource_apply.write_dropin('0', 'CPUAffinity')
            reload.side_effect = subprocess.CalledProcessError(
                1, 'systemctl daemon-reload')
            with self.assertRaises(resource_manager.ResourceError):
                resource_manager.release_osd('0', client_factory=lambda: epa)
            self.assertEqual(epa.claims['ceph-osd.0'], [0, 1])
            self.assertEqual(
                resource_manager.resource_apply.pending_clears(), {'0'})
            self.assertEqual(
                self.kv.get(resource_manager.STATE_KEY)
                ['pending-teardowns']['0']['status'], 'clear-failed')

            reload.side_effect = None
            resource_manager.release_osd('0', client_factory=lambda: epa)
            self.assertEqual(reload.call_count, 2)
        self.assertNotIn('ceph-osd.0', epa.claims)
        self.assertFalse(self.kv.get(
            resource_manager.resource_apply.CLEAR_PENDING_KEY, []))

    def test_release_osd_release_failure_keeps_truthful_retry_state(self):
        self.kv.set(resource_manager.STATE_KEY, {
            'version': 1, 'profile': rp.PROFILE_MINIMAL,
            'osds': {'0': {'osd-id': '0'}},
        })
        epa = FakeEpa(range(0, 16), TWO_NODES,
                      foreign={'ceph-osd.0': [0, 1]})
        events = []
        self.stop_osd.side_effect = lambda osd_id: events.append(
            ('stop', osd_id))
        self.clear_allocation.side_effect = lambda osd_id, **kwargs: (
            events.append(('clear', osd_id)), self.cleared.append(osd_id))[1]
        original_release = epa.release
        epa.release = lambda service: (
            events.append(('release', service)),
            (_ for _ in ()).throw(epa_client.EpaUnavailable('down')))[1]

        with self.assertRaises(resource_manager.ResourceError):
            resource_manager.release_osd('0', client_factory=lambda: epa)

        state = self.kv.get(resource_manager.STATE_KEY)
        self.assertEqual(events, [
            ('stop', '0'), ('clear', '0'), ('release', 'ceph-osd.0')])
        self.assertEqual(state['osds']['0']['osd-id'], '0')
        self.assertEqual(state['pending-teardowns']['0']['status'],
                         'release-failed')
        self.assertEqual(epa.claims['ceph-osd.0'], [0, 1])

        epa.release = original_release
        resource_manager.release_osd('0', client_factory=lambda: epa)
        self.assertNotIn('ceph-osd.0', epa.claims)
        self.assertNotIn('pending-teardowns',
                         self.kv.get(resource_manager.STATE_KEY))

    def test_release_all_completes_each_osd_after_partial_stop_failure(self):
        self.kv.set(resource_manager.STATE_KEY, {
            'version': 1, 'profile': rp.PROFILE_MINIMAL,
            'osds': {'0': {'osd-id': '0'}},
            'pending-transitions': {'1': {'new': True, 'status': 'allocated'}},
        })
        epa = FakeEpa(range(0, 16), TWO_NODES,
                      foreign={'ceph-osd.0': [0, 1], 'ceph-osd.1': [2, 3]})
        events = []

        def stop(osd_id):
            events.append(('stop', osd_id))
            if osd_id == '0':
                raise resource_manager.resource_apply.ApplyError('running')

        self.stop_osd.side_effect = stop
        self.clear_allocation.side_effect = lambda osd_id, **kwargs: (
            events.append(('clear', osd_id)), self.cleared.append(osd_id))[1]
        original_release = epa.release
        epa.release = lambda service: (events.append(('release', service)),
                                       original_release(service))[1]

        with self.assertRaises(resource_manager.ResourceError):
            resource_manager.release_all(client_factory=lambda: epa)

        state = self.kv.get(resource_manager.STATE_KEY)
        self.assertEqual(events, [
            ('stop', '0'), ('stop', '1'), ('clear', '1'),
            ('release', 'ceph-osd.1')])
        self.assertEqual(epa.claims['ceph-osd.0'], [0, 1])
        self.assertNotIn('ceph-osd.1', epa.claims)
        self.assertIn('0', state['osds'])
        self.assertNotIn('1', state.get('pending-transitions', {}))
        self.assertEqual(state['pending-teardowns']['0']['status'],
                         'stop-failed')

    def test_release_all_falls_back_to_allocator_claims_missing_state(self):
        epa = FakeEpa(range(0, 16), TWO_NODES,
                      foreign={'ceph-osd.4': [0, 1], 'nova-compute': [8]})
        resource_manager.release_all(client_factory=lambda: epa)
        self.assertEqual(sorted(epa.claims), ['nova-compute'])
        self.stop_osd.assert_called_once_with('4')
        self.assertEqual(self.cleared, ['4'])

    def test_unmanaged_osd_with_unavailable_epa_is_not_stopped_or_cleared(
            self):
        class Broken(object):
            def list_allocations(self):
                raise epa_client.EpaUnavailable('down')

        self.assertIsNone(
            resource_manager.release_osd('3', client_factory=Broken))
        self.stop_osd.assert_not_called()
        self.assertEqual(self.cleared, [])


class PoolManagementTestCase(ManagerTestCase):

    def test_pool_is_untouched_for_a_pre_existing_snap(self):
        epa = FakeEpa(range(0, 16), TWO_NODES)
        self.reconcile(rp.PROFILE_MINIMAL, [osd(0)], epa)
        self.assertEqual(self.pools_set, [])
        self.assertFalse(
            self.kv.get(resource_manager.MANAGED_KEY, False))

    def test_charm_installed_snap_gets_a_pool(self):
        self.ensure_installed.return_value = 'store'
        epa = FakeEpa(range(0, 32), TWO_NODES)
        state = self.reconcile(rp.PROFILE_MINIMAL, [osd(0)], epa)
        # 20% of each node's 16 CPUs, taken from the top of each node.
        self.assertEqual(self.pools_set, ['12-15,28-31'])
        self.assertTrue(state['epa']['charm-managed'])

    def test_pool_grows_with_profile_demand(self):
        self.ensure_installed.return_value = 'resource'
        self.pool = '30-31'
        epa = FakeEpa(range(0, 32), TWO_NODES)
        self.reconcile(rp.PROFILE_PERFORMANCE, [osd(0)], epa)
        # Node 0 needs 12 CPUs for the OSD, node 0's floor is 4, and the
        # pre-existing pool is retained.
        self.assertEqual(len(self.pools_set), 1)
        pool = self.pools_set[0]
        # The pre-existing CPUs 30-31 are retained, node 0 grows to 12
        # CPUs for the OSD, and node 1 keeps its 20% floor.
        self.assertEqual(pool, '4-15,28-31')

    def test_pool_accounts_for_cpus_held_by_other_workloads(self):
        self.ensure_installed.return_value = 'store'
        epa = FakeEpa(range(0, 32), TWO_NODES,
                      foreign={'nova-compute': [14, 15]})
        self.reconcile(rp.PROFILE_BALANCED, [osd(0)], epa)
        # Node 0 needs 8 CPUs for the OSD plus the 2 held by the
        # co-located workload, so the pool covers 10 CPUs there.
        self.assertEqual(self.pools_set[0], '6-15,28-31')
        self.assertEqual(len(epa.claims['ceph-osd.0']), 8)
        self.assertEqual(epa.claims['nova-compute'], [14, 15])
        self.assertNotIn(14, epa.claims['ceph-osd.0'])

    def test_pool_cap_wins_over_foreign_demand(self):
        # 12 CPUs for the OSD plus 2 held elsewhere exceeds the 75% cap
        # of a 16 CPU node, so the profile is refused rather than the
        # host being handed over entirely.
        self.ensure_installed.return_value = 'store'
        epa = FakeEpa(range(0, 32), TWO_NODES,
                      foreign={'nova-compute': [14, 15]})
        state = self.reconcile(rp.PROFILE_PERFORMANCE, [osd(0)], epa)
        self.assertEqual(self.pools_set[0], '4-15,28-31')
        self.assertIn('needs more CPUs', state['errors'][0])
        self.assertNotIn('ceph-osd.0', epa.claims)

    def test_pool_is_not_reconfigured_when_sufficient(self):
        self.ensure_installed.return_value = 'store'
        self.pool = '1-15,17-31'
        epa = FakeEpa(range(0, 32), TWO_NODES)
        self.reconcile(rp.PROFILE_MINIMAL, [osd(0)], epa)
        self.assertEqual(self.pools_set, [])

    def test_pool_configuration_failure_blocks(self):
        self.ensure_installed.return_value = 'store'
        self.set_pool.side_effect = resource_manager.epa_snap.SnapError(
            'pool rejected')
        epa = FakeEpa(range(0, 32), TWO_NODES)
        state = self.reconcile(rp.PROFILE_MINIMAL, [osd(0)], epa)
        self.assertIn('pool rejected', state['errors'][0])
        self.assertEqual(epa.claims, {})

    def test_resource_path_is_stripped(self):
        self.resource_path = '/var/lib/juju/resources/epa/epa.snap\n'
        epa = FakeEpa(range(0, 32), TWO_NODES)
        self.reconcile(rp.PROFILE_MINIMAL, [osd(0)], epa)
        self.ensure_installed.assert_called_once_with(
            '/var/lib/juju/resources/epa/epa.snap', 'latest/stable')

    def test_install_failure_blocks(self):
        self.ensure_installed.side_effect = (
            resource_manager.epa_snap.SnapError('no snapd'))
        epa = FakeEpa(range(0, 32), TWO_NODES)
        state = self.reconcile(rp.PROFILE_MINIMAL, [osd(0)], epa)
        self.assertIn('no snapd', state['errors'][0])
        self.assertEqual(self.applied, [])


class VerifyTestCase(ManagerTestCase):

    def applied_state(self):
        return {
            'version': 1,
            'profile': rp.PROFILE_BALANCED,
            'osds': {'0': {'osd-id': '0', 'cores': '4-11',
                           'mode': rp.MODE_NUMA, 'numa-node': 0,
                           'numa-policy': 'preferred'}},
        }

    def test_no_drift(self):
        self.kv.set(resource_manager.STATE_KEY, self.applied_state())
        epa = FakeEpa(range(0, 16), TWO_NODES,
                      foreign={'ceph-osd.0': list(range(4, 12))})
        expected = resource_manager.resource_apply.render_dropin(
            rp.PROFILE_BALANCED, '4-11', numa_policy='preferred',
            numa_node=0)
        with patch.object(resource_manager.resource_apply, 'read_dropin',
                          return_value=expected):
            self.assertEqual(
                resource_manager.verify(client_factory=lambda: epa), [])

    def test_missing_dropin_is_drift(self):
        self.kv.set(resource_manager.STATE_KEY, self.applied_state())
        epa = FakeEpa(range(0, 16), TWO_NODES,
                      foreign={'ceph-osd.0': list(range(4, 12))})
        with patch.object(resource_manager.resource_apply, 'read_dropin',
                          return_value=None):
            drift = resource_manager.verify(client_factory=lambda: epa)
        self.assertEqual(len(drift), 1)
        self.assertIn('drop-in differs', drift[0])
        self.assertEqual(
            self.kv.get(resource_manager.STATE_KEY)['drift'], drift)

    def test_lost_claim_is_drift(self):
        self.kv.set(resource_manager.STATE_KEY, self.applied_state())
        epa = FakeEpa(range(0, 16), TWO_NODES)
        expected = resource_manager.resource_apply.render_dropin(
            rp.PROFILE_BALANCED, '4-11', numa_policy='preferred',
            numa_node=0)
        with patch.object(resource_manager.resource_apply, 'read_dropin',
                          return_value=expected):
            drift = resource_manager.verify(client_factory=lambda: epa)
        self.assertIn('no longer records a CPU claim', drift[0])

    def test_conflicting_ceph_numa_settings_are_drift(self):
        self.kv.set(resource_manager.STATE_KEY, self.applied_state())
        epa = FakeEpa(range(0, 16), TWO_NODES,
                      foreign={'ceph-osd.0': list(range(4, 12))})
        expected = resource_manager.resource_apply.render_dropin(
            rp.PROFILE_BALANCED, '4-11', numa_policy='preferred',
            numa_node=0)
        self.verify_enforcement.side_effect = resource_manager.resource_apply.\
            ApplyError('osd.0 has conflicting Ceph NUMA affinity settings')
        with patch.object(resource_manager.resource_apply, 'read_dropin',
                          return_value=expected):
            drift = resource_manager.verify(client_factory=lambda: epa)
        self.assertIn('conflicting Ceph NUMA affinity settings', drift[0])

    def test_changed_claim_is_drift(self):
        self.kv.set(resource_manager.STATE_KEY, self.applied_state())
        epa = FakeEpa(range(0, 16), TWO_NODES,
                      foreign={'ceph-osd.0': [1, 2]})
        expected = resource_manager.resource_apply.render_dropin(
            rp.PROFILE_BALANCED, '4-11', numa_policy='preferred',
            numa_node=0)
        with patch.object(resource_manager.resource_apply, 'read_dropin',
                          return_value=expected):
            drift = resource_manager.verify(client_factory=lambda: epa)
        self.assertIn('allocator holds 1-2 but 4-11 was applied', drift[0])

    def test_unreachable_allocator_is_drift(self):
        self.kv.set(resource_manager.STATE_KEY, self.applied_state())

        class Unreachable(object):
            def list_allocations(self):
                raise epa_client.EpaUnavailable('socket missing')

        with patch.object(resource_manager.resource_apply, 'read_dropin',
                          return_value=None):
            drift = resource_manager.verify(client_factory=Unreachable)
        self.assertIn('cannot query the EPA orchestrator', drift[0])

    def test_retained_unmanaged_allocation_is_still_checked(self):
        state = self.applied_state()
        state['profile'] = rp.PROFILE_UNMANAGED
        self.kv.set(resource_manager.STATE_KEY, state)
        self.assertTrue(any(
            'drop-in differs' in drift for drift in resource_manager.verify()))


class AssessTestCase(ManagerTestCase):

    def test_nothing_to_report_when_unmanaged(self):
        self.assertIsNone(resource_manager.assess())

    def test_errors_block(self):
        self.kv.set(resource_manager.STATE_KEY, {
            'profile': rp.PROFILE_PERFORMANCE,
            'errors': ['osd.7: not aligned', 'second'],
        })
        status = resource_manager.assess()
        self.assertTrue(status.blocked)
        self.assertEqual(
            status.message,
            'performance-profile performance: osd.7: not aligned')

    def test_waiting_rollout_is_reported_as_waiting(self):
        self.kv.set(resource_manager.STATE_KEY, {
            'profile': rp.PROFILE_BALANCED,
            'rollout': {'status': 'waiting', 'message': 'waiting for host0'},
        })
        status = resource_manager.assess()
        self.assertTrue(status.waiting)
        self.assertFalse(status.blocked)
        self.assertEqual(status.message, 'waiting for host0')

    def test_waiting_does_not_hide_existing_drift(self):
        self.kv.set(resource_manager.STATE_KEY, {
            'profile': rp.PROFILE_BALANCED,
            'drift': ['allocator claim missing'],
            'rollout': {'status': 'waiting', 'message': 'waiting for host0'},
        })
        self.assertTrue(resource_manager.assess().blocked)

    def test_failed_rollout_blocks(self):
        self.kv.set(resource_manager.STATE_KEY, {
            'rollout': {'status': 'failed', 'message': 'host0 failed'},
        })
        self.assertTrue(resource_manager.assess().blocked)

    def test_drift_blocks(self):
        self.kv.set(resource_manager.STATE_KEY, {
            'profile': rp.PROFILE_BALANCED,
            'drift': ['osd.0: CPU affinity drop-in differs'],
        })
        self.assertTrue(resource_manager.assess().blocked)

    def test_deviations_warn(self):
        self.kv.set(resource_manager.STATE_KEY, {
            'profile': rp.PROFILE_BALANCED,
            'warnings': ['osd.0: degraded', 'osd.1: degraded'],
            'osds': {'0': {}},
        })
        status = resource_manager.assess()
        self.assertFalse(status.blocked)
        self.assertEqual(
            status.message,
            'profile balanced applied with 2 deviation(s), see logs')

    def test_deviations_can_be_suppressed(self):
        self.config['suppress-profile-warnings'] = True
        self.kv.set(resource_manager.STATE_KEY, {
            'profile': rp.PROFILE_BALANCED,
            'warnings': ['osd.0: degraded'],
            'osds': {'0': {}},
        })
        self.assertEqual(resource_manager.assess().message,
                         'profile balanced applied')

    def test_applied_profile_is_reported(self):
        self.kv.set(resource_manager.STATE_KEY, {
            'profile': rp.PROFILE_MINIMAL, 'osds': {'0': {}},
        })
        self.assertEqual(resource_manager.assess().message,
                         'profile minimal applied')

    def test_managed_profile_without_osds_is_quiet(self):
        self.kv.set(resource_manager.STATE_KEY, {
            'profile': rp.PROFILE_MINIMAL, 'osds': {},
        })
        self.assertIsNone(resource_manager.assess())


class StatusReportTestCase(ManagerTestCase):

    def report(self, epa, osds, dry_run=None, nodes=None, nic_nodes=(0,)):
        host = rp.HostFacts(nodes=nodes or TWO_NODES, nic_interface='eth0',
                            nic_nodes=set(nic_nodes))
        with patch.object(resource_manager, 'collect_osd_facts',
                          return_value=osds), \
                patch.object(resource_manager, 'collect_host_facts',
                             return_value=host):
            return resource_manager.status_report(
                dry_run=dry_run, client_factory=lambda: epa)

    def test_reports_applied_state(self):
        self.kv.set(resource_manager.STATE_KEY, {
            'profile': rp.PROFILE_BALANCED,
            'epa': {'charm-managed': True},
            'osds': {'0': {'osd-id': '0', 'cores': '4-11',
                           'numa-policy': 'preferred'}},
        })
        epa = FakeEpa(range(0, 16), TWO_NODES,
                      foreign={'ceph-osd.0': list(range(4, 12))})
        report = self.report(epa, [osd(0)])
        self.assertEqual(report['profile'], rp.PROFILE_BALANCED)
        self.assertEqual(report['epa']['source'], 'configured')
        self.assertEqual(report['epa']['eligible-cpus'], '0-15')
        self.assertEqual(report['numa-nodes'], {'0': '0-15', '1': '16-31'})
        self.assertEqual(report['nic'],
                         {'interface': 'eth0', 'numa-nodes': [0]})
        entry = report['osds'][0]
        self.assertEqual(entry['osd-id'], '0')
        self.assertEqual(entry['tier'], rp.TIER_NVME)
        self.assertEqual(entry['applied-cores'], '4-11')
        self.assertEqual(entry['allocator-cores'], '4-11')
        self.assertEqual(entry['allocator-policy'], 'non-preemptive')
        self.assertEqual(entry['desired-cores'], 8)

    def test_reports_requested_profile_separately_from_applied_state(self):
        self.kv.set(resource_manager.STATE_KEY, {
            'profile': rp.PROFILE_BALANCED,
            'requested-profile': rp.PROFILE_PERFORMANCE,
            'osds': {'0': {'osd-id': '0', 'cores': '4-11',
                           'numa-policy': 'preferred'}},
        })
        epa = FakeEpa(range(0, 16), TWO_NODES,
                      foreign={'ceph-osd.0': list(range(4, 12))})
        report = self.report(epa, [osd(0)])
        self.assertEqual(report['profile'], rp.PROFILE_BALANCED)
        self.assertEqual(report['requested-profile'], rp.PROFILE_PERFORMANCE)
        self.assertEqual(report['osds'][0]['applied-cores'], '4-11')
        self.assertEqual(report['osds'][0]['desired-cores'], 12)

    def test_dry_run_previews_without_changing_anything(self):
        epa = FakeEpa(range(0, 16), TWO_NODES)
        report = self.report(epa, [osd(0), osd(1)],
                             dry_run=rp.PROFILE_PERFORMANCE)
        self.assertEqual(report['dry-run'], rp.PROFILE_PERFORMANCE)
        self.assertEqual(report['profile'], rp.PROFILE_UNMANAGED)
        self.assertEqual([entry['desired-cores'] for entry in report['osds']],
                         [12, 12])
        self.assertEqual(len(report['shortfalls']), 1)
        self.assertEqual(epa.claims, {})
        self.assertEqual(self.applied, [])

    def test_dry_run_reports_strict_errors(self):
        epa = FakeEpa(range(0, 32), TWO_NODES)
        report = self.report(epa, [osd(0, node=1)],
                             dry_run=rp.PROFILE_PERFORMANCE)
        self.assertEqual(len(report['errors']), 1)
        self.assertIn('osd.0', report['errors'][0])

    def test_report_without_allocator(self):
        class Unreachable(object):
            def list_allocations(self):
                raise epa_client.EpaUnavailable('socket missing')

        host = rp.HostFacts(nodes=TWO_NODES, nic_interface='eth0',
                            nic_nodes=set([0]))
        with patch.object(resource_manager, 'collect_osd_facts',
                          return_value=[osd(0)]), \
                patch.object(resource_manager, 'collect_host_facts',
                             return_value=host):
            report = resource_manager.status_report(
                client_factory=Unreachable)
        self.assertIn('socket missing', report['epa']['error'])
        self.assertEqual(report['osds'][0]['allocator-cores'], '')

    def test_report_when_osd_inventory_is_unavailable(self):
        epa = FakeEpa(range(0, 16), TWO_NODES)
        with patch.object(resource_manager, 'collect_osd_facts') as collect:
            collect.side_effect = resource_manager.TopologyError('no ceph')
            report = resource_manager.status_report(
                client_factory=lambda: epa)
        self.assertIn('no ceph', report['errors'][0])


class SafeWrapperTestCase(ManagerTestCase):

    def test_safe_reconcile_records_unexpected_failures(self):
        with patch.object(resource_manager, 'reconcile') as reconcile:
            reconcile.side_effect = RuntimeError('boom')
            state = resource_manager.safe_reconcile()
        self.assertIn('boom', state['errors'][0])
        self.assertTrue(resource_manager.assess().blocked)

    def test_noop_handoff_requires_active_osds(self):
        self.wait_for_osd.return_value = False
        with patch.object(resource_manager, 'collect_osd_facts',
                          return_value=[osd(0)]):
            self.assertRaises(resource_manager.ResourceError,
                              resource_manager._ready_for_handoff)

    def test_handoff_requires_no_pending_restarts(self):
        self.pending_restarts.return_value = {'0'}
        self.assertRaises(resource_manager.ResourceError,
                          resource_manager._ready_for_handoff)

    def test_waiting_does_not_claim_or_apply(self):
        self.rollout_run.side_effect = resource_manager.resource_rollout.\
            RollingWait('waiting for host0')
        with patch.object(resource_manager, 'reconcile') as reconcile:
            state = resource_manager.safe_reconcile()
        reconcile.assert_not_called()
        self.assertEqual(state['rollout']['status'], 'waiting')
        self.assertTrue(resource_manager.assess().waiting)

    def test_rollout_failure_blocks_without_mutation(self):
        self.rollout_run.side_effect = resource_manager.resource_rollout.\
            RollingError('host0 failed')
        with patch.object(resource_manager, 'reconcile') as reconcile:
            resource_manager.safe_reconcile()
        reconcile.assert_not_called()
        self.assertTrue(resource_manager.assess().blocked)

    def test_verify_observes_timeout_without_reconciling(self):
        self.rollout_observe.side_effect = resource_manager.resource_rollout.\
            RollingError('rollout timed out')
        resource_manager.safe_verify()
        self.rollout_run.assert_not_called()
        self.assertTrue(resource_manager.assess().blocked)

    def test_safe_verify_swallows_failures(self):
        with patch.object(resource_manager, 'verify') as verify:
            verify.side_effect = RuntimeError('boom')
            self.assertEqual(resource_manager.safe_verify(), [])

    def test_safe_release_all_reports_failures(self):
        with patch.object(resource_manager, 'release_all') as release:
            release.side_effect = RuntimeError('boom')
            self.assertFalse(resource_manager.safe_release_all())

    def test_safe_release_osd_reports_failures(self):
        with patch.object(resource_manager, 'release_osd') as release:
            release.side_effect = RuntimeError('boom')
            self.assertFalse(resource_manager.safe_release_osd('0'))


class CollectorTestCase(ManagerTestCase):

    def test_collect_osd_facts(self):
        with patch.object(resource_manager, 'osd_device_map') as devices, \
                patch.object(resource_manager,
                             'device_is_rotational') as rot, \
                patch.object(resource_manager, 'device_numa_node') as node:
            devices.return_value = {
                '0': {'data': '/dev/nvme0n1', 'aux': ['/dev/nvme1n1']},
                '1': {'data': '/dev/sdb', 'aux': []},
            }
            rot.side_effect = lambda device, root='/': device.startswith(
                '/dev/sd')
            node.side_effect = lambda device, root='/': (
                1 if device == '/dev/nvme1n1' else 0)
            facts = resource_manager.collect_osd_facts()
        self.assertEqual([f.osd_id for f in facts], ['0', '1'])
        self.assertEqual(facts[0].tier, rp.TIER_NVME)
        self.assertEqual(facts[0].aux_nodes, {'/dev/nvme1n1': 1})
        self.assertEqual(facts[1].tier, rp.TIER_HDD)
        self.assertEqual(facts[1].data_node, 0)

    def test_collect_host_facts(self):
        with patch.object(resource_manager, 'numa_nodes') as nodes, \
                patch.object(resource_manager, 'ceph_network_address',
                             return_value='10.0.0.5'), \
                patch.object(resource_manager,
                             'interface_for_address') as lookup, \
                patch.object(resource_manager,
                             'interface_numa_nodes') as nic_nodes:
            nodes.return_value = TWO_NODES
            lookup.return_value = 'bond0'
            nic_nodes.return_value = set([1])
            facts = resource_manager.collect_host_facts()
        self.assertEqual(facts.nodes, TWO_NODES)
        self.assertEqual(facts.nic_interface, 'bond0')
        self.assertEqual(facts.nic_nodes, set([1]))

    def test_collect_host_facts_without_an_interface(self):
        with patch.object(resource_manager, 'numa_nodes') as nodes, \
                patch.object(resource_manager, 'ceph_network_address',
                             return_value='10.0.0.5'), \
                patch.object(resource_manager,
                             'interface_for_address') as lookup:
            nodes.return_value = {0: [0, 1]}
            lookup.return_value = None
            facts = resource_manager.collect_host_facts()
        self.assertEqual(facts.nic_nodes, set())
        self.assertIsNone(facts.nic_interface)

    def test_collect_host_facts_without_an_address(self):
        with patch.object(resource_manager, 'numa_nodes') as nodes, \
                patch.object(resource_manager, 'ceph_network_address') as get:
            nodes.return_value = {0: [0, 1]}
            get.side_effect = RuntimeError('no network binding')
            facts = resource_manager.collect_host_facts()
        self.assertEqual(facts.nic_nodes, set())
        self.assertIsNone(facts.nic_interface)
