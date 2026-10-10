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

import json
import os
import shutil
import subprocess
import tempfile
import unittest

from unittest.mock import patch

import host_topology


def write(path, content):
    """Create a file and its parents inside the fixture tree."""
    directory = os.path.dirname(path)
    if not os.path.isdir(directory):
        os.makedirs(directory)
    with open(path, 'w') as handle:
        handle.write(content)


class CpuListTestCase(unittest.TestCase):

    def test_parse_cpu_list(self):
        self.assertEqual(host_topology.parse_cpu_list('0-3,8'),
                         [0, 1, 2, 3, 8])
        self.assertEqual(host_topology.parse_cpu_list(' 5 , 4-4 '), [4, 5])
        self.assertEqual(host_topology.parse_cpu_list(''), [])
        self.assertEqual(host_topology.parse_cpu_list(None), [])

    def test_parse_cpu_list_rejects_descending_range(self):
        self.assertRaises(ValueError, host_topology.parse_cpu_list, '5-2')

    def test_format_cpu_list(self):
        self.assertEqual(host_topology.format_cpu_list([3, 1, 0, 2, 8]),
                         '0-3,8')
        self.assertEqual(host_topology.format_cpu_list([7]), '7')
        self.assertEqual(host_topology.format_cpu_list([]), '')

    def test_round_trip(self):
        for value in ('0-3,8', '2,4,6', '0-63'):
            self.assertEqual(
                host_topology.format_cpu_list(
                    host_topology.parse_cpu_list(value)),
                value)


class SysfsTestCase(unittest.TestCase):
    """Tests driven by a synthetic sysfs tree."""

    def setUp(self):
        super(SysfsTestCase, self).setUp()
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root)

    def path(self, *parts):
        return os.path.join(self.root, *parts)

    def add_cpus(self, online, nodes, siblings=None):
        """Populate CPU topology and NUMA node CPU lists."""
        write(self.path('sys/devices/system/cpu/online'), online + '\n')
        for node, cpulist in nodes.items():
            write(
                self.path('sys/devices/system/node/node{}/cpulist'.format(
                    node)),
                cpulist + '\n')
        for cpu, sibling_list in (siblings or {}).items():
            write(
                self.path(
                    'sys/devices/system/cpu/cpu{}/topology/'
                    'thread_siblings_list'.format(cpu)),
                sibling_list + '\n')

    def add_disk(self, name, rotational, numa_node=None, pci='0000:00:1f.2'):
        """Add a block device backed by a PCI device."""
        device_dir = self.path('sys/devices/pci0000:00', pci)
        block_dir = os.path.join(device_dir, 'block', name)
        write(os.path.join(block_dir, 'queue', 'rotational'),
              '{}\n'.format(1 if rotational else 0))
        if numa_node is not None:
            write(os.path.join(device_dir, 'numa_node'),
                  '{}\n'.format(numa_node))
        link = self.path('sys/class/block', name)
        if not os.path.isdir(os.path.dirname(link)):
            os.makedirs(os.path.dirname(link))
        os.symlink(block_dir, link)
        return block_dir

    def add_nic(self, name, numa_node=None, pci='0000:00:02.0', lowers=None):
        """Add a network interface, optionally a bond over other NICs."""
        net_dir = self.path('sys/class/net', name)
        if not os.path.isdir(net_dir):
            os.makedirs(net_dir)
        for lower in lowers or []:
            write(os.path.join(net_dir, 'lower_{}'.format(lower)), '')
        if lowers:
            return
        device_dir = self.path('sys/devices/pci0000:00', pci)
        if numa_node is not None:
            write(os.path.join(device_dir, 'numa_node'),
                  '{}\n'.format(numa_node))
        else:
            write(os.path.join(device_dir, 'uevent'), '')
        os.symlink(device_dir, os.path.join(net_dir, 'device'))

    def test_online_cpus(self):
        self.add_cpus('0-3', {0: '0-3'})
        self.assertEqual(host_topology.online_cpus(root=self.root),
                         set([0, 1, 2, 3]))

    def test_online_cpus_missing(self):
        self.assertRaises(host_topology.TopologyError,
                          host_topology.online_cpus, root=self.root)

    def test_numa_nodes(self):
        self.add_cpus('0-7', {0: '0-3', 1: '4-7'})
        self.assertEqual(host_topology.numa_nodes(root=self.root),
                         {0: [0, 1, 2, 3], 1: [4, 5, 6, 7]})

    def test_numa_nodes_ignores_offline_cpus(self):
        self.add_cpus('0-2', {0: '0-3'})
        self.assertEqual(host_topology.numa_nodes(root=self.root),
                         {0: [0, 1, 2]})

    def test_numa_nodes_without_node_information(self):
        write(self.path('sys/devices/system/cpu/online'), '0-1\n')
        self.assertEqual(host_topology.numa_nodes(root=self.root),
                         {0: [0, 1]})

    def test_physical_cores_groups_smt_siblings(self):
        self.add_cpus('0-3', {0: '0-3'},
                      siblings={0: '0,2', 1: '1,3', 2: '0,2', 3: '1,3'})
        self.assertEqual(
            host_topology.physical_cores([0, 1, 2, 3], root=self.root),
            [[0, 2], [1, 3]])

    def test_physical_cores_without_topology(self):
        self.assertEqual(host_topology.physical_cores([1, 0], root=self.root),
                         [[0], [1]])

    def test_physical_cores_restricted_to_candidates(self):
        self.add_cpus('0-3', {0: '0-3'},
                      siblings={0: '0,2', 1: '1,3', 2: '0,2', 3: '1,3'})
        self.assertEqual(
            host_topology.physical_cores([0, 1], root=self.root),
            [[0], [1]])

    def test_device_is_rotational(self):
        self.add_disk('sda', rotational=True)
        self.add_disk('nvme0n1', rotational=False, pci='0000:00:1f.3')
        self.assertTrue(
            host_topology.device_is_rotational('/dev/sda', root=self.root))
        self.assertFalse(
            host_topology.device_is_rotational(
                '/dev/nvme0n1', root=self.root))

    def test_device_is_rotational_unknown(self):
        self.assertIsNone(
            host_topology.device_is_rotational('/dev/sdz', root=self.root))

    def test_device_numa_node(self):
        self.add_disk('nvme0n1', rotational=False, numa_node=1)
        self.assertEqual(
            host_topology.device_numa_node('/dev/nvme0n1', root=self.root), 1)

    def test_device_numa_node_unknown_when_negative(self):
        self.add_disk('vda', rotational=True, numa_node=-1)
        self.assertIsNone(
            host_topology.device_numa_node('/dev/vda', root=self.root))

    def test_device_numa_node_missing_device(self):
        self.assertIsNone(
            host_topology.device_numa_node('/dev/sdz', root=self.root))

    def test_device_numa_node_through_partition(self):
        block_dir = self.add_disk('sdb', rotational=True, numa_node=0)
        partition = os.path.join(block_dir, 'sdb1')
        write(os.path.join(partition, 'partition'), '1\n')
        os.symlink(partition, self.path('sys/class/block/sdb1'))
        self.assertEqual(
            host_topology.device_numa_node('/dev/sdb1', root=self.root), 0)
        self.assertTrue(
            host_topology.device_is_rotational('/dev/sdb1', root=self.root))

    def test_device_numa_node_through_device_mapper(self):
        self.add_disk('nvme1n1', rotational=False, numa_node=1,
                      pci='0000:00:1f.4')
        dm_dir = self.path('sys/class/block/dm-0')
        os.makedirs(os.path.join(dm_dir, 'slaves'))
        os.symlink(self.path('sys/class/block/nvme1n1'),
                   os.path.join(dm_dir, 'slaves', 'nvme1n1'))
        self.assertEqual(
            host_topology.device_numa_node('/dev/dm-0', root=self.root), 1)

    def test_interface_numa_nodes(self):
        self.add_nic('eth0', numa_node=1)
        self.assertEqual(
            host_topology.interface_numa_nodes('eth0', root=self.root),
            set([1]))

    def test_interface_numa_nodes_bond(self):
        self.add_nic('eth0', numa_node=0, pci='0000:00:02.0')
        self.add_nic('eth1', numa_node=1, pci='0000:00:03.0')
        self.add_nic('bond0', lowers=['eth0', 'eth1'])
        self.assertEqual(
            host_topology.interface_numa_nodes('bond0', root=self.root),
            set([0, 1]))

    def test_interface_numa_nodes_unknown(self):
        self.add_nic('eth0', numa_node=None)
        self.assertEqual(
            host_topology.interface_numa_nodes('eth0', root=self.root),
            set())

    def test_interface_numa_nodes_virtual(self):
        os.makedirs(self.path('sys/class/net/lo'))
        self.assertEqual(
            host_topology.interface_numa_nodes('lo', root=self.root), set())


