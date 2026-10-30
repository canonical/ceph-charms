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
sizing, hugepages or Ceph settings are computed here.  The network
address an OSD binds is chosen to be local to the NUMA node it is pinned
to, when the host has such an address.
"""

import collections
import ipaddress

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

TIERS = (TIER_NVME, TIER_HDD)

RESOURCE_CPUS = 'cpus'

# Logical CPUs per OSD, by profile and device tier.
PROFILE_CORES = {
    PROFILE_PERFORMANCE: {TIER_NVME: 16, TIER_HDD: 4},
    PROFILE_BALANCED: {TIER_NVME: 8, TIER_HDD: 2},
    PROFILE_MINIMAL: {TIER_NVME: 2, TIER_HDD: 2},
}

# Resources an operator may override, per profile.
OVERRIDABLE_RESOURCES = {
    PROFILE_PERFORMANCE: (RESOURCE_CPUS,),
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

# ``nic_interface`` and ``nic_nodes`` describe the interfaces holding a
# cluster network address and their NUMA nodes; ``networks`` maps each
# Ceph network role to its NetworkFacts, or is None when not discovered.
HostFacts = collections.namedtuple(
    'HostFacts', ['nodes', 'nic_interface', 'nic_nodes', 'networks'])
HostFacts.__new__.__defaults__ = (None,)

# Ceph networks an OSD binds an address on.
NETWORK_PUBLIC = 'public'
NETWORK_CLUSTER = 'cluster'
NETWORK_ROLES = (NETWORK_PUBLIC, NETWORK_CLUSTER)

# A local address an OSD could bind; ``nodes`` is the sorted tuple of its
# interface's NUMA nodes, empty when unknown; ``note`` explains why the
# interface spans several nodes, or is None.
NetworkCandidate = collections.namedtuple(
    'NetworkCandidate', ['address', 'interface', 'nodes', 'note'])
NetworkCandidate.__new__.__defaults__ = (None,)

# The candidate addresses of one Ceph network.  ``host_address`` is the
# address ceph.conf gives every OSD, or None when Ceph selects one itself;
# ``source`` describes where the candidates came from.
NetworkFacts = collections.namedtuple(
    'NetworkFacts', ['role', 'source', 'host_address', 'candidates'])

# Outcomes of choosing the address an OSD binds on one network.
BIND_LOCAL = 'local'
BIND_HOST = 'host'
BIND_REMOTE = 'remote'
BIND_UNKNOWN = 'unknown'
BIND_UNPINNED = 'unpinned'

# Outcomes where the OSD keeps an address that is not known to be local.
BIND_FALLBACKS = frozenset([BIND_REMOTE, BIND_UNKNOWN])

# ``address`` is the address the OSD must bind, or None to keep the host
# address; ``spanning`` is set when the chosen interface spans NUMA nodes,
# so the address is not reliably local.
AddressChoice = collections.namedtuple(
    'AddressChoice', ['role', 'address', 'outcome', 'message', 'spanning'])
AddressChoice.__new__.__defaults__ = (False,)


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


def _positive_int(value, what):
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(
            '{} must be a positive integer, got {!r}'.format(what, value))
    return value


def resolve_overrides(overrides):
    """Validate operator resource overrides and normalise them.

    ``overrides`` maps a profile to its resources, for example
    ``{'performance': {'cpus': 16}}``.  A CPU count applies to every
    device tier; a mapping such as ``{'nvme': 16, 'hdd': 6}`` overrides
    individual tiers and leaves the others at the profile default.

    :param overrides: overrides as configured, None for none
    :type overrides: dict or None
    :returns: profile -> resource -> tier -> value, for overridden tiers
    :rtype: dict[str, dict[str, dict[str, int]]]
    :raises ValueError: the overrides are malformed or not supported
    """
    if overrides is None:
        return {}
    if not isinstance(overrides, dict):
        raise ValueError('expected a mapping of profile to resources, '
                         'got {!r}'.format(overrides))
    resolved = {}
    for profile, resources in overrides.items():
        if profile not in OVERRIDABLE_RESOURCES:
            raise ValueError(
                'resources of profile {!r} cannot be overridden, expected '
                'one of {}'.format(
                    profile, ', '.join(sorted(OVERRIDABLE_RESOURCES))))
        if not isinstance(resources, dict):
            raise ValueError(
                '{}: expected a mapping of resource to value, got '
                '{!r}'.format(profile, resources))
        for resource, value in resources.items():
            if resource not in OVERRIDABLE_RESOURCES[profile]:
                raise ValueError(
                    '{}: resource {!r} cannot be overridden, expected one '
                    'of {}'.format(profile, resource, ', '.join(
                        OVERRIDABLE_RESOURCES[profile])))
            what = '{}.{}'.format(profile, resource)
            if isinstance(value, dict):
                unknown = [tier for tier in value if tier not in TIERS]
                if unknown:
                    raise ValueError(
                        '{}: unknown device tier {!r}, expected one of '
                        '{}'.format(what, unknown[0], ', '.join(TIERS)))
                counts = dict(
                    (tier, _positive_int(count, '{}.{}'.format(what, tier)))
                    for tier, count in value.items())
            else:
                count = _positive_int(value, what)
                counts = dict((tier, count) for tier in TIERS)
            resolved.setdefault(profile, {})[resource] = counts
    return resolved


def cores_for(profile, tier, overrides=None):
    """Return the logical CPU count for a profile and device tier.

    :param overrides: resolved overrides, see :func:`resolve_overrides`
    :type overrides: dict or None
    :rtype: int
    """
    cpus = (overrides or {}).get(profile, {}).get(RESOURCE_CPUS, {})
    return cpus.get(tier, PROFILE_CORES[profile][tier])


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


def _address_key(address):
    """Sort key ordering addresses numerically."""
    parsed = ipaddress.ip_address(address)
    return (parsed.version, int(parsed))


def _describe_candidate(candidate):
    """Render a candidate address with its interface and NUMA nodes."""
    if not candidate.nodes:
        where = 'NUMA node unknown'
    else:
        where = 'NUMA node{} {}'.format(
            's' if len(candidate.nodes) > 1 else '',
            ','.join(str(n) for n in candidate.nodes))
    return '{} on {} ({})'.format(
        candidate.address, candidate.interface, where)


def _host_address_text(network):
    """Describe the address an OSD uses without an override."""
    if network.host_address:
        return 'the host {} address {}'.format(
            network.role, network.host_address)
    return 'the {} address Ceph selects'.format(network.role)


def choose_address(network, node, single_node=False):
    """Choose the address an OSD binds on one Ceph network.

    The OSD keeps the host address unless a different candidate sits on
    the NUMA node it is pinned to.  Among local candidates, an interface
    wholly on that node beats one spanning nodes (such as a cross-socket
    bond), then the host address wins, then the lowest address.  A choice
    on a spanning interface is marked, since it is not reliably local.

    :param network: the network's candidate addresses
    :type network: NetworkFacts
    :param node: NUMA node the OSD is pinned to, None when unpinned
    :type node: int or None
    :param single_node: whether the host has a single NUMA node
    :type single_node: bool
    :rtype: AddressChoice
    """
    role = network.role
    host = _host_address_text(network)
    if node is None:
        return AddressChoice(
            role, None, BIND_UNPINNED,
            'not pinned to a NUMA node; keeping {}'.format(host))
    if single_node:
        return AddressChoice(
            role, None, BIND_HOST,
            'single NUMA node host; keeping {}'.format(host))
    local = [c for c in network.candidates if node in c.nodes]
    if not local:
        listed = ', '.join(
            _describe_candidate(c) for c in network.candidates) or 'none'
        if any(c.nodes for c in network.candidates):
            return AddressChoice(
                role, None, BIND_REMOTE,
                'no {} network address on NUMA node {} (candidates: {}); '
                'keeping {}'.format(role, node, listed, host))
        return AddressChoice(
            role, None, BIND_UNKNOWN,
            'NUMA node of the {} network interfaces is unknown '
            '(candidates: {}); keeping {}'.format(role, listed, host))
    chosen = min(local, key=lambda c: (
        tuple(c.nodes) != (node,),
        c.address != network.host_address,
        _address_key(c.address)))
    if tuple(chosen.nodes) != (node,):
        # No interface is wholly on the node; this one only partly is.
        spans = '{}, which spans NUMA nodes {} and is not reliably local ' \
            'to node {}{}'.format(
                chosen.interface, ','.join(str(n) for n in chosen.nodes),
                node, '; {}'.format(chosen.note) if chosen.note else '')
        if chosen.address == network.host_address:
            return AddressChoice(
                role, None, BIND_HOST,
                'keeping host {} address {} on {}'.format(
                    role, chosen.address, spans), True)
        return AddressChoice(
            role, chosen.address, BIND_LOCAL,
            'binding {} address {} (from {}) on {}'.format(
                role, chosen.address, network.source, spans), True)
    if chosen.address == network.host_address:
        return AddressChoice(
            role, None, BIND_HOST,
            'host {} address {} on {} is local to NUMA node {}'.format(
                role, chosen.address, chosen.interface, node))
    return AddressChoice(
        role, chosen.address, BIND_LOCAL,
        'binding {} address {} on {}, local to NUMA node {} '
        '(from {})'.format(role, chosen.address, chosen.interface, node,
                           network.source))


def choose_addresses(host, node):
    """Choose an OSD's address on every discovered Ceph network.

    :param host: host topology facts
    :type host: HostFacts
    :param node: NUMA node the OSD is pinned to, None when unpinned
    :type node: int or None
    :rtype: list[AddressChoice]
    """
    networks = host.networks or {}
    single_node = len(host.nodes) <= 1
    return [choose_address(networks[role], node, single_node=single_node)
            for role in NETWORK_ROLES if role in networks]


def _public_network_deviation(host, node):
    """Describe a strict OSD whose public address is not reliably local.

    The strict NIC requirement covers the cluster network.  The public
    network only warns, since it is commonly one interface shared by the
    whole host; when it has the same candidates as the cluster network,
    the cluster check already covers it.

    :rtype: str or None
    """
    networks = host.networks or {}
    public = networks.get(NETWORK_PUBLIC)
    cluster = networks.get(NETWORK_CLUSTER)
    if public is None:
        return None
    if cluster is not None and (
            set(c.address for c in cluster.candidates) ==
            set(c.address for c in public.candidates)):
        return None
    choice = choose_address(public, node)
    if choice.outcome in BIND_FALLBACKS or choice.spanning:
        return choice.message
    return None


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

    # NIC alignment is only a strict-profile requirement.  Any interface
    # holding a cluster network address on the OSD's node satisfies it;
    # one that also spans other nodes only with a deviation.
    if is_strict(profile):
        if not host.nic_nodes:
            deviations.append(
                'NUMA node of Ceph cluster network interfaces ({}) is '
                'unknown; NIC alignment not verified'.format(
                    host.nic_interface or 'unknown'))
        elif node not in host.nic_nodes:
            errors.append(
                'data device {} is on node {} but Ceph cluster network '
                'interfaces ({}) are on node(s) {}'.format(
                    osd.data_device, node, host.nic_interface,
                    ','.join(str(n) for n in sorted(host.nic_nodes))))
            return None
        elif NETWORK_CLUSTER in (host.networks or {}):
            choice = choose_address(host.networks[NETWORK_CLUSTER], node)
            if choice.spanning:
                deviations.append(choice.message)
        deviation = _public_network_deviation(host, node)
        if deviation:
            deviations.append(deviation)
    return node


def build_plan(profile, osds, host, overrides=None):
    """Compute the desired allocation for a profile.

    :param profile: profile name
    :type profile: str
    :param osds: local OSD facts
    :type osds: list[OsdFacts]
    :param host: host topology facts
    :type host: HostFacts
    :param overrides: resolved overrides, see :func:`resolve_overrides`
    :type overrides: dict or None
    :rtype: Plan
    """
    if not is_managed(profile):
        return Plan(profile)

    plan = Plan(profile)
    policy = PROFILE_NUMA_POLICY[profile]
    for osd in sorted(osds, key=lambda o: int(o.osd_id)):
        deviations = []
        errors = []
        cores = cores_for(profile, osd.tier, overrides)
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
