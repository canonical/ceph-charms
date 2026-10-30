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

"""Charm-facing orchestration of OSD resource allocation.

This module wires together host discovery, the EPA orchestrator, the
profile planner and enforcement:

1. collect inputs (profile, OSD inventory, host topology),
2. calculate the desired plan (the same calculation used for previews),
3. validate it against the CPUs the allocator can grant,
4. claim the CPUs, and
5. enforce them, restarting only the OSDs whose allocation changed.

Enforcement also binds an OSD to a public or cluster network address on
the NUMA node it is pinned to, when the host has one there.

Allocations are applied automatically, without an operator-initiated restart.
The hook-facing entry point admits one host at a time through the shared Ceph
rolling-operation protocol before calculating, claiming or enforcing resources.
"""

import collections
import ipaddress
import traceback

import yaml

from charmhelpers.core.hookenv import (
    config,
    log,
    network_get,
    resource_get,
    DEBUG,
    ERROR,
    INFO,
    WARNING,
)
from charmhelpers.core.unitdata import kv

import epa_snap
import resource_apply
import resource_rollout

from epa_client import (
    EpaClient,
    EpaError,
    EpaRequestError,
    claimed_by_others,
    eligible_cpus,
    allocations_by_service,
    osd_id_for,
    pool_source,
    service_name_for,
)
from host_topology import (
    TopologyError,
    device_is_rotational,
    device_numa_node,
    format_cpu_list,
    parse_cpu_list,
    interface_numa_nodes,
    local_addresses,
    numa_nodes,
    osd_device_map,
    spanning_bonds,
)
from resource_profiles import (
    BIND_FALLBACKS,
    HostFacts,
    MODE_ANY,
    MODE_NUMA,
    NETWORK_CLUSTER,
    NETWORK_PUBLIC,
    NETWORK_ROLES,
    NetworkCandidate,
    NetworkFacts,
    OsdFacts,
    PROFILE_UNMANAGED,
    PROFILES,
    RESOURCE_CPUS,
    build_plan,
    check_capacity,
    choose_addresses,
    is_managed,
    is_strict,
    resolve_overrides,
    tier_for_rotational,
)

STATE_KEY = 'resource-alloc.state'
MANAGED_KEY = 'resource-alloc.epa-charm-managed'
PENDING_POOL_KEY = 'resource-alloc.epa-pending-pool'
STATE_VERSION = 1
AFFINITY_PROTECTION_VERSION = 1

RESOURCE_NAME = 'epa-orchestrator'

ResourceStatus = collections.namedtuple(
    'ResourceStatus', ['blocked', 'message', 'waiting'])
ResourceStatus.__new__.__defaults__ = (False,)


class ResourceError(Exception):
    """Raised when the requested allocation cannot be carried out."""


def _config(key, default=None):
    """Read a charm config option, tolerating absent options."""
    try:
        value = config(key)
    except Exception:
        return default
    return default if value is None else value


def configured_profile():
    """Return the configured profile name.

    :raises ResourceError: the configured value is not a known profile
    """
    profile = _config('performance-profile', PROFILE_UNMANAGED)
    if profile not in PROFILES:
        raise ResourceError(
            'invalid performance-profile {!r}, expected one of {}'.format(
                profile, ', '.join(PROFILES)))
    return profile


def configured_overrides():
    """Return the validated profile resource overrides.

    :returns: resolved overrides, see
        :func:`resource_profiles.resolve_overrides`
    :rtype: dict
    :raises ResourceError: the configured value is not valid
    """
    value = _config('performance-profile-resources', '')
    try:
        return resolve_overrides(yaml.safe_load(value) if value else None)
    except (yaml.YAMLError, ValueError) as exc:
        raise ResourceError(
            'invalid performance-profile-resources: {}'.format(
                ' '.join(str(exc).split())))


def _warn_oversized_overrides(profile, overrides, nodes):
    """Log overridden CPU counts that exceed a node's share of the pool.

    The charm never offers more than ``epa_snap.MAX_POOL_PERCENT`` of a
    NUMA node's logical CPUs to EPA, so an OSD asking for more cannot be
    placed on that node.  This only warns: the planner and the capacity
    check decide what happens to the request.
    """
    cpus = overrides.get(profile, {}).get(RESOURCE_CPUS, {})
    for tier, count in sorted(cpus.items()):
        over = [str(node) for node, node_cpus in sorted(nodes.items())
                if node_cpus and count > epa_snap.pool_cap(node_cpus)]
        if over:
            log('performance-profile-resources: {} logical CPUs per {} OSD '
                'for profile {} exceed {}% of the logical CPUs of NUMA '
                'node(s) {}'.format(count, tier, profile,
                                    epa_snap.MAX_POOL_PERCENT,
                                    ','.join(over)),
                level=WARNING)


def warnings_suppressed():
    """Report whether profile deviation warnings are suppressed."""
    return bool(_config('suppress-profile-warnings', False))


def affinity_protection_required():
    """Whether rendered OSD config must prevent Ceph widening affinity."""
    previous = get_state()
    return (is_managed(_config('performance-profile', PROFILE_UNMANAGED)) or
            is_managed(previous.get('profile', PROFILE_UNMANAGED)) or
            bool(previous.get('osds')))


def get_state():
    """Return the last recorded allocation state."""
    return kv().get(STATE_KEY) or {}


def _save_state(state):
    """Persist the allocation state."""
    database = kv()
    database.set(STATE_KEY, state)
    database.flush()


def _epa_charm_managed(value=None):
    """Get or set whether the charm owns the EPA installation."""
    database = kv()
    if value is not None:
        database.set(MANAGED_KEY, bool(value))
        database.flush()
    return bool(database.get(MANAGED_KEY))


def _host_address(role):
    """Return the address ceph.conf gives every OSD on one Ceph network.

    This mirrors the ceph.conf rendering: with ``prefer-ipv6`` and a
    configured network, ceph.conf names no address and Ceph selects one.

    :param role: ``public`` or ``cluster``
    :type role: str
    :rtype: str or None
    """
    import utils
    if _config('prefer-ipv6', False):
        if utils.get_networks('ceph-{}-network'.format(role)):
            return None
        return utils.get_ipv6_addr()[0]
    if role == NETWORK_PUBLIC:
        return utils.get_public_addr()
    return utils.get_cluster_addr()


def _binding_addresses(role):
    """Return this unit's addresses on a Juju binding, None when unknown.

    :rtype: set[str] or None
    """
    try:
        info = network_get(role)
    except Exception as exc:
        log('Cannot read the {} binding: {}'.format(role, exc), level=DEBUG)
        return None
    addresses = set()
    for binding in (info or {}).get('bind-addresses') or []:
        for entry in binding.get('addresses') or []:
            address = entry.get('value') or entry.get('address')
            if address:
                addresses.add(address)
    return addresses


def _in_networks(address, networks):
    """Whether an address lies inside any of the given networks."""
    parsed = ipaddress.ip_address(address)
    return any(parsed in network for network in networks)


def _network_members(role, addresses):
    """Select the local addresses belonging to one Ceph network.

    The ``ceph-<role>-network`` option is the filter when set; otherwise
    the addresses Juju reports for the ``<role>`` binding.

    :param addresses: local ``(interface, address)`` tuples
    :type addresses: list[tuple[str, str]]
    :returns: tuple of (source description, selected addresses)
    :rtype: tuple[str, list[tuple[str, str]]]
    """
    option = 'ceph-{}-network'.format(role)
    configured = _config(option)
    if configured:
        networks = []
        for cidr in configured.split():
            try:
                networks.append(ipaddress.ip_network(cidr, strict=False))
            except ValueError:
                log('Ignoring invalid {} entry {!r}'.format(option, cidr),
                    level=WARNING)
        return option, [(interface, address)
                        for interface, address in addresses
                        if _in_networks(address, networks)]
    bound = _binding_addresses(role)
    if bound is None:
        return 'the host address', []
    return "the '{}' binding".format(role), [
        (interface, address) for interface, address in addresses
        if address in bound]


