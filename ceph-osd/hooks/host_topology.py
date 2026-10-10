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

"""Host topology discovery for OSD resource allocation.

All discovery is read-only and based on sysfs, so that it can be
exercised in unit tests against a fixture tree by passing ``root``.

Counts and identifiers used here are always *logical* CPUs (i.e. the
schedulable CPU IDs the kernel exposes), never physical cores.
"""

import glob
import json
import os
import subprocess

CPU_ROOT = 'sys/devices/system/cpu'
NODE_ROOT = 'sys/devices/system/node'
BLOCK_ROOT = 'sys/class/block'
NET_ROOT = 'sys/class/net'

# ceph-volume reports the logical volume type for every device backing an
# OSD.  Only the 'block' device carries the OSD data.
DATA_DEVICE_TYPE = 'block'


class TopologyError(Exception):
    """Raised when required host topology cannot be determined."""


def parse_cpu_list(value):
    """Parse a kernel/sysfs style CPU list into sorted logical CPU IDs.

    :param value: CPU list such as ``0-3,8``.
    :type value: str
    :returns: sorted list of logical CPU IDs
    :rtype: list[int]
    """
    cpus = set()
    if not value:
        return []
    for part in value.strip().split(','):
        part = part.strip()
        if not part:
            continue
        if '-' in part:
            start, _, end = part.partition('-')
            start, end = int(start), int(end)
            if end < start:
                raise ValueError('invalid CPU range: {}'.format(part))
            cpus.update(range(start, end + 1))
        else:
            cpus.add(int(part))
    return sorted(cpus)


def format_cpu_list(cpus):
    """Render logical CPU IDs as a compact range string.

    :param cpus: logical CPU IDs
    :type cpus: iterable[int]
    :returns: compact range string such as ``0-3,8``
    :rtype: str
    """
    cpus = sorted(set(cpus))
    if not cpus:
        return ''
    parts = []
    start = prev = cpus[0]
    for cpu in cpus[1:]:
        if cpu == prev + 1:
            prev = cpu
            continue
        parts.append((start, prev))
        start = prev = cpu
    parts.append((start, prev))
    return ','.join(
        str(lo) if lo == hi else '{}-{}'.format(lo, hi) for lo, hi in parts
    )


def _read(path):
    """Read and strip a sysfs file, returning None when unavailable."""
    try:
        with open(path, 'r') as handle:
            return handle.read().strip()
    except (IOError, OSError):
        return None


def online_cpus(root='/'):
    """Return the set of online logical CPU IDs.

    :rtype: set[int]
    """
    value = _read(os.path.join(root, CPU_ROOT, 'online'))
    if value is None:
        raise TopologyError('cannot determine online CPUs')
    return set(parse_cpu_list(value))


def numa_nodes(root='/'):
    """Map NUMA node IDs to their online logical CPU IDs.

    Hosts without NUMA information are reported as a single node 0 that
    owns every online CPU.

    :rtype: dict[int, list[int]]
    """
    online = online_cpus(root=root)
    nodes = {}
    pattern = os.path.join(root, NODE_ROOT, 'node[0-9]*')
    for node_dir in sorted(glob.glob(pattern)):
        try:
            node_id = int(os.path.basename(node_dir)[len('node'):])
        except ValueError:
            continue
        cpus = parse_cpu_list(_read(os.path.join(node_dir, 'cpulist')) or '')
        nodes[node_id] = sorted(set(cpus) & online)
    if not nodes:
        nodes = {0: sorted(online)}
    return nodes


def thread_siblings(cpu, root='/'):
    """Return the SMT sibling group of a logical CPU, including itself.

    :rtype: list[int]
    """
    path = os.path.join(
        root, CPU_ROOT, 'cpu{}'.format(cpu),
        'topology', 'thread_siblings_list')
    value = _read(path)
    if not value:
        return [cpu]
    siblings = parse_cpu_list(value.replace(' ', ','))
    return siblings or [cpu]


def physical_cores(cpus, root='/'):
    """Group logical CPUs into SMT sibling groups.

    Groups are ordered by their lowest logical CPU ID, and only contain
    CPUs from the supplied set.

    :param cpus: candidate logical CPU IDs
    :type cpus: iterable[int]
    :rtype: list[list[int]]
    """
    remaining = set(cpus)
    groups = []
    for cpu in sorted(remaining):
        if cpu not in remaining:
            continue
        group = sorted(set(thread_siblings(cpu, root=root)) & remaining)
        if not group:
            group = [cpu]
        remaining.difference_update(group)
        groups.append(group)
    return groups


def _block_dir(device, root='/'):
    """Return the sysfs directory for a block device path."""
    name = os.path.basename(os.path.realpath(device))
    path = os.path.join(root, BLOCK_ROOT, name)
    if not os.path.exists(path):
        return None
    # Partitions carry neither the queue nor the parent PCI link.
    if os.path.exists(os.path.join(path, 'partition')):
        parent = os.path.dirname(os.path.realpath(path))
        if os.path.exists(os.path.join(parent, 'queue')):
            return parent
    return path


