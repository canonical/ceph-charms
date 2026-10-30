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

import unittest

import resource_profiles as rp


def osd(osd_id, tier=rp.TIER_NVME, data_node=0, aux_nodes=None,
        data_device=None):
    """Build OSD facts for the planner."""
    aux_nodes = aux_nodes or {}
    return rp.OsdFacts(
        osd_id=str(osd_id),
        data_device=data_device or '/dev/nvme{}n1'.format(osd_id),
        aux_devices=sorted(aux_nodes),
        tier=tier,
        data_node=data_node,
        aux_nodes=aux_nodes,
    )


def host(nodes=None, nic_nodes=(0,), interface='eth0'):
    """Build host facts for the planner."""
    if nodes is None:
        nodes = {0: list(range(0, 8)), 1: list(range(8, 16))}
    return rp.HostFacts(nodes=nodes, nic_interface=interface,
                        nic_nodes=set(nic_nodes))


def candidate(address, interface, *nodes, note=None):
    return rp.NetworkCandidate(address=address, interface=interface,
                               nodes=tuple(nodes), note=note)


NO_PRIMARY = 'bond0 is an active-backup bond without a primary leg'


def network(role, host_address, *candidates, source='ceph-public-network'):
    return rp.NetworkFacts(role=role, source=source,
                           host_address=host_address,
                           candidates=tuple(candidates))


# One NIC per socket on each network; the host addresses are on node 0.
PUBLIC = network(rp.NETWORK_PUBLIC, '10.0.0.5',
                 candidate('10.0.0.5', 'ens1f0', 0),
                 candidate('10.0.1.5', 'ens2f0', 1))
CLUSTER = network(rp.NETWORK_CLUSTER, '10.1.0.5',
                  candidate('10.1.0.5', 'ens1f1', 0),
                  candidate('10.1.1.5', 'ens2f1', 1),
                  source='ceph-cluster-network')


def networked_host(public=PUBLIC, cluster=CLUSTER, nodes=None):
    """Build host facts whose NIC fields derive from the cluster network."""
    if nodes is None:
        nodes = {0: list(range(0, 8)), 1: list(range(8, 16))}
    return rp.HostFacts(
        nodes=nodes,
        nic_interface=','.join(sorted(
            set(c.interface for c in cluster.candidates))),
        nic_nodes=set(n for c in cluster.candidates for n in c.nodes),
        networks={rp.NETWORK_PUBLIC: public, rp.NETWORK_CLUSTER: cluster})


SINGLE_NODE = {0: list(range(0, 16))}


class ProfileDefinitionTestCase(unittest.TestCase):

    def test_core_counts(self):
        self.assertEqual(
            rp.cores_for(rp.PROFILE_PERFORMANCE, rp.TIER_NVME), 16)
        self.assertEqual(
            rp.cores_for(rp.PROFILE_PERFORMANCE, rp.TIER_HDD), 4)
        self.assertEqual(rp.cores_for(rp.PROFILE_BALANCED, rp.TIER_NVME), 8)
        self.assertEqual(rp.cores_for(rp.PROFILE_BALANCED, rp.TIER_HDD), 2)
        self.assertEqual(rp.cores_for(rp.PROFILE_MINIMAL, rp.TIER_NVME), 2)
        self.assertEqual(rp.cores_for(rp.PROFILE_MINIMAL, rp.TIER_HDD), 2)

    def test_strictness(self):
        self.assertTrue(rp.is_strict(rp.PROFILE_PERFORMANCE))
        self.assertFalse(rp.is_strict(rp.PROFILE_BALANCED))
        self.assertFalse(rp.is_strict(rp.PROFILE_MINIMAL))

    def test_managed_profiles(self):
        self.assertFalse(rp.is_managed(rp.PROFILE_UNMANAGED))
        for profile in (rp.PROFILE_PERFORMANCE, rp.PROFILE_BALANCED,
                        rp.PROFILE_MINIMAL):
            self.assertTrue(rp.is_managed(profile))

    def test_tier_from_rotational_flag(self):
        self.assertEqual(rp.tier_for_rotational(True), rp.TIER_HDD)
        self.assertEqual(rp.tier_for_rotational(False), rp.TIER_NVME)
        # An unknown flag is treated as the frugal choice.
        self.assertEqual(rp.tier_for_rotational(None), rp.TIER_HDD)