def _address_version(address):
    """Return the IP version of an address, or None when not an IP."""
    try:
        return ipaddress.ip_address(address).version
    except ValueError:
        return None


def _spread_note(bonds):
    """Explain how bonds spread an interface's traffic over NUMA nodes.

    :param bonds: ``(bond, mode)`` tuples from ``spanning_bonds``
    :rtype: str or None
    """
    notes = []
    for bond, mode in bonds:
        if mode == 'active-backup':
            notes.append(
                '{} is an active-backup bond without a primary leg, so its '
                'traffic uses whichever leg is active; set a primary leg to '
                "make it local to that leg's node".format(bond))
        else:
            notes.append('{} is a bond in {} mode, which uses all its '
                         'legs'.format(bond, mode))
    return '; '.join(notes) or None


def collect_network_facts(root='/'):
    """Discover the local addresses OSDs could bind on each Ceph network.

    Candidates share the address family of the host-wide address, and the
    host-wide address is always a candidate when it is local. Addresses on
    interfaces that are administratively down are never candidates.

    :rtype: dict[str, resource_profiles.NetworkFacts]
    """
    addresses = local_addresses()
    down = local_addresses(up=False)
    localities = {}

    def locality(interface):
        """Return an interface's NUMA nodes and why it spans several."""
        if interface not in localities:
            nodes = tuple(sorted(interface_numa_nodes(interface, root=root)))
            note = None
            if len(nodes) > 1:
                note = _spread_note(spanning_bonds(interface, root=root))
            localities[interface] = (nodes, note)
        return localities[interface]

    facts = {}
    for role in NETWORK_ROLES:
        try:
            host_address = _host_address(role)
        except Exception as exc:
            host_address = None
            log('Cannot determine the Ceph {} address: {}'.format(role, exc),
                level=WARNING)
        version = _address_version(host_address) if host_address else None
        if version is None:
            version = 6 if _config('prefer-ipv6', False) else 4
        source, members = _network_members(role, addresses + down)
        members.extend(
            (interface, address) for interface, address in addresses + down
            if address == host_address)
        candidates = {}
        ignored = {}
        for interface, address in members:
            if _address_version(address) != version:
                continue
            if (interface, address) in down:
                ignored.setdefault(address, interface)
            elif address not in candidates:
                nodes, note = locality(interface)
                candidates[address] = NetworkCandidate(
                    address=address, interface=interface, nodes=nodes,
                    note=note)
        for address, interface in sorted(ignored.items()):
            if address not in candidates:
                log('Ignoring Ceph {} network address {} on {}: the '
                    'interface is administratively down'.format(
                        role, address, interface), level=INFO)
        facts[role] = NetworkFacts(
            role=role, source=source, host_address=host_address,
            candidates=tuple(sorted(
                candidates.values(),
                key=lambda c: ipaddress.ip_address(c.address))))
        log('Ceph {} network candidates from {}: {}; host address {}'.format(
            role, source,
            ', '.join('{} on {}'.format(c.address, c.interface)
                      for c in facts[role].candidates) or 'none',
            host_address), level=DEBUG)
    return facts


def collect_host_facts(root='/'):
    """Discover NUMA topology and Ceph network interface locality.

    :rtype: resource_profiles.HostFacts
    """
    nodes = numa_nodes(root=root)
    networks = collect_network_facts(root=root)
    cluster = networks[NETWORK_CLUSTER].candidates
    interfaces = sorted(set(c.interface for c in cluster))
    return HostFacts(nodes=nodes,
                     nic_interface=','.join(interfaces) or None,
                     nic_nodes=set(n for c in cluster for n in c.nodes),
                     networks=networks)


def _bind_addresses(osd_id, host, numa_node):
    """Choose and log the addresses an OSD binds instead of ceph.conf's.

    :returns: allocation keys (``public-addr``, ``cluster-addr``) mapped to
        an address override, or None to keep the host address, and
        ``address-binding`` to the decisions
    :rtype: dict
    """
    addresses = dict(('{}-addr'.format(role), None) for role in NETWORK_ROLES)
    addresses['address-binding'] = []
    if host is None:
        return addresses
    for choice in choose_addresses(host, numa_node):
        level = (WARNING if choice.outcome in BIND_FALLBACKS or
                 choice.spanning else INFO)
        log('osd.{}: {}'.format(osd_id, choice.message), level=level)
        addresses['{}-addr'.format(choice.role)] = choice.address
        addresses['address-binding'].append(choice.message)
    return addresses


def collect_osd_facts(root='/'):
    """Discover the local OSDs and the devices backing them.

    :rtype: list[resource_profiles.OsdFacts]
    """
    facts = []
    for osd_id, devices in sorted(osd_device_map().items()):
        data = devices['data']
        aux = devices['aux']
        facts.append(OsdFacts(
            osd_id=str(osd_id),
            data_device=data,
            aux_devices=list(aux),
            tier=tier_for_rotational(device_is_rotational(data, root=root)),
            data_node=device_numa_node(data, root=root),
            aux_nodes=dict(
                (device, device_numa_node(device, root=root))
                for device in aux),
        ))
    return facts


def _free_for_service(listing, service):
    """CPUs the allocator could grant to one service right now.

    The service's own claims count as available, because re-requesting
    them overrides its existing claim instead of creating a new one.

    :rtype: set[int]
    """
    free = set(eligible_cpus(listing))
    for name, entry in allocations_by_service(listing).items():
        if name == service:
            continue
        free.difference_update(entry['cores'])
    return free


def _release_stale_claims(client, listing, local_osd_ids, state):
    """Release allocations for OSDs that no longer exist on this host.

    A missing inventory entry is not proof that no daemon runs on the claim,
    so an OSD whose unit is still active keeps it. Enforcement is cleared
    before the claim is released, and the recorded ownership is forgotten
    only once both are gone, so the next reconcile retries a failure.

    :returns: the OSD IDs whose EPA claims were released
    :rtype: list[str]
    """
    claims = allocations_by_service(listing)
    stale = set(state['osds'])
    stale.update(state['pending-transitions'])
    stale.update(resource_apply.pending_restarts())
    stale.update(osd_id_for(service) for service in claims)
    stale.discard(None)
    released = []
    for osd_id in sorted(stale - set(local_osd_ids)):
        service = service_name_for(osd_id)
        if resource_apply.osd_unit_active(osd_id):
            state['warnings'].append(
                'osd.{} is missing from the OSD inventory but still '
                'running; keeping its CPU allocation'.format(osd_id))
            continue
        try:
            resource_apply.clear_allocation(osd_id)
            if service in claims:
                client.release(service)
                released.append(osd_id)
        except (EpaError, resource_apply.ApplyError) as exc:
            state['warnings'].append(
                'cannot release the CPU allocation of removed osd.{}: '
                '{}'.format(osd_id, exc))
            continue
        state['osds'].pop(osd_id, None)
        state['pending-transitions'].pop(osd_id, None)
        _save_state(state)
    return released


def _claim(client, service, mode, numa_node, count):
    """Claim CPUs for one OSD, returning the granted CPU IDs."""
    if mode == MODE_NUMA:
        return client.allocate_numa_cores(service, numa_node, count)
    return client.allocate_cores(service, count)


def _transition_matches(transition, request):
    """Whether a durable transition already owns this request's target."""
    return (transition and transition.get('mode') == request.mode and
            transition.get('numa-node') == request.numa_node and
            transition.get('requested') == request.cores and
            transition.get('status') == 'allocated')


def _new_transition_matches(transition, request):
    """Whether a journaled first claim targets this strict request."""
    return (transition and transition.get('new') and
            transition.get('mode') == request.mode and
            transition.get('numa-node') == request.numa_node and
            transition.get('requested') == request.cores)


