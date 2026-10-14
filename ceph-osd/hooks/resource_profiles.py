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

"""OSD resource profiles and the (pure) allocation planner.

This module owns the profile definitions and turns host facts into a
desired allocation plan.  It performs no I/O: discovery lives in
:mod:`host_topology`, claiming CPUs lives in :mod:`epa_client`, and
applying the plan lives in :mod:`resource_apply`.

Only CPU resources are planned in this iteration.  Memory is handled
solely as a NUMA *locality* hint (systemd ``NUMAPolicy``); no memory
sizing, hugepages or Ceph settings are computed here.
"""

import collections

PROFILE_UNMANAGED = 'unmanaged'
PROFILE_PERFORMANCE = 'performance'
PROFILE_BALANCED = 'balanced'
PROFILE_MINIMAL = 'minimal'

PROFILES = (
    PROFILE_UNMANAGED,
    PROFILE_PERFORMANCE,
    PROFILE_BALANCED,
    PROFILE_MINIMAL,
)

TIER_NVME = 'nvme'
TIER_HDD = 'hdd'

# Logical CPUs per OSD, by profile and device tier.
PROFILE_CORES = {
    PROFILE_PERFORMANCE: {TIER_NVME: 12, TIER_HDD: 4},
    PROFILE_BALANCED: {TIER_NVME: 8, TIER_HDD: 2},
    PROFILE_MINIMAL: {TIER_NVME: 2, TIER_HDD: 2},
}

# systemd NUMAPolicy applied alongside the CPU affinity.  'bind' is a
# strict memory binding, 'preferred' is a hint, None leaves the default.
PROFILE_NUMA_POLICY = {
    PROFILE_PERFORMANCE: 'bind',
    PROFILE_BALANCED: 'preferred',
    PROFILE_MINIMAL: None,
}

# Profiles that refuse to apply anything they cannot satisfy exactly.
STRICT_PROFILES = frozenset([PROFILE_PERFORMANCE])

# Allocation modes for a single OSD.
MODE_NUMA = 'numa'
MODE_ANY = 'any'

OsdFacts = collections.namedtuple(
    'OsdFacts',
    ['osd_id', 'data_device', 'aux_devices', 'tier', 'data_node',
     'aux_nodes'])

HostFacts = collections.namedtuple(
    'HostFacts', ['nodes', 'nic_interface', 'nic_nodes'])


def is_managed(profile):
    """Report whether a profile results in charm-managed allocations."""
    return profile in PROFILE_CORES


def is_strict(profile):
    """Report whether a profile refuses partial satisfaction."""
    return profile in STRICT_PROFILES


def tier_for_rotational(rotational):
    """Classify a device tier from its rotational flag.

    Non-rotational devices (NVMe and SATA/SAS SSDs alike) get the NVMe
    core counts; rotational devices get the HDD counts.  An unknown
    flag is treated as rotational, which is the frugal choice.

    :param rotational: rotational flag, None when unknown
    :type rotational: bool or None
    :rtype: str
    """
    if rotational is None:
        return TIER_HDD
    return TIER_HDD if rotational else TIER_NVME


def cores_for(profile, tier):
    """Return the logical CPU count for a profile and device tier.

    :rtype: int
    """
    return PROFILE_CORES[profile][tier]


class OsdRequest(object):
    """The desired allocation for a single OSD."""

    def __init__(self, osd_id, tier, cores, mode, numa_node=None,
                 numa_policy=None, deviations=None):
        self.osd_id = str(osd_id)
        self.tier = tier
        self.cores = cores
        self.mode = mode
        self.numa_node = numa_node
        self.numa_policy = numa_policy
        self.deviations = list(deviations or [])

    def __repr__(self):
        return (
            'OsdRequest(osd_id={0.osd_id!r}, tier={0.tier!r}, '
            'cores={0.cores!r}, mode={0.mode!r}, '
            'numa_node={0.numa_node!r})'.format(self)
        )

    def to_dict(self):
        """Render the request as plain data (for state and actions)."""
        return {
            'osd-id': self.osd_id,
            'tier': self.tier,
            'cores': self.cores,
            'mode': self.mode,
            'numa-node': self.numa_node,
            'numa-policy': self.numa_policy,
            'deviations': list(self.deviations),
        }


class Plan(object):
    """A profile's desired allocation for every local OSD."""

    def __init__(self, profile, requests=None, errors=None, warnings=None):
        self.profile = profile
        self.requests = list(requests or [])
        self.errors = list(errors or [])
        self.warnings = list(warnings or [])

    @property
    def satisfiable(self):
        """True when nothing prevents the plan from being applied."""
        return not self.errors

    def deviations(self):
        """Return every per-OSD deviation, prefixed with the OSD ID."""
        result = []
        for request in self.requests:
            for deviation in request.deviations:
                result.append('osd.{}: {}'.format(request.osd_id, deviation))
        return result

    def demand_by_node(self):
        """Total NUMA-bound logical CPU demand, per NUMA node.

        :rtype: dict[int, int]
        """
        demand = collections.defaultdict(int)
        for request in self.requests:
            if request.mode == MODE_NUMA:
                demand[request.numa_node] += request.cores
        return dict(demand)

    def demand_unbound(self):
        """Total logical CPU demand that is not bound to a NUMA node.

        :rtype: int
        """
        return sum(r.cores for r in self.requests if r.mode == MODE_ANY)

    def to_dict(self):
        """Render the plan as plain data (for state and actions)."""
        return {
            'profile': self.profile,
            'osds': [r.to_dict() for r in self.requests],
            'errors': list(self.errors),
            'warnings': list(self.warnings),
        }