class UnmanagedPlanTestCase(unittest.TestCase):

    def test_unmanaged_plans_nothing(self):
        plan = rp.build_plan(rp.PROFILE_UNMANAGED, [osd(0)], host())
        self.assertEqual(plan.requests, [])
        self.assertTrue(plan.satisfiable)


class PerformancePlanTestCase(unittest.TestCase):

    def test_aligned_nvme_and_hdd(self):
        osds = [
            osd(0, tier=rp.TIER_NVME, data_node=0),
            osd(1, tier=rp.TIER_HDD, data_node=0, data_device='/dev/sdb'),
        ]
        plan = rp.build_plan(rp.PROFILE_PERFORMANCE, osds, host())
        self.assertTrue(plan.satisfiable)
        self.assertEqual([(r.osd_id, r.cores, r.mode, r.numa_node,
                           r.numa_policy) for r in plan.requests],
                         [('0', 16, rp.MODE_NUMA, 0, 'bind'),
                          ('1', 4, rp.MODE_NUMA, 0, 'bind')])
        self.assertEqual(plan.demand_by_node(), {0: 20})
        self.assertEqual(plan.demand_unbound(), 0)

    def test_nic_on_other_node_is_refused(self):
        plan = rp.build_plan(rp.PROFILE_PERFORMANCE,
                             [osd(7, data_node=1)], host(nic_nodes=(0,)))
        self.assertFalse(plan.satisfiable)
        self.assertEqual(len(plan.errors), 1)
        self.assertIn('osd.7', plan.errors[0])
        self.assertIn('node 1', plan.errors[0])
        self.assertIn('eth0', plan.errors[0])
        self.assertEqual(plan.requests, [])

    def test_unknown_nic_node_is_a_deviation_not_an_error(self):
        plan = rp.build_plan(rp.PROFILE_PERFORMANCE,
                             [osd(0, data_node=0)], host(nic_nodes=()))
        self.assertTrue(plan.satisfiable)
        self.assertEqual(len(plan.requests), 1)
        self.assertEqual(len(plan.requests[0].deviations), 1)
        self.assertIn('not verified', plan.requests[0].deviations[0])

    def test_unknown_device_node_is_refused(self):
        plan = rp.build_plan(rp.PROFILE_PERFORMANCE,
                             [osd(0, data_node=None)], host())
        self.assertFalse(plan.satisfiable)
        self.assertIn('unknown', plan.errors[0])

    def test_db_device_on_other_node_is_refused(self):
        osds = [osd(0, data_node=0, aux_nodes={'/dev/nvme9n1': 1})]
        plan = rp.build_plan(rp.PROFILE_PERFORMANCE, osds, host())
        self.assertFalse(plan.satisfiable)
        self.assertIn('/dev/nvme9n1', plan.errors[0])

    def test_db_device_with_unknown_node_is_refused(self):
        osds = [osd(0, data_node=0, aux_nodes={'/dev/nvme9n1': None})]
        plan = rp.build_plan(rp.PROFILE_PERFORMANCE, osds, host())
        self.assertFalse(plan.satisfiable)
        self.assertIn('unknown node', plan.errors[0])

    def test_single_node_host_satisfies_alignment_trivially(self):
        osds = [osd(0, data_node=None, aux_nodes={'/dev/sdz': None})]
        plan = rp.build_plan(rp.PROFILE_PERFORMANCE, osds,
                             host(nodes=SINGLE_NODE, nic_nodes=()))
        self.assertTrue(plan.satisfiable)
        self.assertEqual(plan.requests[0].numa_node, 0)
        self.assertEqual(plan.requests[0].deviations, [])

    def test_partial_failure_refuses_only_offending_osd(self):
        osds = [osd(0, data_node=0), osd(1, data_node=1)]
        plan = rp.build_plan(rp.PROFILE_PERFORMANCE, osds,
                             host(nic_nodes=(0,)))
        # The whole plan is unsatisfiable, so the charm applies nothing.
        self.assertFalse(plan.satisfiable)
        self.assertEqual([r.osd_id for r in plan.requests], ['0'])