def _needs_transition(previous, request, transition=None):
    """Whether changing this request could release CPUs used by an OSD.

    A failed replacement leaves the applied record describing the old
    allocation while the OSD may already run on, and EPA hold, the pending
    target. Returning to the recorded shape must still stop and fully
    release that target: a NUMA claim only replaces CPUs on its own node.
    A transition records its target before stopping, so a stop that was
    never confirmed must be repeated even when that target matches.
    """
    if not previous:
        return False
    if transition and (transition.get('status') == 'stopping' or
                       transition.get('mode') != request.mode or
                       transition.get('numa-node') != request.numa_node or
                       transition.get('requested') != request.cores):
        return True
    count = previous.get('count')
    if count is None:
        count = len(parse_cpu_list(previous.get('cores') or ''))
    return (previous.get('mode') != request.mode or
            previous.get('numa-node') != request.numa_node or
            count != request.cores)


def _claim_may_back_enforcement(previous, transition, osd_id):
    """Whether a claim needs a fresh stop boundary before its release.

    An applied record, a rendered drop-in, or a durable transition beyond a
    known fresh claim can all survive a hook crash after enforcement. A
    missing applied record and ``new`` marker alone are not proof that CPUs
    are unused.
    """
    if previous or resource_apply.read_dropin(osd_id):
        return True
    if not transition:
        return False
    return (transition.get('status') not in ('claiming', 'cleanup') or
            bool(transition.get('source')))


def _remember_transition_source(transition, previous):
    """Keep the ownership being replaced while recording the new target."""
    if transition.get('source'):
        return
    source = previous or transition
    transition['source'] = dict(
        (key, source[key]) for key in
        ('mode', 'numa-node', 'requested', 'cores', 'status', 'new')
        if key in source)


def _stop_before_replacing(client, service, previous, request, state):
    """Make a claim replacement safe by stopping its OSD before release.

    EPA commits each service mutation independently. Record this transition
    before stopping so a hook interruption leaves the OSD fenced rather than
    allowing a still-running process to lose ownership of its CPUs.
    """
    transitions = state.setdefault('pending-transitions', {})
    osd_id = str(request.osd_id)
    transition = transitions.setdefault(osd_id, {})
    _remember_transition_source(transition, previous)
    transition.update({
        'mode': request.mode,
        'numa-node': request.numa_node,
        'requested': request.cores,
        'status': 'stopping',
    })
    _save_state(state)
    # The replacement claim may grant the same CPUs, leaving the drop-in
    # unchanged; the stopped OSD must still be restarted.
    resource_apply.mark_restart_pending(request.osd_id)
    resource_apply.stop_osd(request.osd_id)
    transition['status'] = 'stopped'
    _save_state(state)
    client.release(service)
    transition['status'] = 'replacing'
    _save_state(state)


def _release_for_replacement(client, service, previous, request, state):
    """Release a claim only after the shared live-ownership decision."""
    osd_id = str(request.osd_id)
    transition = state.setdefault('pending-transitions', {}).get(osd_id)
    if _claim_may_back_enforcement(previous, transition, osd_id):
        _stop_before_replacing(client, service, previous, request, state)
        return

    # This is a known never-enforced first claim. Keep its journal until the
    # release succeeds so an interrupted cleanup remains retryable.
    transition = state['pending-transitions'].setdefault(osd_id, {})
    transition.update({
        'mode': request.mode,
        'numa-node': request.numa_node,
        'requested': request.cores,
        'status': 'cleanup',
    })
    _save_state(state)
    client.release(service)
    transition['status'] = 'replacing'
    _save_state(state)


_DEFAULT_CLAIM_VALUE = object()


def _begin_new_claim(request, state, mode=_DEFAULT_CLAIM_VALUE,
                     numa_node=_DEFAULT_CLAIM_VALUE, profile=None):
    """Journal a first claim before EPA can persist it."""
    transitions = state.setdefault('pending-transitions', {})
    transitions[str(request.osd_id)] = {
        'mode': request.mode if mode is _DEFAULT_CLAIM_VALUE else mode,
        'numa-node': (request.numa_node if numa_node is _DEFAULT_CLAIM_VALUE
                      else numa_node),
        'requested': request.cores,
        'status': 'claiming',
        'new': True,
    }
    if profile is not None:
        transitions[str(request.osd_id)]['profile'] = profile
    _save_state(state)


def _journal_claim_attempt(request, state, mode, numa_node, profile):
    """Persist the exact flexible claim shape before mutating EPA."""
    transitions = state.setdefault('pending-transitions', {})
    transition = transitions.get(str(request.osd_id))
    if transition is None:
        _begin_new_claim(request, state, mode, numa_node, profile)
        return
    transition.update({
        'mode': mode,
        'numa-node': numa_node,
        'requested': request.cores,
        'profile': profile,
        'status': 'claiming',
    })
    _save_state(state)


def _cleanup_orphaned_new_claims(client, state):
    """Release only journaled first claims that never reached enforcement."""
    errors = []
    transitions = state.get('pending-transitions') or {}
    for osd_id, transition in list(transitions.items()):
        if not transition.get('new'):
            continue
        if _claim_may_back_enforcement(None, transition, osd_id):
            errors.append(
                'osd.{} has an unfinished first allocation; refusing to '
                'release a claim that may back enforcement'.format(osd_id))
            continue
        transition['status'] = 'cleanup'
        _save_state(state)
        try:
            client.release(service_name_for(osd_id))
        except EpaError as exc:
            errors.append('cannot clean up first claim for osd.{}: {}'.format(
                osd_id, exc))
            continue
        del transitions[osd_id]
        _save_state(state)
    return errors


def _cleanup_new_claim(client, service, osd_id, state):
    """Release only a journaled first-attempt claim that cannot be live."""
    transition = state['pending-transitions'][str(osd_id)]
    if _claim_may_back_enforcement(None, transition, osd_id):
        return 'refusing to release a claim that may back enforcement'
    transition['status'] = 'cleanup'
    _save_state(state)
    try:
        client.release(service)
    except EpaError as exc:
        return str(exc)
    del state['pending-transitions'][str(osd_id)]
    _save_state(state)
    return None


def _replacement_growth(request, claims, nodes):
    """Logical CPUs a request needs beyond what its OSD holds there now.

    :param claims: EPA claims, see :func:`epa_client.allocations_by_service`
    :type claims: dict
    :param nodes: NUMA node to logical CPU mapping
    :type nodes: dict[int, list[int]]
    :rtype: int
    """
    claim = claims.get(service_name_for(request.osd_id)) or {}
    held = set(claim.get('cores') or [])
    if request.mode == MODE_NUMA:
        held &= set(nodes.get(request.numa_node, []))
    return request.cores - len(held)


def _replacement_order(requests, claims, nodes, unfinished=()):
    """Order requests so that replacements releasing CPUs run first.

    Replacements run one OSD at a time, and an OSD's claim is released only
    once it has stopped. A growing OSD handled before a shrinking one can
    therefore find its NUMA node still full, even though the final plan fits.
    Handling every request that needs no more CPUs where it is going first
    leaves room for the growing ones whenever they share a node; the check in
    :func:`_check_replacement_room` covers moves between nodes.

    An unfinished transition or restart can leave its OSD stopped, and its
    released claim makes it look like growth. Such OSDs are recovered before
    any other OSD is stopped, so a retry never takes down a second OSD.

    :param unfinished: IDs of OSDs with a pending transition or restart
    :type unfinished: Iterable[str]
    :rtype: list[OsdRequest]
    """
    unfinished = set(str(osd_id) for osd_id in unfinished)
    return sorted(requests, key=lambda request: (
        str(request.osd_id) not in unfinished,
        _replacement_growth(request, claims, nodes) > 0,
        int(request.osd_id)))