def _target_node(profile, osd, host, deviations, errors):
    """Resolve the NUMA node an OSD should be pinned to.

    :returns: the NUMA node, or None to allocate without NUMA locality
    :rtype: int or None
    """
    single_node = len(host.nodes) <= 1
    if single_node:
        # A single-node host trivially satisfies every alignment
        # requirement, including the NIC and memory constraints.
        return sorted(host.nodes)[0] if host.nodes else 0

    node = osd.data_node
    if node is None:
        message = (
            'NUMA node of data device {} is unknown'.format(osd.data_device))
        if is_strict(profile):
            errors.append(message)
            return None
        deviations.append('{}; allocating without NUMA locality'.format(
            message))
        return None

    if node not in host.nodes:
        message = 'data device {} reports unknown NUMA node {}'.format(
            osd.data_device, node)
        if is_strict(profile):
            errors.append(message)
            return None
        deviations.append('{}; allocating without NUMA locality'.format(
            message))
        return None

    # Auxiliary (block.db / block.wal) devices.
    for device, aux_node in sorted(osd.aux_nodes.items()):
        if aux_node == node:
            continue
        described = 'node {}'.format(aux_node) if aux_node is not None \
            else 'an unknown node'
        message = (
            'device {} is on {} but data device {} is on node {}'.format(
                device, described, osd.data_device, node))
        if is_strict(profile):
            errors.append(message)
            return None
        deviations.append(message)

    # NIC alignment is only a strict-profile requirement.
    if is_strict(profile):
        if not host.nic_nodes:
            deviations.append(
                'NUMA node of Ceph network interface {} is unknown; '
                'NIC alignment not verified'.format(
                    host.nic_interface or 'unknown'))
        elif node not in host.nic_nodes:
            errors.append(
                'data device {} is on node {} but Ceph network interface '
                '{} is on node(s) {}'.format(
                    osd.data_device, node, host.nic_interface,
                    ','.join(str(n) for n in sorted(host.nic_nodes))))
            return None
    return node


def build_plan(profile, osds, host):
    """Compute the desired allocation for a profile.

    :param profile: profile name
    :type profile: str
    :param osds: local OSD facts
    :type osds: list[OsdFacts]
    :param host: host topology facts
    :type host: HostFacts
    :rtype: Plan
    """
    if not is_managed(profile):
        return Plan(profile)

    plan = Plan(profile)
    policy = PROFILE_NUMA_POLICY[profile]
    for osd in sorted(osds, key=lambda o: int(o.osd_id)):
        deviations = []
        errors = []
        cores = cores_for(profile, osd.tier)
        if profile == PROFILE_MINIMAL:
            # The minimal profile deliberately requests no NUMA
            # alignment at all.
            node = None
        else:
            node = _target_node(profile, osd, host, deviations, errors)
        if errors:
            plan.errors.extend(
                'osd.{}: {}'.format(osd.osd_id, error) for error in errors)
            continue
        mode = MODE_NUMA if node is not None else MODE_ANY
        plan.requests.append(OsdRequest(
            osd_id=osd.osd_id,
            tier=osd.tier,
            cores=cores,
            mode=mode,
            numa_node=node,
            numa_policy=policy if mode == MODE_NUMA else None,
            deviations=deviations,
        ))
    return plan


def check_capacity(plan, free_cpus, nodes):
    """Verify a plan fits the CPUs the allocator can currently grant.

    This is a preflight check: for strict profiles it lets the charm
    refuse a plan before making any partial claim.

    :param plan: the desired plan
    :type plan: Plan
    :param free_cpus: logical CPUs the charm may claim
    :type free_cpus: iterable[int]
    :param nodes: NUMA node to logical CPU mapping
    :type nodes: dict[int, list[int]]
    :returns: shortfall messages, empty when the plan fits
    :rtype: list[str]
    """
    free = set(free_cpus)
    shortfalls = []
    remaining = len(free)
    for node, demand in sorted(plan.demand_by_node().items()):
        available = len(free & set(nodes.get(node, [])))
        if demand > available:
            shortfalls.append(
                'NUMA node {} needs {} logical CPUs, {} available'.format(
                    node, demand, available))
        remaining -= min(demand, available)
    unbound = plan.demand_unbound()
    if unbound > remaining:
        shortfalls.append(
            'unaligned allocations need {} logical CPUs, {} available'.format(
                unbound, max(remaining, 0)))
    return shortfalls