class BalancedPlanTestCase(unittest.TestCase):

    def test_aligns_data_device_only(self):
        osds = [osd(0, data_node=1, aux_nodes={'/dev/nvme9n1': 0})]
        plan = rp.build_plan(rp.PROFILE_BALANCED, osds, host(nic_nodes=(0,)))
        self.assertTrue(plan.satisfiable)
        request = plan.requests[0]
        self.assertEqual((request.cores, request.mode, request.numa_node,
                          request.numa_policy),
                         (8, rp.MODE_NUMA, 1, 'preferred'))
        # The db device on another node is reported, not refused.
        self.assertEqual(len(request.deviations), 1)
        self.assertIn('/dev/nvme9n1', request.deviations[0])

    def test_hdd_counts(self):
        osds = [osd(3, tier=rp.TIER_HDD, data_node=0,
                    data_device='/dev/sdb')]
        plan = rp.build_plan(rp.PROFILE_BALANCED, osds, host())
        self.assertEqual(plan.requests[0].cores, 2)

    def test_unknown_device_node_falls_back_without_numa(self):
        plan = rp.build_plan(rp.PROFILE_BALANCED, [osd(0, data_node=None)],
                             host())
        self.assertTrue(plan.satisfiable)
        request = plan.requests[0]
        self.assertEqual(request.mode, rp.MODE_ANY)
        self.assertIsNone(request.numa_node)
        self.assertIsNone(request.numa_policy)
        self.assertIn('without NUMA locality', request.deviations[0])
        self.assertEqual(plan.demand_unbound(), 8)

    def test_device_reporting_unknown_node_id(self):
        plan = rp.build_plan(rp.PROFILE_BALANCED, [osd(0, data_node=5)],
                             host())
        self.assertTrue(plan.satisfiable)
        self.assertEqual(plan.requests[0].mode, rp.MODE_ANY)

    def test_deviations_are_prefixed_with_the_osd(self):
        plan = rp.build_plan(rp.PROFILE_BALANCED, [osd(4, data_node=None)],
                             host())
        self.assertTrue(plan.deviations()[0].startswith('osd.4: '))


class MinimalPlanTestCase(unittest.TestCase):

    def test_requests_no_numa_alignment(self):
        osds = [osd(0, data_node=0),
                osd(1, tier=rp.TIER_HDD, data_node=1)]
        plan = rp.build_plan(rp.PROFILE_MINIMAL, osds, host())
        self.assertTrue(plan.satisfiable)
        for request in plan.requests:
            self.assertEqual(request.cores, 2)
            self.assertEqual(request.mode, rp.MODE_ANY)
            self.assertIsNone(request.numa_node)
            self.assertIsNone(request.numa_policy)
            self.assertEqual(request.deviations, [])
        self.assertEqual(plan.demand_by_node(), {})
        self.assertEqual(plan.demand_unbound(), 4)