def _check_replacement_room(client, service, request, host=None):
    """Refuse a replacement the allocator cannot grant, before stopping.

    The OSD's own claim counts as free, since it is released before the
    replacement is claimed; claims of every other service, including this
    unit's other OSDs, do not. Without host facts the whole pool counts.

    :raises ResourceError: the target has too few logical CPUs free
    """
    listing = client.list_allocations()
    free = set(eligible_cpus(listing))
    free.difference_update(claimed_by_others(listing, (service,)))
    target = 'the allocator'
    if request.mode == MODE_NUMA and host is not None:
        free &= set(host.nodes.get(request.numa_node, []))
        target = 'NUMA node {}'.format(request.numa_node)
    if len(free) < request.cores:
        raise ResourceError(
            '{} has {} logical CPUs free for this OSD but {} are needed; not '
            'replacing its allocation'.format(
                target, len(free), request.cores))


def _apply_strict(client, requests, previous_osds, state, osds, profile,
                  existing_claims, host=None):
    """Claim and enforce strict allocations one OSD at a time.

    A replacement is stopped, released, claimed and restarted before the next
    OSD is touched. This deliberately permits a failed later OSD to leave an
    earlier OSD truthfully recorded on the new allocation instead of leaving a
    running daemon without an EPA claim. A replacement that cannot fit is
    refused before its OSD is stopped.
    """
    errors = []
    for request in requests:
        osd_id = str(request.osd_id)
        service = service_name_for(request.osd_id)
        previous = previous_osds.get(osd_id)
        migrate_affinity = (
            previous and previous.get('affinity-protection-version') !=
            AFFINITY_PROTECTION_VERSION)
        transition = state.setdefault('pending-transitions', {}).get(osd_id)
        new_claim = False
        try:
            if _needs_transition(previous, request, transition):
                if not _transition_matches(transition, request):
                    _check_replacement_room(client, service, request, host)
                    _release_for_replacement(
                        client, service, previous, request, state)
            elif not previous:
                # Resume a journaled first claim before treating a matching
                # EPA record as untracked. Without a drop-in it cannot back
                # enforced affinity, so discard/recreate it; with one, keep
                # ownership and resume enforcement/verification below.
                if _new_transition_matches(transition, request):
                    if (service in existing_claims and
                            not resource_apply.read_dropin(osd_id)):
                        # An allocated journal without a drop-in is the
                        # crash boundary before enforcement was persisted.
                        # It is not proof that the OSD never ran: preserve
                        # its source and re-prove the stop boundary before
                        # replacing its EPA ownership.
                        _release_for_replacement(
                            client, service, previous, request, state)
                        _journal_claim_attempt(
                            request, state, request.mode, request.numa_node,
                            profile)
                    new_claim = True
                else:
                    # A missing record is not proof an EPA claim is disposable.
                    # Do not overwrite an untracked claim/drop-in that could
                    # still back a running OSD.
                    if (service in existing_claims or
                            resource_apply.read_dropin(osd_id)):
                        raise ResourceError(
                            'untracked existing allocation for osd.{}; '
                            'refusing to replace it'.format(osd_id))
                    _begin_new_claim(request, state)
                    new_claim = True
            cores = _claim(client, service, request.mode, request.numa_node,
                           request.cores)
        except (EpaError, ResourceError, resource_apply.ApplyError) as exc:
            message = 'osd.{}: {}'.format(osd_id, exc)
            if new_claim:
                cleanup_error = _cleanup_new_claim(
                    client, service, osd_id, state)
                if cleanup_error:
                    message += '; cannot clean up first claim: {}'.format(
                        cleanup_error)
            errors.append(message)
            break
        if len(cores) != request.cores:
            message = ('osd.{}: allocator granted {} of {} requested logical '
                       'CPUs'.format(osd_id, len(cores), request.cores))
            if new_claim:
                cleanup_error = _cleanup_new_claim(
                    client, service, osd_id, state)
                if cleanup_error:
                    message += '; cannot clean up first claim: {}'.format(
                        cleanup_error)
            errors.append(message)
            break
        allocation = _allocation(
            request, cores, request.mode, request.numa_node,
            _bind_addresses(osd_id, host, request.numa_node))
        transition = state['pending-transitions'].get(osd_id)
        if transition:
            transition.update({
                'status': 'allocated',
                'cores': format_cpu_list(cores),
                'mode': request.mode,
                'numa-node': request.numa_node,
                'profile': profile,
            })
            _save_state(state)
        try:
            restarted = resource_apply.apply_allocations(
                profile, [allocation],
                force_osd_ids=(osd_id,) if migrate_affinity else (),
                verify_unchanged=True)
        except resource_apply.ApplyError as exc:
            errors.append('osd.{}: {}'.format(osd_id, exc))
            break
        state['osds'][osd_id] = _record(allocation, osds, profile)
        state['restarted'].extend(restarted)
        state['pending-transitions'].pop(osd_id, None)
        _save_state(state)
    return errors


def _reset_claim(client, service, previous, request, state):
    """Safely reset a flexible claim whose placement shape changed."""
    transition = state.get('pending-transitions', {}).get(str(request.osd_id))
    if not _needs_transition(previous, request, transition):
        return
    _release_for_replacement(client, service, previous, request, state)