class InterfaceLookupTestCase(unittest.TestCase):

    @patch.object(host_topology.subprocess, 'check_output')
    def test_interface_for_address(self, check_output):
        check_output.return_value = json.dumps([
            {'ifname': 'lo', 'addr_info': [{'local': '127.0.0.1'}]},
            {'ifname': 'eth0', 'addr_info': [{'local': '10.0.0.5'}]},
        ]).encode('UTF-8')
        self.assertEqual(
            host_topology.interface_for_address('10.0.0.5'), 'eth0')
        self.assertIsNone(host_topology.interface_for_address('10.0.0.6'))

    def test_interface_for_address_without_address(self):
        self.assertIsNone(host_topology.interface_for_address(None))

    @patch.object(host_topology.subprocess, 'check_output')
    def test_interface_for_address_command_failure(self, check_output):
        check_output.side_effect = OSError('no ip command')
        self.assertIsNone(host_topology.interface_for_address('10.0.0.5'))


class OsdDeviceMapTestCase(unittest.TestCase):

    LISTING = {
        '0': [
            {'type': 'block', 'devices': ['/dev/nvme0n1']},
            {'type': 'db', 'devices': ['/dev/nvme1n1']},
        ],
        '1': [
            {'type': 'block', 'devices': ['/dev/sdb']},
        ],
        '2': [
            {'type': 'db', 'devices': ['/dev/sdc']},
        ],
    }

    @patch.object(host_topology.subprocess, 'check_output')
    def test_osd_device_map(self, check_output):
        check_output.return_value = json.dumps(self.LISTING).encode('UTF-8')
        self.assertEqual(host_topology.osd_device_map(), {
            '0': {'data': '/dev/nvme0n1', 'aux': ['/dev/nvme1n1']},
            '1': {'data': '/dev/sdb', 'aux': []},
        })

    @patch.object(host_topology.subprocess, 'check_output')
    def test_osd_device_map_failure(self, check_output):
        check_output.side_effect = subprocess.CalledProcessError(1, 'x')
        self.assertRaises(host_topology.TopologyError,
                          host_topology.osd_device_map)