class OverrideTestCase(unittest.TestCase):

    def test_no_overrides(self):
        self.assertEqual(rp.resolve_overrides(None), {})
        self.assertEqual(rp.resolve_overrides({}), {})
        self.assertEqual(rp.resolve_overrides({'performance': {}}), {})

    def test_cpu_count_applies_to_every_tier(self):
        self.assertEqual(
            rp.resolve_overrides({'performance': {'cpus': 20}}),
            {'performance': {'cpus': {'nvme': 20, 'hdd': 20}}})

    def test_cpu_count_per_tier(self):
        overrides = rp.resolve_overrides(
            {'performance': {'cpus': {'nvme': 20}}})
        self.assertEqual(overrides,
                         {'performance': {'cpus': {'nvme': 20}}})
        self.assertEqual(
            rp.cores_for(rp.PROFILE_PERFORMANCE, rp.TIER_NVME, overrides), 20)
        # A tier left out keeps the profile default.
        self.assertEqual(
            rp.cores_for(rp.PROFILE_PERFORMANCE, rp.TIER_HDD, overrides), 4)

    def test_other_profiles_keep_their_defaults(self):
        overrides = rp.resolve_overrides({'performance': {'cpus': 20}})
        self.assertEqual(
            rp.cores_for(rp.PROFILE_BALANCED, rp.TIER_NVME, overrides), 8)

    def test_invalid_overrides_are_rejected(self):
        for overrides in (
                16,
                'performance',
                ['performance'],
                {'performance': 16},
                {'performance': None},
                {'balanced': {'cpus': 4}},
                {'unmanaged': {'cpus': 4}},
                {'performance': {'memory': 4}},
                {'performance': {'cpus': 0}},
                {'performance': {'cpus': -2}},
                {'performance': {'cpus': 2.5}},
                {'performance': {'cpus': '16'}},
                {'performance': {'cpus': True}},
                {'performance': {'cpus': None}},
                {'performance': {'cpus': {'ssd': 16}}},
                {'performance': {'cpus': {'nvme': 0}}}):
            self.assertRaises(ValueError, rp.resolve_overrides, overrides)

    def test_plan_uses_overridden_counts(self):
        overrides = rp.resolve_overrides(
            {'performance': {'cpus': {'nvme': 6, 'hdd': 2}}})
        osds = [
            osd(0, tier=rp.TIER_NVME, data_node=0),
            osd(1, tier=rp.TIER_HDD, data_node=0, data_device='/dev/sdb'),
        ]
        plan = rp.build_plan(rp.PROFILE_PERFORMANCE, osds, host(), overrides)
        self.assertTrue(plan.satisfiable)
        self.assertEqual([(r.osd_id, r.cores, r.mode, r.numa_policy)
                          for r in plan.requests],
                         [('0', 6, rp.MODE_NUMA, 'bind'),
                          ('1', 2, rp.MODE_NUMA, 'bind')])
        self.assertEqual(plan.demand_by_node(), {0: 8})


class CapacityTestCase(unittest.TestCase):

    def test_numa_demand_fits(self):
        plan = rp.build_plan(rp.PROFILE_PERFORMANCE, [osd(0, data_node=0)],
                             host())
        nodes = {0: list(range(0, 16)), 1: list(range(16, 32))}
        self.assertEqual(
            rp.check_capacity(plan, range(0, 16), nodes), [])

    def test_numa_shortfall_is_reported_per_node(self):
        osds = [osd(0, data_node=0), osd(1, data_node=0)]
        plan = rp.build_plan(rp.PROFILE_PERFORMANCE, osds, host())
        nodes = {0: list(range(0, 16)), 1: list(range(16, 32))}
        shortfalls = rp.check_capacity(plan, range(0, 16), nodes)
        self.assertEqual(len(shortfalls), 1)
        self.assertIn('NUMA node 0 needs 32 logical CPUs, 16 available',
                      shortfalls[0])

    def test_unbound_demand_uses_what_numa_left_over(self):
        plan = rp.build_plan(rp.PROFILE_MINIMAL,
                             [osd(0), osd(1), osd(2)], host())
        nodes = {0: list(range(0, 8))}
        self.assertEqual(
            rp.check_capacity(plan, [0, 1, 2, 3, 4, 5], nodes), [])
        shortfalls = rp.check_capacity(plan, [0, 1, 2], nodes)
        self.assertEqual(len(shortfalls), 1)
        self.assertIn('unaligned allocations need 6 logical CPUs, 3 '
                      'available', shortfalls[0])

    def test_mixed_demand(self):
        osds = [osd(0, data_node=0), osd(1, data_node=None)]
        plan = rp.build_plan(rp.PROFILE_BALANCED, osds, host())
        nodes = {0: list(range(0, 8)), 1: list(range(8, 16))}
        # Node 0 satisfies the aligned OSD, leaving node 1 for the other.
        self.assertEqual(rp.check_capacity(plan, range(0, 16), nodes), [])
        shortfalls = rp.check_capacity(plan, range(0, 12), nodes)
        self.assertEqual(len(shortfalls), 1)
        self.assertIn('unaligned allocations', shortfalls[0])