def _allocate_flexible(client, requests, previous_osds, state, osds,
                       profile, host=None):
    """Claim CPUs for a non-strict profile, degrading as needed.

    The ladder is: the planned NUMA node, then any node, then whatever
    capacity is left, then nothing.  Every downgrade is recorded as a
    deviation.

    :returns: tuple of (allocations, warnings)
    :rtype: tuple[list[dict], list[str]]
    """
    warnings = []
    errors = []
    for request in requests:
        osd_id = str(request.osd_id)
        service = service_name_for(request.osd_id)
        previous = previous_osds.get(osd_id)
        transition = state.setdefault('pending-transitions', {}).get(osd_id)
        try:
            # Re-prove a persisted stop/release boundary before retrying an
            # interrupted replacement. Its source fields are retained until
            # a later allocation is enforced and recorded.
            if transition and transition.get('status') in (
                    'stopping', 'stopped', 'replacing', 'cleanup',
                    'unallocated'):
                _release_for_replacement(
                    client, service, previous, request, state)
                transition = state['pending-transitions'][osd_id]
            # Recover every journaled first flexible claim before accepting a
            # different target. A drop-in may mean this reservation is live;
            # stop and prove it stopped before release in that case.
            if not previous and transition and transition.get('new'):
                if (not _new_transition_matches(transition, request) and
                        transition.get('status') != 'replacing'):
                    _release_for_replacement(
                        client, service, previous, request, state)
                    transition = state['pending-transitions'][osd_id]
                elif resource_apply.read_dropin(osd_id):
                    # Keep a possibly enforced matching first claim and let
                    # the idempotent allocation/verification resume it.
                    pass
            if not previous and transition is None:
                _begin_new_claim(request, state, profile=profile)
                transition = state['pending-transitions'][osd_id]
            _reset_claim(client, service, previous, request, state)
        except (EpaError, resource_apply.ApplyError) as exc:
            errors.append('osd.{}: {}'.format(osd_id, exc))
            break
        deviations = list(request.deviations)
        cores = []
        mode = request.mode
        numa_node = request.numa_node

        if request.mode == MODE_NUMA:
            try:
                cores = _claim(client, service, MODE_NUMA, numa_node,
                               request.cores)
            except EpaRequestError as exc:
                deviations.append(
                    'cannot allocate {} logical CPUs on NUMA node {} '
                    '({}); retrying without NUMA locality'.format(
                        request.cores, numa_node, exc))
                try:
                    _release_for_replacement(
                        client, service, previous, request, state)
                except (EpaError, resource_apply.ApplyError) as release_error:
                    errors.append('osd.{}: {}'.format(osd_id, release_error))
                    break

        if not cores:
            mode, numa_node = MODE_ANY, None
            _journal_claim_attempt(request, state, mode, numa_node, profile)
            try:
                cores = _claim(client, service, MODE_ANY, None, request.cores)
            except EpaRequestError as exc:
                deviations.append(
                    'cannot allocate {} logical CPUs ({}); reducing the '
                    'request'.format(request.cores, exc))

        if not cores:
            available = len(_free_for_service(
                client.list_allocations(), service))
            if available > 0:
                _journal_claim_attempt(request, state, MODE_ANY, None, profile)
                try:
                    cores = _claim(client, service, MODE_ANY, None, available)
                except EpaRequestError as exc:
                    deviations.append(
                        'reduced request of {} logical CPUs failed: '
                        '{}'.format(available, exc))

        if not cores:
            deviations.append(
                'no CPUs available; leaving this OSD unpinned')
            warnings.extend(
                'osd.{}: {}'.format(request.osd_id, d) for d in deviations)
            # Drop stale enforcement so that the OSD is not pinned to
            # CPUs the charm no longer owns.
            try:
                resource_apply.clear_allocation(request.osd_id, restart=True)
            except resource_apply.ApplyError as exc:
                errors.append('osd.{}: {}'.format(osd_id, exc))
                break
            state['osds'].pop(osd_id, None)
            transition = state['pending-transitions'].get(osd_id)
            if transition and transition.get('source'):
                # EPA errors are not a proof that no partial replacement
                # exists. Retain the stopped/released provenance for retry.
                transition['status'] = 'unallocated'
            else:
                state['pending-transitions'].pop(osd_id, None)
            _save_state(state)
            continue

        if len(cores) < request.cores:
            deviations.append(
                'allocated {} of {} requested logical CPUs'.format(
                    len(cores), request.cores))
        warnings.extend(
            'osd.{}: {}'.format(request.osd_id, d) for d in deviations)
        allocation = _allocation(
            request, cores, mode, numa_node,
            _bind_addresses(osd_id, host, numa_node))
        allocation['deviations'] = deviations
        transition = state['pending-transitions'].get(osd_id)
        if transition:
            transition.update({
                'status': 'allocated', 'cores': format_cpu_list(cores),
                'mode': mode, 'numa-node': numa_node, 'profile': profile,
            })
            _save_state(state)
        try:
            force_ids = ()
            if (previous and previous.get('affinity-protection-version') !=
                    AFFINITY_PROTECTION_VERSION):
                force_ids = (osd_id,)
            restarted = resource_apply.apply_allocations(
                profile, [allocation], force_osd_ids=force_ids,
                verify_unchanged=True)
        except resource_apply.ApplyError as exc:
            errors.append('osd.{}: {}'.format(osd_id, exc))
            break
        state['osds'][osd_id] = _record(allocation, osds, profile)
        state['restarted'].extend(restarted)
        state['pending-transitions'].pop(osd_id, None)
        _save_state(state)
    return warnings, errors


def _allocation(request, cores, mode, numa_node, addresses=None):
    """Build the record describing one OSD's granted allocation."""
    policy = request.numa_policy if mode == MODE_NUMA else None
    addresses = addresses or {}
    return {
        'osd-id': request.osd_id,
        'tier': request.tier,
        'mode': mode,
        'numa-node': numa_node,
        'numa-policy': policy,
        'public-addr': addresses.get('public-addr'),
        'cluster-addr': addresses.get('cluster-addr'),
        'address-binding': list(addresses.get('address-binding') or []),
        'requested': request.cores,
        'cores': list(cores),
        'count': len(cores),
        'deviations': list(request.deviations),
    }


def _pool_demand(plan, host, listing, own_services):
    """Per-node CPU demand the charm-owned pool has to cover.

    CPUs held by other owners inside a node still occupy pool capacity,
    so they are added to the charm's own demand. Without this, a pool
    sized to the profile alone would be short exactly by whatever a
    co-located workload already holds.

    :rtype: dict[int, int]
    """
    demand = dict(plan.demand_by_node())
    foreign = claimed_by_others(listing, own_services)
    for node, cpus in host.nodes.items():
        held = len(foreign & set(cpus))
        if held:
            demand[node] = demand.get(node, 0) + held
    return demand


def _activate_epa_pool(cpus, client):
    """Journal pool activation across snap configuration and daemon restart.

    The caller has established ownership and capability support before creating
    this intent. Replay it before probing readiness: the previous restart may
    have left the daemon stopped or still serving its old pool.
    """
    database = kv()
    database.set(PENDING_POOL_KEY, list(cpus))
    database.flush()
    try:
        epa_snap.set_pool(cpus)
        listing = client.wait_ready()
    except (epa_snap.SnapError, EpaError) as exc:
        raise ResourceError(str(exc))
    database.set(PENDING_POOL_KEY, None)
    database.flush()
    return listing


def _ensure_epa(plan, host, own_services, client_factory=EpaClient):
    """Ensure the EPA orchestrator is installed and has capacity.

    :returns: tuple of (client, listing, epa state dict)
    :raises ResourceError: EPA is unusable for the requested profile
    """
    try:
        resource_path = resource_get(RESOURCE_NAME)
    except Exception:
        resource_path = None
    if isinstance(resource_path, str):
        # resource-get reports the path with a trailing newline.
        resource_path = resource_path.strip()
    channel = _config('epa-orchestrator-channel', 'latest/stable')

    try:
        origin = epa_snap.ensure_installed(resource_path, channel)
    except epa_snap.SnapError as exc:
        raise ResourceError(str(exc))
    if origin != 'present':
        _epa_charm_managed(True)
    charm_managed = _epa_charm_managed()

    client = client_factory()
    pending_pool = kv().get(PENDING_POOL_KEY)
    if charm_managed and pending_pool:
        _activate_epa_pool(pending_pool, client)
    try:
        # A freshly installed daemon may still be binding its socket.
        discovery = client.wait_ready(discover=True)
    except EpaError as exc:
        raise ResourceError(str(exc))

    if not client.supports_non_preemptive(discovery):
        raise ResourceError(
            'the installed EPA orchestrator does not support '
            'non-preemptive allocations, which this charm requires')
    if 'cpu-pools' not in (discovery.get('supported_cpu_features') or []):
        raise ResourceError(
            'the installed EPA orchestrator does not support cpu-pools, '
            'which this charm requires')

    configured = epa_snap.get_configured_pool() if charm_managed else None
    listing = {}
    if not charm_managed or (configured and configured.strip() != 'isolated'):
        try:
            listing = client.list_allocations()
        except EpaError as exc:
            raise ResourceError(str(exc))
    if charm_managed:
        desired = epa_snap.compute_pool(
            host.nodes,
            demand_by_node=_pool_demand(plan, host, listing, own_services),
            unbound_demand=plan.demand_unbound(),
            excluded_cpus=parse_cpu_list(
                (discovery.get('cpu_pool') or {}).get('configured_cpus')
                or ''))
        merged, changed = epa_snap.grow_pool(
            configured, desired)
        if changed:
            listing = _activate_epa_pool(merged, client)

    epa_state = {
        'charm-managed': charm_managed,
        'origin': origin,
        'source': pool_source(listing),
        'eligible-cpus': format_cpu_list(eligible_cpus(listing)),
    }
    return client, listing, epa_state