def device_is_rotational(device, root='/'):
    """Report whether a block device is rotational.

    :returns: True for rotational (HDD) devices, False for
        non-rotational devices, None when it cannot be determined.
    :rtype: bool or None
    """
    block = _block_dir(device, root=root)
    if block is None:
        return None
    value = _read(os.path.join(block, 'queue', 'rotational'))
    if value is None:
        return None
    return value.strip() == '1'


def _numa_node_from_sysfs_path(start, root='/'):
    """Walk up a sysfs path looking for a usable ``numa_node`` attribute."""
    boundary = os.path.realpath(os.path.join(root, 'sys'))
    current = os.path.realpath(start)
    while current.startswith(boundary) and current != boundary:
        value = _read(os.path.join(current, 'numa_node'))
        if value is not None:
            try:
                node = int(value)
            except ValueError:
                node = -1
            if node >= 0:
                return node
        current = os.path.dirname(current)
    return None


def device_numa_node(device, root='/'):
    """Return the NUMA node backing a block device.

    :returns: NUMA node ID, or None when unknown (for example virtio
        devices, or hosts that do not expose NUMA affinity).
    :rtype: int or None
    """
    block = _block_dir(device, root=root)
    if block is None:
        return None
    slaves = sorted(glob.glob(os.path.join(block, 'slaves', '*')))
    if slaves:
        # Device-mapper stack: use the first underlying device that
        # exposes a NUMA node.
        for slave in slaves:
            node = _numa_node_from_sysfs_path(slave, root=root)
            if node is not None:
                return node
    return _numa_node_from_sysfs_path(block, root=root)


def _net_leaf_interfaces(interface, root='/', _seen=None):
    """Resolve an interface to its physical member interfaces.

    Bonds, bridges and VLANs are expanded through their ``lower_*``
    links; anything with no lower links is treated as a leaf.

    :rtype: list[str]
    """
    if _seen is None:
        _seen = set()
    if interface in _seen:
        return []
    _seen.add(interface)
    path = os.path.join(root, NET_ROOT, interface)
    lowers = sorted(glob.glob(os.path.join(path, 'lower_*')))
    if not lowers:
        return [interface]
    leaves = []
    for lower in lowers:
        name = os.path.basename(lower)[len('lower_'):]
        leaves.extend(_net_leaf_interfaces(name, root=root, _seen=_seen))
    return leaves


def interface_numa_nodes(interface, root='/'):
    """Return the NUMA nodes of every physical member of an interface.

    :returns: set of NUMA node IDs; empty when unknown (virtual
        interfaces, containers, or hosts without NUMA affinity).
    :rtype: set[int]
    """
    nodes = set()
    for leaf in _net_leaf_interfaces(interface, root=root):
        device = os.path.join(root, NET_ROOT, leaf, 'device')
        if not os.path.exists(device):
            continue
        node = _numa_node_from_sysfs_path(device, root=root)
        if node is not None:
            nodes.add(node)
    return nodes


def interface_for_address(address):
    """Return the interface that holds an IP address.

    :param address: IPv4 or IPv6 address
    :type address: str
    :rtype: str or None
    """
    if not address:
        return None
    try:
        output = subprocess.check_output(
            ['ip', '-json', 'addr', 'show'], stderr=subprocess.DEVNULL)
        links = json.loads(output.decode('UTF-8'))
    except (subprocess.CalledProcessError, OSError, ValueError):
        return None
    for link in links:
        for info in link.get('addr_info', []):
            if info.get('local') == address:
                return link.get('ifname')
    return None


def osd_device_map():
    """Map local OSD IDs to the devices backing them.

    :returns: mapping of OSD ID (as string) to a dict with the ``data``
        device path and a sorted list of ``aux`` (db/wal) device paths.
    :rtype: dict[str, dict]
    """
    try:
        output = subprocess.check_output(
            ['ceph-volume', 'lvm', 'list', '--format=json'],
            stderr=subprocess.DEVNULL)
        listing = json.loads(output.decode('UTF-8'))
    except (subprocess.CalledProcessError, OSError, ValueError) as exc:
        raise TopologyError(
            'cannot enumerate OSD devices: {}'.format(exc))

    devices = {}
    for osd_id, entries in listing.items():
        data = None
        aux = set()
        for entry in entries:
            paths = [p for p in entry.get('devices', []) if p]
            if entry.get('type') == DATA_DEVICE_TYPE:
                if paths:
                    data = paths[0]
                    aux.update(paths[1:])
            else:
                aux.update(paths)
        if data is None:
            continue
        devices[str(osd_id)] = {
            'data': data,
            'aux': sorted(aux - {data}),
        }
    return devices