class SerialisationTestCase(unittest.TestCase):

    def test_plan_to_dict(self):
        plan = rp.build_plan(rp.PROFILE_BALANCED, [osd(0, data_node=1)],
                             host())
        data = plan.to_dict()
        self.assertEqual(data['profile'], rp.PROFILE_BALANCED)
        self.assertEqual(data['osds'], [{
            'osd-id': '0',
            'tier': rp.TIER_NVME,
            'cores': 8,
            'mode': rp.MODE_NUMA,
            'numa-node': 1,
            'numa-policy': 'preferred',
            'deviations': [],
        }])

    def test_request_repr(self):
        plan = rp.build_plan(rp.PROFILE_MINIMAL, [osd(2)], host())
        self.assertIn("osd_id='2'", repr(plan.requests[0]))


class AddressChoiceTestCase(unittest.TestCase):

    def test_binds_the_address_on_the_osd_node(self):
        choice = rp.choose_address(PUBLIC, 1)
        self.assertEqual(choice.outcome, rp.BIND_LOCAL)
        self.assertEqual(choice.address, '10.0.1.5')
        self.assertIn('ens2f0', choice.message)
        self.assertIn('ceph-public-network', choice.message)

    def test_keeps_a_local_host_address(self):
        choice = rp.choose_address(PUBLIC, 0)
        self.assertEqual(choice.outcome, rp.BIND_HOST)
        self.assertIsNone(choice.address)
        self.assertIn('10.0.0.5', choice.message)

    def test_falls_back_without_an_address_on_the_node(self):
        single_nic = network(rp.NETWORK_PUBLIC, '10.0.0.5',
                             candidate('10.0.0.5', 'ens1f0', 0))
        choice = rp.choose_address(single_nic, 1)
        self.assertEqual(choice.outcome, rp.BIND_REMOTE)
        self.assertIsNone(choice.address)
        self.assertIn('NUMA node 1', choice.message)
        self.assertIn('10.0.0.5 on ens1f0 (NUMA node 0)', choice.message)
        self.assertIn('keeping the host public address 10.0.0.5',
                      choice.message)

    def test_unknown_locality_keeps_the_host_address(self):
        virtual = network(rp.NETWORK_PUBLIC, '10.0.0.5',
                          candidate('10.0.0.5', 'eth0'),
                          candidate('10.0.1.5', 'eth1'))
        choice = rp.choose_address(virtual, 1)
        self.assertEqual(choice.outcome, rp.BIND_UNKNOWN)
        self.assertIsNone(choice.address)
        self.assertIn('unknown', choice.message)

    def test_no_candidates_is_unknown(self):
        choice = rp.choose_address(
            network(rp.NETWORK_PUBLIC, '10.0.0.5'), 1)
        self.assertEqual(choice.outcome, rp.BIND_UNKNOWN)
        self.assertIn('candidates: none', choice.message)

    def test_unpinned_osd_keeps_the_host_address(self):
        choice = rp.choose_address(PUBLIC, None)
        self.assertEqual(choice.outcome, rp.BIND_UNPINNED)
        self.assertIsNone(choice.address)

    def test_single_node_host_keeps_the_host_address(self):
        choice = rp.choose_address(PUBLIC, 1, single_node=True)
        self.assertEqual(choice.outcome, rp.BIND_HOST)
        self.assertIsNone(choice.address)

    def test_without_a_host_address_ceph_selection_is_overridden(self):
        # prefer-ipv6 with a configured network leaves the choice to Ceph,
        # which may pick an address on the other node.
        ipv6 = network(rp.NETWORK_PUBLIC, None,
                       candidate('fd00::5', 'ens1f0', 0))
        choice = rp.choose_address(ipv6, 0)
        self.assertEqual(choice.outcome, rp.BIND_LOCAL)
        self.assertEqual(choice.address, 'fd00::5')

    def test_node_exclusive_interface_beats_a_spanning_host_bond(self):
        bonded = network(rp.NETWORK_PUBLIC, '10.0.0.5',
                         candidate('10.0.0.5', 'bond0', 0, 1),
                         candidate('10.0.9.5', 'ens2f0', 1))
        choice = rp.choose_address(bonded, 1)
        self.assertEqual(choice.address, '10.0.9.5')
        self.assertFalse(choice.spanning)
        # The spanning bond is still local to node 0.
        self.assertEqual(rp.choose_address(bonded, 0).outcome, rp.BIND_HOST)

    def test_spanning_host_bond_is_kept_but_marked(self):
        bonded = network(rp.NETWORK_PUBLIC, '10.0.0.5',
                         candidate('10.0.0.5', 'bond0.20', 0, 1,
                                   note=NO_PRIMARY))
        choice = rp.choose_address(bonded, 1)
        self.assertEqual(choice.outcome, rp.BIND_HOST)
        self.assertIsNone(choice.address)
        self.assertTrue(choice.spanning)
        self.assertEqual(
            choice.message,
            'keeping host public address 10.0.0.5 on bond0.20, which spans '
            'NUMA nodes 0,1 and is not reliably local to node 1; ' +
            NO_PRIMARY)

    def test_spanning_bond_is_bound_when_nothing_else_is_local(self):
        bonded = network(rp.NETWORK_PUBLIC, '10.0.0.5',
                         candidate('10.0.0.5', 'ens1f0', 0),
                         candidate('10.0.9.5', 'bond1', 0, 1))
        choice = rp.choose_address(bonded, 1)
        self.assertEqual(choice.outcome, rp.BIND_LOCAL)
        self.assertEqual(choice.address, '10.0.9.5')
        self.assertTrue(choice.spanning)
        self.assertEqual(
            choice.message,
            'binding public address 10.0.9.5 (from ceph-public-network) on '
            'bond1, which spans NUMA nodes 0,1 and is not reliably local to '
            'node 1')

    def test_host_address_beats_a_lower_local_address(self):
        two = network(rp.NETWORK_PUBLIC, '10.0.0.9',
                      candidate('10.0.0.1', 'ens1f0', 0),
                      candidate('10.0.0.9', 'ens1f1', 0))
        self.assertEqual(rp.choose_address(two, 0).outcome, rp.BIND_HOST)

    def test_lowest_address_breaks_remaining_ties(self):
        two = network(rp.NETWORK_PUBLIC, '10.0.0.5',
                      candidate('10.0.1.10', 'ens2f1', 1),
                      candidate('10.0.1.9', 'ens2f0', 1))
        self.assertEqual(rp.choose_address(two, 1).address, '10.0.1.9')

    def test_choose_addresses_covers_every_network(self):
        choices = rp.choose_addresses(networked_host(), 1)
        self.assertEqual([(c.role, c.address) for c in choices],
                         [(rp.NETWORK_PUBLIC, '10.0.1.5'),
                          (rp.NETWORK_CLUSTER, '10.1.1.5')])

    def test_choose_addresses_without_networks(self):
        self.assertEqual(rp.choose_addresses(host(), 1), [])