def reconcile(client_factory=EpaClient):
    """Calculate, claim and enforce the profile's CPU allocations.

    :returns: the recorded allocation state
    :rtype: dict
    """
    previous = get_state()
    # ``profile`` and ``osds`` describe the last allocation that reached
    # enforcement.  Keep them until a replacement is fully applied: a
    # refused request must not make an active allocation unverifiable or
    # release its EPA claim on a later retry.
    state = {
        'version': STATE_VERSION,
        'profile': previous.get('profile', PROFILE_UNMANAGED),
        'requested-profile': previous.get('profile', PROFILE_UNMANAGED),
        'osds': dict(previous.get('osds') or {}),
        'pending-transitions': dict(
            previous.get('pending-transitions') or {}),
        'pending-teardowns': dict(
            previous.get('pending-teardowns') or {}),
        'affinity-protection-version': previous.get(
            'affinity-protection-version'),
        'errors': [],
        'warnings': [],
        'restarted': [],
        'epa': dict(previous.get('epa') or {}),
    }
    try:
        profile = configured_profile()
        overrides = configured_overrides()
    except ResourceError as exc:
        state.update({'requested-profile': _config('performance-profile'),
                      'errors': [str(exc)]})
        _save_state(state)
        return state
    state['requested-profile'] = profile

    # Do not allocate or restart an OSD while a removal has stopped it and
    # retained its CPU claim for retry.  In particular, an EPA-only claim can
    # have no applied allocation record, so retaining this marker is the only
    # evidence that removal must still block destructive work.
    if state['pending-teardowns']:
        state['errors'] = [
            'unfinished OSD resource teardown; retry CPU claim cleanup '
            'before reconciling allocations']
        _save_state(state)
        return state

    if not is_managed(profile):
        # Transitioning to unmanaged keeps the current allocations and
        # enforcement in place; only the recorded profile changes.
        log('performance-profile is {}; leaving any existing OSD resource '
            'allocation untouched'.format(profile), level=DEBUG)
        keep = dict(previous)
        applied_profile = previous.get('profile', PROFILE_UNMANAGED)
        if not keep.get('osds'):
            applied_profile = profile
        keep.update({'version': STATE_VERSION, 'profile': applied_profile,
                     'requested-profile': profile, 'errors': [],
                     'warnings': [], 'restarted': []})
        if resource_apply.pending_restarts():
            keep['errors'] = [
                'unfinished resource restarts; recover the managed profile '
                'before switching to unmanaged']
        if keep.get('pending-transitions') and not keep['errors']:
            try:
                cleanup_errors = _cleanup_orphaned_new_claims(
                    client_factory(), keep)
            except EpaError as exc:
                cleanup_errors = [
                    'cannot clean up pending first claims: {}'.format(exc)]
            if cleanup_errors:
                keep['errors'] = cleanup_errors
                _save_state(keep)
                return keep
        # A legacy retained allocation may have been widened before this
        # charm rendered the Ceph NUMA protection. Repair it in the normal
        # serialized turn even though the requested profile is unmanaged.
        if (keep.get('osds') and
                keep.get('affinity-protection-version') !=
                AFFINITY_PROTECTION_VERSION and not keep['errors']):
            try:
                for osd_id, record in sorted(keep['osds'].items()):
                    allocation = dict(record)
                    allocation['osd-id'] = osd_id
                    allocation['cores'] = parse_cpu_list(record['cores'])
                    resource_apply.apply_allocations(
                        record.get('profile', applied_profile), [allocation],
                        force_osd_ids=(osd_id,), verify_unchanged=True)
            except resource_apply.ApplyError as exc:
                keep['errors'] = [str(exc)]
                _save_state(keep)
                return keep
            keep['affinity-protection-version'] = AFFINITY_PROTECTION_VERSION
        _save_state(keep)
        return keep

    try:
        osds = collect_osd_facts()
        host = collect_host_facts()
    except TopologyError as exc:
        state['errors'] = [str(exc)]
        _save_state(state)
        return state

    if not osds:
        log('No local OSDs found; nothing to allocate', level=DEBUG)
        if (state['osds'] or state['pending-transitions'] or
                resource_apply.pending_restarts()):
            # The last OSD went away outside remove-disk. Release what this
            # unit recorded, without installing or resizing EPA for a unit
            # that has nothing left to allocate.
            try:
                client = client_factory()
                _release_stale_claims(
                    client, client.list_allocations(), set(), state)
            except EpaError as exc:
                state['warnings'].append(
                    'cannot release the CPU allocations of removed OSDs: '
                    '{}'.format(exc))
            for warning in state['warnings']:
                log(warning, level=WARNING)
        _save_state(state)
        return state

    _warn_oversized_overrides(profile, overrides, host.nodes)
    plan = build_plan(profile, osds, host, overrides)
    state['warnings'] = list(plan.warnings) + plan.deviations()
    if not plan.satisfiable:
        state['errors'] = list(plan.errors)
        log('Profile {} cannot be satisfied: {}'.format(
            profile, '; '.join(plan.errors)), level=ERROR)
        _save_state(state)
        return state

    local_ids = set(str(osd.osd_id) for osd in osds)
    own_services = set(service_name_for(osd_id) for osd_id in local_ids)

    try:
        client, listing, epa_state = _ensure_epa(
            plan, host, own_services, client_factory)
    except ResourceError as exc:
        state['errors'] = [str(exc)]
        _save_state(state)
        return state
    state['epa'] = epa_state

    if _release_stale_claims(client, listing, local_ids, state):
        # The released CPUs are free again; do not refuse a plan over them.
        try:
            listing = client.list_allocations()
        except EpaError as exc:
            state['errors'] = [str(exc)]
            _save_state(state)
            return state

    free = set(eligible_cpus(listing))
    free.difference_update(claimed_by_others(listing, own_services))
    shortfalls = check_capacity(plan, free, host.nodes)
    if shortfalls and is_strict(profile):
        state['errors'] = [
            'profile {} needs more CPUs than the allocator can grant: '
            '{}'.format(profile, '; '.join(shortfalls))]
        log(state['errors'][0], level=ERROR)
        _save_state(state)
        return state
    if shortfalls:
        state['warnings'].extend(shortfalls)

    previous_osds = previous.get('osds') or {}
    claims = allocations_by_service(listing)
    unfinished = (set(state['pending-transitions']) |
                  resource_apply.pending_restarts())
    requests = _replacement_order(
        plan.requests, claims, host.nodes, unfinished)
    if is_strict(profile):
        errors = _apply_strict(
            client, requests, previous_osds, state, osds, profile, claims,
            host=host)
        state['errors'].extend(errors)
        if state['errors']:
            log('Not enforcing allocations for profile {}: {}'.format(
                profile, '; '.join(state['errors'])), level=ERROR)
            _save_state(state)
            return state
        state['profile'] = profile
        state['affinity-protection-version'] = AFFINITY_PROTECTION_VERSION
    else:
        warnings, errors = _allocate_flexible(
            client, requests, previous_osds, state, osds, profile,
            host=host)
        state['warnings'].extend(warnings)
        state['errors'].extend(errors)
        if state['errors']:
            _save_state(state)
            return state
        state['profile'] = profile
        state['affinity-protection-version'] = AFFINITY_PROTECTION_VERSION

    if state['errors']:
        log('Not enforcing allocations for profile {}: {}'.format(
            profile, '; '.join(state['errors'])), level=ERROR)
        _save_state(state)
        return state

    if not state.get('pending-transitions'):
        state.pop('pending-transitions', None)
    for warning in state['warnings']:
        log(warning, level=WARNING)
    if state['restarted']:
        log('Applied profile {} and restarted OSDs: {}'.format(
            profile, ', '.join(state['restarted'])), level=INFO)
    _save_state(state)
    return state


def _record(allocation, osds, profile):
    """Build the persisted record for one OSD allocation."""
    facts = dict((str(osd.osd_id), osd) for osd in osds)
    osd = facts.get(allocation['osd-id'])
    record = dict(allocation)
    record['profile'] = profile
    record['affinity-protection-version'] = AFFINITY_PROTECTION_VERSION
    record['cores'] = format_cpu_list(allocation['cores'])
    if osd is not None:
        record['data-device'] = osd.data_device
        record['aux-devices'] = list(osd.aux_devices)
    return record


def verify(client_factory=EpaClient):
    """Check applied allocations against actual state, without changes.

    Drift is recorded in the persisted state so that it is reported by
    :func:`assess`; nothing is allocated, written or restarted.

    :returns: the list of drift messages
    :rtype: list[str]
    """
    state = get_state()
    profile = state.get('profile', PROFILE_UNMANAGED)
    osds = state.get('osds') or {}
    if not osds:
        return []

    drift = []
    listing = None
    try:
        listing = client_factory().list_allocations()
    except EpaError as exc:
        drift.append('cannot query the EPA orchestrator: {}'.format(exc))

    claims = allocations_by_service(listing or {})
    for osd_id, record in sorted(osds.items()):
        addresses = resource_apply.allocation_addresses(record)
        expected = resource_apply.render_dropin(
            record.get('profile', profile), record.get('cores'),
            numa_policy=record.get('numa-policy'),
            numa_node=record.get('numa-node'),
            addresses=addresses)
        if resource_apply.read_dropin(osd_id) != expected:
            drift.append(
                'osd.{}: CPU affinity drop-in differs from the applied '
                'allocation'.format(osd_id))
        else:
            try:
                resource_apply.verify_running(
                    osd_id, record.get('cores'), addresses=addresses)
            except resource_apply.ApplyError as exc:
                drift.append(str(exc))
        if listing is None:
            continue
        service = service_name_for(osd_id)
        claim = claims.get(service)
        if claim is None:
            drift.append(
                'osd.{}: the allocator no longer records a CPU '
                'claim'.format(osd_id))
        elif format_cpu_list(claim['cores']) != record.get('cores'):
            drift.append(
                'osd.{}: allocator holds {} but {} was applied'.format(
                    osd_id, format_cpu_list(claim['cores']),
                    record.get('cores')))

    state['drift'] = drift
    _save_state(state)
    return drift


def _teardown_state(state, osd_id, status, error=None):
    """Persist an incomplete teardown before crossing its next boundary."""
    entry = state.setdefault('pending-teardowns', {}).setdefault(
        str(osd_id), {})
    entry['status'] = status
    if error is None:
        entry.pop('error', None)
    else:
        entry['error'] = str(error)
    _save_state(state)


def _complete_teardown(state, osd_id):
    """Forget ownership records only after enforcement and EPA are clear."""
    osd_id = str(osd_id)
    state.setdefault('osds', {}).pop(osd_id, None)
    state.setdefault('pending-transitions', {}).pop(osd_id, None)
    state.setdefault('pending-teardowns', {}).pop(osd_id, None)
    if not state.get('pending-transitions'):
        state.pop('pending-transitions', None)
    if not state.get('pending-teardowns'):
        state.pop('pending-teardowns', None)
    _save_state(state)


def _recorded_teardown_ownership(state, osd_id):
    """Whether durable state says this OSD could still own a CPU claim."""
    osd_id = str(osd_id)
    return (osd_id in (state.get('osds') or {}) or
            osd_id in (state.get('pending-transitions') or {}) or
            osd_id in resource_apply.pending_restarts() or
            resource_apply.read_dropin(osd_id) is not None or
            osd_id in (state.get('pending-teardowns') or {}))


def release_osd(osd_id, client_factory=EpaClient, client=None,
                claim_known=False):
    """Stop, unpin and release one OSD's known CPU ownership.

    A record, pending transition, drop-in or allocator claim is sufficient to
    require a fresh stop proof.  A completely unknown OSD remains a no-op so
    removal of unmanaged OSDs does not become dependent on EPA availability.

    :returns: True when no ownership remains, None when EPA cannot identify
        an otherwise unmanaged OSD
    :raises ResourceError: a known ownership could not be safely released
    """
    osd_id = str(osd_id)
    if osd_id.startswith('osd.'):
        osd_id = osd_id[len('osd.'):]
    service = service_name_for(osd_id)
    state = get_state()
    known = claim_known or _recorded_teardown_ownership(state, osd_id)
    if client is None:
        client = client_factory()
    if not known:
        try:
            known = service in allocations_by_service(
                client.list_allocations())
        except EpaError as exc:
            log('Cannot check CPU claim for unmanaged osd.{}: {}'.format(
                osd_id, exc), level=WARNING)
            return None
    if not known:
        return True

    _teardown_state(state, osd_id, 'stopping')
    try:
        resource_apply.stop_osd(osd_id)
    except resource_apply.ApplyError as exc:
        _teardown_state(state, osd_id, 'stop-failed', exc)
        raise ResourceError(
            'osd.{} did not stop; CPU claim retained: {}'.format(
                osd_id, exc))
    _teardown_state(state, osd_id, 'stopped')
    try:
        # Do not restart a deliberately stopped OSD.  Releasing EPA first
        # would let a later restart run with a stale affinity drop-in.
        resource_apply.clear_allocation(osd_id)
    except resource_apply.ApplyError as exc:
        _teardown_state(state, osd_id, 'clear-failed', exc)
        raise ResourceError(
            'cannot clear CPU enforcement for osd.{}: {}'.format(
                osd_id, exc))
    _teardown_state(state, osd_id, 'cleared')
    try:
        client.release(service)
    except EpaError as exc:
        _teardown_state(state, osd_id, 'release-failed', exc)
        raise ResourceError('cannot release CPU claim for osd.{}: {}'.format(
            osd_id, exc))
    _complete_teardown(state, osd_id)
    return True


def release_all(client_factory=EpaClient):
    """Safely release every recorded or EPA-owned claim one OSD at a time."""
    state = get_state()
    osd_ids = set(state.get('osds') or {})
    osd_ids.update(state.get('pending-transitions') or {})
    osd_ids.update(state.get('pending-teardowns') or {})
    osd_ids.update(resource_apply.pending_restarts())
    try:
        client = client_factory()
        claims = allocations_by_service(client.list_allocations())
    except EpaError as exc:
        client = None
        claims = {}
        if osd_ids:
            log('Cannot list CPU claims during teardown: {}'.format(exc),
                level=WARNING)
    for service in claims:
        osd_id = osd_id_for(service)
        if osd_id is not None:
            osd_ids.add(osd_id)

    errors = []
    for osd_id in sorted(osd_ids):
        try:
            release_osd(osd_id, client_factory=client_factory, client=client,
                        claim_known=service_name_for(osd_id) in claims)
        except (EpaError, ResourceError) as exc:
            errors.append(str(exc))
    if errors:
        raise ResourceError('; '.join(errors))
    return True


def assess():
    """Summarise the allocation state for the unit's workload status.

    :returns: a ResourceStatus, or None when there is nothing to report
    :rtype: ResourceStatus or None
    """
    try:
        state = get_state()
    except Exception as exc:
        log('Cannot read resource allocation state: {}'.format(exc),
            level=WARNING)
        return None
    rollout = state.get('rollout') or {}
    if rollout.get('status') == 'failed':
        return ResourceStatus(True, rollout['message'])
    profile = state.get('profile', PROFILE_UNMANAGED)
    errors = list(state.get('errors') or []) + list(state.get('drift') or [])
    if errors:
        return ResourceStatus(True, 'performance-profile {}: {}'.format(
            profile, errors[0]))
    if rollout.get('status') == 'waiting':
        return ResourceStatus(False, rollout['message'], True)
    if not is_managed(profile):
        return None
    warnings = state.get('warnings') or []
    if warnings and not warnings_suppressed():
        return ResourceStatus(False, 'profile {} applied with {} '
                              'deviation(s), see logs'.format(
                                  profile, len(warnings)))
    if state.get('osds'):
        return ResourceStatus(False, 'profile {} applied'.format(profile))
    return None