class NetworkPlanTestCase(unittest.TestCase):

    def test_strict_profile_accepts_a_cluster_nic_per_socket(self):
        osds = [osd(0, data_node=0), osd(1, data_node=1)]
        plan = rp.build_plan(rp.PROFILE_PERFORMANCE, osds, networked_host())
        self.assertTrue(plan.satisfiable)
        self.assertEqual(plan.deviations(), [])

    def test_strict_profile_refuses_a_remote_cluster_network(self):
        cluster = network(rp.NETWORK_CLUSTER, '10.1.0.5',
                          candidate('10.1.0.5', 'ens1f1', 0))
        plan = rp.build_plan(rp.PROFILE_PERFORMANCE, [osd(1, data_node=1)],
                             networked_host(cluster=cluster))
        self.assertFalse(plan.satisfiable)
        self.assertIn('ens1f1', plan.errors[0])

    def test_strict_profile_warns_about_a_remote_public_network(self):
        public = network(rp.NETWORK_PUBLIC, '10.0.0.5',
                         candidate('10.0.0.5', 'bond0', 0))
        plan = rp.build_plan(rp.PROFILE_PERFORMANCE, [osd(1, data_node=1)],
                             networked_host(public=public))
        self.assertTrue(plan.satisfiable)
        self.assertEqual(len(plan.deviations()), 1)
        self.assertIn('osd.1: no public network address on NUMA node 1',
                      plan.deviations()[0])

    def test_strict_profile_does_not_repeat_a_shared_network_warning(self):
        # Without a separate cluster network both roles share candidates;
        # the cluster check already speaks for the public network.
        shared = network(rp.NETWORK_CLUSTER, '10.0.0.5',
                         *PUBLIC.candidates)
        plan = rp.build_plan(rp.PROFILE_PERFORMANCE, [osd(1, data_node=1)],
                             networked_host(cluster=shared))
        self.assertTrue(plan.satisfiable)
        self.assertEqual(plan.deviations(), [])

    def test_strict_profile_reports_a_spanning_cluster_bond(self):
        bond = candidate('10.1.0.5', 'bond0', 0, 1, note=NO_PRIMARY)
        cluster = network(rp.NETWORK_CLUSTER, '10.1.0.5', bond)
        public = network(rp.NETWORK_PUBLIC, '10.1.0.5', bond)
        plan = rp.build_plan(rp.PROFILE_PERFORMANCE, [osd(1, data_node=1)],
                             networked_host(public=public, cluster=cluster))
        self.assertTrue(plan.satisfiable)
        # One deviation: the shared public network is not reported again.
        self.assertEqual(plan.deviations(), [
            'osd.1: keeping host cluster address 10.1.0.5 on bond0, which '
            'spans NUMA nodes 0,1 and is not reliably local to node 1; ' +
            NO_PRIMARY])

    def test_strict_profile_prefers_a_wholly_local_cluster_nic(self):
        cluster = network(rp.NETWORK_CLUSTER, '10.1.0.5',
                          candidate('10.1.0.5', 'bond0', 0, 1),
                          candidate('10.1.1.5', 'ens2f1', 1))
        plan = rp.build_plan(rp.PROFILE_PERFORMANCE, [osd(1, data_node=1)],
                             networked_host(cluster=cluster))
        self.assertTrue(plan.satisfiable)
        self.assertEqual(plan.deviations(), [])

    def test_strict_profile_reports_a_spanning_public_bond(self):
        public = network(rp.NETWORK_PUBLIC, '10.0.0.5',
                         candidate('10.0.0.5', 'bond0', 0, 1,
                                   note=NO_PRIMARY))
        plan = rp.build_plan(rp.PROFILE_PERFORMANCE, [osd(1, data_node=1)],
                             networked_host(public=public))
        self.assertTrue(plan.satisfiable)
        self.assertEqual(len(plan.deviations()), 1)
        self.assertIn('keeping host public address 10.0.0.5 on bond0',
                      plan.deviations()[0])

    def test_balanced_profile_does_not_report_a_spanning_bond(self):
        bond = candidate('10.1.0.5', 'bond0', 0, 1, note=NO_PRIMARY)
        plan = rp.build_plan(
            rp.PROFILE_BALANCED, [osd(1, data_node=1)], networked_host(
                cluster=network(rp.NETWORK_CLUSTER, '10.1.0.5', bond)))
        self.assertEqual(plan.deviations(), [])

    def test_balanced_profile_does_not_warn_about_networks(self):
        public = network(rp.NETWORK_PUBLIC, '10.0.0.5',
                         candidate('10.0.0.5', 'bond0', 0))
        plan = rp.build_plan(rp.PROFILE_BALANCED, [osd(1, data_node=1)],
                             networked_host(public=public))
        self.assertEqual(plan.deviations(), [])