def status_report(dry_run=None, client_factory=EpaClient):
    """Build a read-only report of the current or previewed allocation.

    :param dry_run: profile to preview instead of reporting the applied
        state; nothing is claimed, written or restarted
    :type dry_run: str or None
    :rtype: dict
    """
    state = get_state()
    report = {
        'profile': state.get('profile', PROFILE_UNMANAGED),
        'requested-profile': state.get(
            'requested-profile', state.get('profile', PROFILE_UNMANAGED)),
        'epa': dict(state.get('epa') or {}),
        'warnings': list(state.get('warnings') or []),
        'errors': list(state.get('errors') or []),
        'drift': list(state.get('drift') or []),
        'rollout': dict(state.get('rollout') or {}),
        'osds': [],
    }
    if dry_run:
        report['dry-run'] = dry_run

    try:
        listing = client_factory().list_allocations()
    except EpaError as exc:
        listing = {}
        report['epa']['error'] = str(exc)
    if listing:
        report['epa'].update({
            'source': pool_source(listing),
            'eligible-cpus': format_cpu_list(eligible_cpus(listing)),
            'supported-features': listing.get('supported_cpu_features') or [],
        })
    claims = allocations_by_service(listing or {})

    try:
        osds = collect_osd_facts()
        host = collect_host_facts()
    except TopologyError as exc:
        report['errors'].append(str(exc))
        return report

    report['numa-nodes'] = dict(
        (str(node), format_cpu_list(cpus))
        for node, cpus in sorted(host.nodes.items()))
    report['nic'] = {
        'interface': host.nic_interface,
        'numa-nodes': sorted(host.nic_nodes),
    }
    report['networks'] = _network_report(host)

    profile = dry_run or report['requested-profile']
    plan = None
    try:
        overrides = configured_overrides()
    except ResourceError as exc:
        report['errors'].append(str(exc))
        overrides = None
    else:
        report['resource-overrides'] = overrides
    if is_managed(profile) and overrides is not None:
        plan = build_plan(profile, osds, host, overrides)
        if dry_run:
            report['errors'] = list(plan.errors)
            report['warnings'] = plan.deviations()
            free = set(eligible_cpus(listing or {}))
            own = set(service_name_for(o.osd_id) for o in osds)
            for name, entry in claims.items():
                if name not in own:
                    free.difference_update(entry['cores'])
            report['shortfalls'] = check_capacity(plan, free, host.nodes)
    planned = dict((r.osd_id, r) for r in (plan.requests if plan else []))
    applied = state.get('osds') or {}

    for osd in sorted(osds, key=lambda o: int(o.osd_id)):
        request = planned.get(osd.osd_id)
        record = applied.get(osd.osd_id) or {}
        claim = claims.get(service_name_for(osd.osd_id)) or {}
        choices = choose_addresses(host, request.numa_node) if request else []
        desired = dict((c.role, c.address) for c in choices)
        report['osds'].append({
            'osd-id': osd.osd_id,
            'data-device': osd.data_device,
            'aux-devices': list(osd.aux_devices),
            'tier': osd.tier,
            'device-numa-node': osd.data_node,
            'desired-cores': request.cores if request else None,
            'desired-mode': request.mode if request else None,
            'desired-numa-node': request.numa_node if request else None,
            'applied-cores': record.get('cores'),
            'applied-mode': record.get('mode'),
            'applied-numa-node': record.get('numa-node'),
            'applied-numa-policy': record.get('numa-policy'),
            'desired-public-addr': desired.get(NETWORK_PUBLIC),
            'desired-cluster-addr': desired.get(NETWORK_CLUSTER),
            'applied-public-addr': record.get('public-addr'),
            'applied-cluster-addr': record.get('cluster-addr'),
            # Decisions for the planned node, and those made when the
            # allocation was applied, which differ after a fallback.
            'desired-address-binding': [c.message for c in choices],
            'applied-address-binding': record.get('address-binding'),
            'allocator-cores': format_cpu_list(claim.get('cores') or []),
            'allocator-policy': claim.get('preemption_policy'),
            'deviations': (list(request.deviations) if request
                           else list(record.get('deviations') or [])),
        })
    return report


def _network_report(host):
    """Render the discovered Ceph network candidates as plain data."""
    report = {}
    for role, network in sorted((host.networks or {}).items()):
        report[role] = {
            'source': network.source,
            'host-address': network.host_address,
            'candidates': [
                {'address': c.address, 'interface': c.interface,
                 'numa-nodes': list(c.nodes)}
                for c in network.candidates],
        }
    return report


def _rollout_inputs():
    """Identify inventory/topology changes as well as configuration changes."""
    if _config('performance-profile', PROFILE_UNMANAGED) == PROFILE_UNMANAGED:
        return {}
    try:
        host = collect_host_facts()
        return {'osds': [o._asdict() for o in collect_osd_facts()],
                'nodes': host.nodes, 'interface': host.nic_interface,
                'nic-nodes': sorted(host.nic_nodes),
                'networks': _network_report(host)}
    except TopologyError as exc:
        # Publish a request even when discovery fails, so an unhealthy unit
        # cannot silently disappear from the cohort. Reconcile records it.
        return {'discovery-error': str(exc)}


def _ready_for_handoff():
    """Verify no-op/unmanaged participants before admitting a successor."""
    if resource_apply.pending_restarts():
        raise ResourceError('unfinished OSD resource restarts')
    for osd in collect_osd_facts():
        if not resource_apply.wait_for_osd(osd.osd_id):
            raise ResourceError(
                'osd.{} is not active; stopping resource rollout'.format(
                    osd.osd_id))


def _rollout_status(status, message=''):
    state = get_state()
    state['rollout'] = {'status': status, 'message': message}
    _save_state(state)
    return state


def _retained_affinity_migration_pending():
    """Whether an unmanaged request still needs a serialized restart."""
    state = get_state()
    return (bool(state.get('osds')) and
            state.get('affinity-protection-version') !=
            AFFINITY_PROTECTION_VERSION)


def safe_reconcile(client_factory=EpaClient):
    """Reconcile, converting unexpected failures into a blocked state.

    Resource allocation must never abort an unrelated hook, so any
    unexpected exception is recorded and reported through the unit
    status instead of propagating.
    """
    try:
        resource_rollout.run(
            _rollout_inputs(),
            lambda: reconcile(client_factory=client_factory),
            unmanaged=(_config('performance-profile', PROFILE_UNMANAGED) ==
                       PROFILE_UNMANAGED and
                       not _retained_affinity_migration_pending()),
            check_ready=_ready_for_handoff)
        return _rollout_status('complete')
    except resource_rollout.RollingWait as exc:
        return _rollout_status('waiting', str(exc))
    except resource_rollout.RollingError as exc:
        return _rollout_status('failed', str(exc))
    except Exception as exc:
        log('Resource allocation failed: {}\n{}'.format(
            exc, traceback.format_exc()), level=ERROR)
        state = get_state()
        message = 'resource allocation failed: {}'.format(exc)
        state['errors'] = [message]
        state['rollout'] = {'status': 'failed', 'message': message}
        try:
            _save_state(state)
        except Exception:
            pass
        return state


def safe_verify(client_factory=EpaClient):
    """Verify applied allocations, swallowing unexpected failures."""
    try:
        drift = verify(client_factory=client_factory)
        try:
            resource_rollout.observe()
        except resource_rollout.RollingWait as exc:
            _rollout_status('waiting', str(exc))
        except resource_rollout.RollingError as exc:
            _rollout_status('failed', str(exc))
        return drift
    except Exception as exc:
        log('Resource allocation verification failed: {}'.format(exc),
            level=WARNING)
        return []


def safe_release_all(client_factory=EpaClient):
    """Release every claim and return whether all known claims are clear."""
    try:
        return release_all(client_factory=client_factory)
    except Exception as exc:
        log('Releasing CPU claims failed: {}'.format(exc), level=WARNING)
        return False


def safe_release_osd(osd_id, client_factory=EpaClient):
    """Release one claim without hiding a failure from destructive callers."""
    try:
        return release_osd(osd_id, client_factory=client_factory)
    except Exception as exc:
        log('Releasing the CPU claim for {} failed: {}'.format(osd_id, exc),
            level=WARNING)
        return False
