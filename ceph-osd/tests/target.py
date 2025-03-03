# Copyright 2018 Canonical Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Ceph Testing."""

import unittest
from copy import deepcopy
import json
import logging
from os import (
    listdir,
    path
)
import re
import tempfile
import urllib3

import tenacity

import zaza.openstack.charm_tests.test_utils as test_utils
import zaza.model as zaza_model
import zaza.openstack.utilities.ceph as zaza_ceph
import zaza.openstack.utilities.exceptions as zaza_exceptions
import zaza.openstack.utilities.generic as zaza_utils
import zaza.openstack.utilities.openstack as zaza_openstack

# Disable warnings for ssl_verify=false
urllib3.disable_warnings(
    urllib3.exceptions.InsecureRequestWarning
)


class CephLowLevelTest(test_utils.BaseCharmTest):
    """Ceph Low Level Test Class."""

    @classmethod
    def setUpClass(cls):
        """Run class setup for running ceph low level tests."""
        super(CephLowLevelTest, cls).setUpClass()

    def test_processes(self):
        """Verify Ceph processes.

        Verify that the expected service processes are running
        on each ceph unit.
        """
        logging.info('Checking ceph-mon and ceph-osd processes...')
        # Process name and quantity of processes to expect on each unit
        ceph_mon_processes = {
            'ceph-mon': 1,
            'ceph-mgr': 1,
        }

        ceph_osd_processes = {
            'ceph-osd': [1, 2, 3]
        }

        # Units with process names and PID quantities expected
        expected_processes = {
            'ceph-mon/0': ceph_mon_processes,
            'ceph-mon/1': ceph_mon_processes,
            'ceph-mon/2': ceph_mon_processes,
            'ceph-osd/0': ceph_osd_processes,
            'ceph-osd/1': ceph_osd_processes,
            'ceph-osd/2': ceph_osd_processes
        }

        actual_pids = zaza_utils.get_unit_process_ids(expected_processes)
        ret = zaza_utils.validate_unit_process_ids(expected_processes,
                                                   actual_pids)
        self.assertTrue(ret)

    def test_services(self):
        """Verify the ceph services.

        Verify the expected services are running on the service units.
        """
        logging.info('Checking ceph-osd and ceph-mon services...')
        services = {}
        ceph_services = ['ceph-mon', 'ceph-mgr']
        services['ceph-osd/0'] = ['ceph-osd']

        services['ceph-mon/0'] = ceph_services
        services['ceph-mon/1'] = ceph_services
        services['ceph-mon/2'] = ceph_services

        for unit_name, unit_services in services.items():
            zaza_model.block_until_service_status(
                unit_name=unit_name,
                services=unit_services,
                target_status='running'
            )

    @test_utils.skipUntilVersion('ceph-mon', 'ceph', '14.2.0')
    def test_pg_tuning(self):
        """Verify that auto PG tuning is enabled for Nautilus+."""
        unit_name = 'ceph-mon/0'
        cmd = "ceph osd pool autoscale-status --format=json"
        result = zaza_model.run_on_unit(unit_name, cmd)
        self.assertEqual(result['Code'], '0')
        for pool in json.loads(result['Stdout']):
            self.assertEqual(pool['pg_autoscale_mode'], 'on')


class CephTest(test_utils.BaseCharmTest):
    """Ceph common functional tests."""

    @classmethod
    def setUpClass(cls):
        """Run the ceph's common class setup."""
        super(CephTest, cls).setUpClass()

    def osd_out_in(self, services):
        """Run OSD out and OSD in tests.

        Remove OSDs and then add them back in on a unit checking that services
        are in the required state after each action

        :param services: Services expected to be restarted when config_file is
                         changed.
        :type services: list
        """
        zaza_model.block_until_service_status(
            self.lead_unit,
            services,
            'running',
            model_name=self.model_name)
        zaza_model.block_until_unit_wl_status(
            self.lead_unit,
            'active',
            model_name=self.model_name)
        zaza_model.run_action(
            self.lead_unit,
            'osd-out',
            model_name=self.model_name)
        zaza_model.block_until_unit_wl_status(
            self.lead_unit,
            'maintenance',
            model_name=self.model_name)
        zaza_model.block_until_all_units_idle(model_name=self.model_name)
        zaza_model.run_action(
            self.lead_unit,
            'osd-in',
            model_name=self.model_name)
        zaza_model.block_until_unit_wl_status(
            self.lead_unit,
            'active',
            model_name=self.model_name)
        zaza_model.block_until_all_units_idle(model_name=self.model_name)
        zaza_model.block_until_service_status(
            self.lead_unit,
            services,
            'running',
            model_name=self.model_name)

    def test_ceph_check_osd_pools(self):
        """Check OSD pools.

        Check osd pools on all ceph units, expect them to be
        identical, and expect specific pools to be present.
        """
        try:
            zaza_model.get_application('cinder-ceph')
        except KeyError:
            raise unittest.SkipTest("Skipping OpenStack dependent test")
        logging.info('Checking pools on ceph units...')

        expected_pools = zaza_ceph.get_expected_pools()
        results = []
        unit_name = 'ceph-mon/0'

        # Check for presence of expected pools on each unit
        logging.debug('Expected pools: {}'.format(expected_pools))
        pools = zaza_ceph.get_ceph_pools(unit_name)
        results.append(pools)

        for expected_pool in expected_pools:
            if expected_pool not in pools:
                msg = ('{} does not have pool: '
                       '{}'.format(unit_name, expected_pool))
                raise zaza_exceptions.CephPoolNotFound(msg)
        logging.debug('{} has (at least) the expected '
                      'pools.'.format(unit_name))

        # Check that all units returned the same pool name:id data
        for i, result in enumerate(results):
            for other in results[i+1:]:
                logging.debug('result: {}, other: {}'.format(result, other))
                self.assertEqual(result, other)

    def test_ceph_pool_creation_with_text_file(self):
        """Check the creation of a pool and a text file.

        Create a pool, add a text file to it and retrieve its content.
        Verify that the content matches the original file.
        """
        unit_name = 'ceph-mon/0'
        cmd = 'sudo ceph osd pool create test {PG_NUM}; \
               echo 123456789 > /tmp/input.txt; \
               rados put -p test test_input /tmp/input.txt; \
               rados get -p test test_input /dev/stdout'
        cmd = cmd.format(PG_NUM=32)
        logging.debug('Creating test pool and putting test file in pool...')
        result = zaza_model.run_on_unit(unit_name, cmd)
        code = result.get('Code')
        if code != '0':
            raise zaza_model.CommandRunFailed(cmd, result)
        output = result.get('Stdout').strip()
        logging.debug('Output received: {}'.format(output))
        self.assertEqual(output, '123456789')

    def test_ceph_encryption(self):
        """Test Ceph encryption.

        Verify that the new disk is added with encryption by checking for
        Ceph's encryption keys directory.
        """
        current_release = zaza_openstack.get_os_release(application='ceph-mon')
        trusty_mitaka = zaza_openstack.get_os_release('trusty_mitaka')
        if current_release >= trusty_mitaka:
            logging.warn("Skipping encryption test for Mitaka and higher")
            return
        unit_name = 'ceph-osd/0'
        set_default = {
            'osd-encrypt': 'False',
            'osd-devices': '/dev/vdb /srv/ceph',
        }
        set_alternate = {
            'osd-encrypt': 'True',
            'osd-devices': '/dev/vdb /srv/ceph /srv/ceph_encrypted',
        }
        juju_service = 'ceph-osd'
        logging.info('Making config change on {}...'.format(juju_service))
        mtime = zaza_model.get_unit_time(unit_name)

        file_mtime = None

        folder_name = '/etc/ceph/dmcrypt-keys/'
        with self.config_change(set_default, set_alternate,
                                application_name=juju_service):
            with tempfile.TemporaryDirectory() as tempdir:
                # Creating a temp dir to copy keys
                temp_folder = '/tmp/dmcrypt-keys'
                cmd = 'mkdir {}'.format(temp_folder)
                ret = zaza_model.run_on_unit(unit_name, cmd)
                logging.debug('Ret for cmd {} is {}'.format(cmd, ret))
                # Copy keys from /etc to /tmp
                cmd = 'sudo cp {}* {}'.format(folder_name, temp_folder)
                ret = zaza_model.run_on_unit(unit_name, cmd)
                logging.debug('Ret for cmd {} is {}'.format(cmd, ret))
                # Changing permissions to be able to SCP the files
                cmd = 'sudo chown -R ubuntu:ubuntu {}'.format(temp_folder)
                ret = zaza_model.run_on_unit(unit_name, cmd)
                logging.debug('Ret for cmd {} is {}'.format(cmd, ret))
                # SCP to retrieve all files in folder
                # -p: preserve timestamps
                source = '/tmp/dmcrypt-keys/*'
                zaza_model.scp_from_unit(unit_name=unit_name,
                                         source=source,
                                         destination=tempdir,
                                         scp_opts='-p')
                for elt in listdir(tempdir):
                    file_path = '/'.join([tempdir, elt])
                    if path.isfile(file_path):
                        file_mtime = path.getmtime(file_path)
                        if file_mtime:
                            break

        if not file_mtime:
            logging.warn('Could not determine mtime, assuming '
                         'folder does not exist')
            raise FileNotFoundError('folder does not exist')

        if file_mtime >= mtime:
            logging.info('Folder mtime is newer than provided mtime '
                         '(%s >= %s) on %s (OK)' % (file_mtime,
                                                    mtime, unit_name))
        else:
            logging.warn('Folder mtime is older than provided mtime'
                         '(%s < on %s) on %s' % (file_mtime,
                                                 mtime, unit_name))
            raise Exception('Folder mtime is older than provided mtime')

    def test_blocked_when_non_pristine_disk_appears(self):
        """Test blocked state with non-pristine disk.

        Validate that charm goes into blocked state when it is presented with
        new block devices that have foreign data on them.
        Instances used in UOSCI has a flavour with ephemeral storage in
        addition to the bootable instance storage.  The ephemeral storage
        device is partitioned, formatted and mounted early in the boot process
        by cloud-init.
        As long as the device is mounted the charm will not attempt to use it.
        If we unmount it and trigger the config-changed hook the block device
        will appear as a new and previously untouched device for the charm.
        One of the first steps of device eligibility checks should be to make
        sure we are seeing a pristine and empty device before doing any
        further processing.
        As the ephemeral device will have data on it we can use it to validate
        that these checks work as intended.
        """
        current_release = zaza_openstack.get_os_release(application='ceph-mon')
        focal_ussuri = zaza_openstack.get_os_release('focal_ussuri')
        if current_release >= focal_ussuri:
            # NOTE(ajkavanagh) - focal (on ServerStack) is broken for /dev/vdb
            # and so this test can't pass: LP#1842751 discusses the issue, but
            # basically the snapd daemon along with lxcfs results in /dev/vdb
            # being mounted in the lxcfs process namespace.  If the charm
            # 'tries' to umount it, it can (as root), but the mount is still
            # 'held' by lxcfs and thus nothing else can be done with it.  This
            # is only a problem in serverstack with images with a default
            # /dev/vdb ephemeral
            logging.warn("Skipping pristine disk test for focal and higher")
            return
        logging.info('Checking behaviour when non-pristine disks appear...')
        logging.info('Configuring ephemeral-unmount...')
        alternate_conf = {
            'ephemeral-unmount': '/mnt',
            'osd-devices': '/dev/vdb'
        }
        juju_service = 'ceph-osd'
        zaza_model.set_application_config(juju_service, alternate_conf)
        ceph_osd_states = {
            'ceph-osd': {
                'workload-status': 'blocked',
                'workload-status-message': 'Non-pristine'
            }
        }
        zaza_model.wait_for_application_states(states=ceph_osd_states)
        logging.info('Units now in blocked state, running zap-disk action...')
        unit_names = ['ceph-osd/0', 'ceph-osd/1', 'ceph-osd/2']
        for unit_name in unit_names:
            zap_disk_params = {
                'devices': '/dev/vdb',
                'i-really-mean-it': True,
            }
            action_obj = zaza_model.run_action(
                unit_name=unit_name,
                action_name='zap-disk',
                action_params=zap_disk_params
            )
            logging.debug('Result of action: {}'.format(action_obj))

        logging.info('Running add-disk action...')
        for unit_name in unit_names:
            add_disk_params = {
                'osd-devices': '/dev/vdb',
            }
            action_obj = zaza_model.run_action(
                unit_name=unit_name,
                action_name='add-disk',
                action_params=add_disk_params
            )
            logging.debug('Result of action: {}'.format(action_obj))

        logging.info('Wait for idle/ready status...')
        zaza_model.wait_for_application_states()

        logging.info('OK')

        set_default = {
            'ephemeral-unmount': '',
            'osd-devices': '/dev/vdb',
        }

        bionic_train = zaza_openstack.get_os_release('bionic_train')
        if current_release < bionic_train:
            set_default['osd-devices'] = '/dev/vdb /srv/ceph'

        logging.info('Restoring to default configuration...')
        zaza_model.set_application_config(juju_service, set_default)

        zaza_model.wait_for_application_states()

    def test_pause_and_resume(self):
        """The services can be paused and resumed."""
        logging.info('Checking pause and resume actions...')
        self.pause_resume(['ceph-osd'])

    def get_device_for_blacklist(self, unit):
        """Return a device to be used by the blacklist tests."""
        cmd = "mount | grep 'on / ' | awk '{print $1}'"
        obj = zaza_model.run_on_unit(unit, cmd)
        return obj.get('Stdout').strip()

    def test_blacklist(self):
        """Check the blacklist action.

        The blacklist actions execute and behave as expected.
        """
        logging.info('Checking blacklist-add-disk and '
                     'blacklist-remove-disk actions...')
        unit_name = 'ceph-osd/0'

        zaza_model.block_until_unit_wl_status(
            unit_name,
            'active'
        )

        # Attempt to add device with non-absolute path should fail
        action_obj = zaza_model.run_action(
            unit_name=unit_name,
            action_name='blacklist-add-disk',
            action_params={'osd-devices': 'vda'}
        )
        self.assertTrue(action_obj.status != 'completed')
        zaza_model.block_until_unit_wl_status(
            unit_name,
            'active'
        )

        # Attempt to add device with non-existent path should fail
        action_obj = zaza_model.run_action(
            unit_name=unit_name,
            action_name='blacklist-add-disk',
            action_params={'osd-devices': '/non-existent'}
        )
        self.assertTrue(action_obj.status != 'completed')
        zaza_model.block_until_unit_wl_status(
            unit_name,
            'active'
        )

        # Attempt to add device with existent path should succeed
        device = self.get_device_for_blacklist(unit_name)
        if not device:
            raise unittest.SkipTest(
                "Skipping test because no device was found")

        action_obj = zaza_model.run_action(
            unit_name=unit_name,
            action_name='blacklist-add-disk',
            action_params={'osd-devices': device}
        )
        self.assertEqual('completed', action_obj.status)
        zaza_model.block_until_unit_wl_status(
            unit_name,
            'active'
        )

        # Attempt to remove listed device should always succeed
        action_obj = zaza_model.run_action(
            unit_name=unit_name,
            action_name='blacklist-remove-disk',
            action_params={'osd-devices': device}
        )
        self.assertEqual('completed', action_obj.status)
        zaza_model.block_until_unit_wl_status(
            unit_name,
            'active'
        )
        logging.debug('OK')

    def test_list_disks(self):
        """Test the list-disks action.

        The list-disks action execute.
        """
        logging.info('Checking list-disks action...')
        unit_name = 'ceph-osd/0'

        zaza_model.block_until_unit_wl_status(
            unit_name,
            'active'
        )

        action_obj = zaza_model.run_action(
            unit_name=unit_name,
            action_name='list-disks',
        )
        self.assertEqual('completed', action_obj.status)
        zaza_model.block_until_unit_wl_status(
            unit_name,
            'active'
        )
        logging.debug('OK')

    def get_local_osd_id(self, unit):
        """Get the OSD id for a unit."""
        ret = zaza_model.run_on_unit(unit,
                                     'ceph-volume lvm list --format=json')
        local = list(json.loads(ret['Stdout']))[-1]
        return local if local.startswith('osd.') else 'osd.' + local

    def get_num_osds(self, osd, is_up_only=False):
        """Compute the number of active OSD's."""
        result = zaza_model.run_on_unit(osd, 'ceph osd stat --format=json')
        result = json.loads(result['Stdout'])
        if is_up_only:
            return int(result['num_up_osds'])
        else:
            return int(result['num_osds'])

    def get_osd_devices_on_unit(self, unit_name):
        """Get information for osd devices present on a particular unit.

        :param unit: Unit name to be queried for osd device info.
        :type unit: str
        """
        osd_devices = json.loads(
            zaza_model.run_on_unit(
                unit_name, 'ceph-volume lvm list --format=json'
            ).get('Stdout', '')
        )

        return osd_devices

    def remove_disk_from_osd_unit(self, unit, osd_id, is_purge=False):
        """Remove osd device with provided osd_id from unit.

        :param unit: Unit name where the osd device is to be removed from.
        :type unit: str

        :param osd_id: osd-id for the osd device to be removed.
        :type osd_id: str

        :param is_purge: whether to purge the osd device
        :type is_purge: bool
        """
        action_obj = zaza_model.run_action(
            unit_name=unit,
            action_name='remove-disk',
            action_params={
                'osd-ids': osd_id,
                'timeout': 10,
                'format': 'json',
                'purge': is_purge
            }
        )
        zaza_utils.assertActionRanOK(action_obj)
        results = json.loads(action_obj.data['results']['message'])
        results = results[next(iter(results))]
        self.assertEqual(results['osd-ids'], osd_id)
        zaza_model.run_on_unit(unit, 'partprobe')

    def remove_one_osd(self, unit, block_devs):
        """Remove one device from osd unit.

        :param unit: Unit name where the osd device is to be removed from.
        :type unit: str
        :params block_devs: list of block devices on the scpecified unit
        :type block_devs: list[str]
        """
        # Should have more than 1 OSDs to take one out and test.
        self.assertGreater(len(block_devs), 1)

        # Get complete device details for an OSD.
        key = list(block_devs)[-1]
        device = {
            'osd-id': key if key.startswith('osd.') else 'osd.' + key,
            'block-device': block_devs[key][0]['devices'][0]
        }

        self.remove_disk_from_osd_unit(unit, device['osd-id'], is_purge=True)
        return device

    def test_cache_device(self):
        """Test replacing a disk in use."""
        logging.info('Running add-disk action with a caching device')
        mon = next(iter(zaza_model.get_units('ceph-mon'))).entity_id
        osds = [x.entity_id for x in zaza_model.get_units('ceph-osd')]
        osd_info = dict()

        # Remove one of the two disks.
        logging.info('Removing single disk from each OSD')
        for unit in osds:
            block_devs = self.get_osd_devices_on_unit(unit)
            if len(block_devs) < 2:
                continue
            device_info = self.remove_one_osd(unit, block_devs)
            block_dev = device_info['block-device']
            logging.info("Removing device %s from unit %s" % (block_dev, unit))
            osd_info[unit] = device_info
        if not osd_info:
            raise unittest.SkipTest(
                'Skipping OSD replacement Test, no spare devices added')

        logging.debug('Removed OSD Info: {}'.format(osd_info))
        zaza_model.wait_for_application_states()

        logging.info('Recycling previously removed disks')
        for unit, device_info in osd_info.items():
            osd_id = device_info['osd-id']
            block_dev = device_info['block-device']
            logging.info("Found device %s on unit %s" % (block_dev, unit))
            self.assertNotEqual(block_dev, None)
            action_obj = zaza_model.run_action(
                unit_name=unit,
                action_name='add-disk',
                action_params={'osd-devices': block_dev,
                               'osd-ids': osd_id,
                               'partition-size': 5}
            )
            zaza_utils.assertActionRanOK(action_obj)
        zaza_model.wait_for_application_states()

        logging.info('Removing previously added OSDs')
        for unit, device_info in osd_info.items():
            osd_id = device_info['osd-id']
            block_dev = device_info['block-device']
            logging.info(
                "Removing block device %s from unit %s" %
                (block_dev, unit)
            )
            self.remove_disk_from_osd_unit(unit, osd_id, is_purge=False)
        zaza_model.wait_for_application_states()

        logging.info('Finally adding back OSDs')
        for unit, device_info in osd_info.items():
            block_dev = device_info['block-device']
            action_obj = zaza_model.run_action(
                unit_name=unit,
                action_name='add-disk',
                action_params={'osd-devices': block_dev,
                               'partition-size': 5}
            )
            zaza_utils.assertActionRanOK(action_obj)
        zaza_model.wait_for_application_states()

        for attempt in tenacity.Retrying(
            wait=tenacity.wait_exponential(multiplier=2, max=32),
            reraise=True, stop=tenacity.stop_after_attempt(10),
            retry=tenacity.retry_if_exception_type(AssertionError)
        ):
            with attempt:
                self.assertEqual(
                    len(osds) * 2, self.get_num_osds(mon, is_up_only=True)
                )


class SecurityTest(unittest.TestCase):
    """Ceph Security Tests."""

    @classmethod
    def setUpClass(cls):
        """Run class setup for running ceph security tests."""
        super(SecurityTest, cls).setUpClass()

    def test_osd_security_checklist(self):
        """Verify expected state with security-checklist."""
        expected_failures = []
        expected_passes = [
            'validate-file-ownership',
            'validate-file-permissions',
        ]

        logging.info('Running `security-checklist` action'
                     ' on Ceph OSD leader unit')
        test_utils.audit_assertions(
            zaza_model.run_action_on_leader(
                'ceph-osd',
                'security-checklist',
                action_params={}),
            expected_passes,
            expected_failures,
            expected_to_pass=True)


class OsdService:
    """Simple representation of ceph-osd systemd service."""

    def __init__(self, id_):
        """
        Init service using its ID.

        e.g.: id_=1 -> ceph-osd@1
        """
        self.id = id_
        self.name = 'ceph-osd@{}'.format(id_)


async def async_wait_for_service_status(unit_name, services, target_status,
                                        model_name=None, timeout=2700):
    """Wait for all services on the unit to be in the desired state.

    Note: This function emulates the
    `zaza.model.async_block_until_service_status` function, but it's using
    `systemctl is-active` command instead of `pidof/pgrep` of the original
    function.

    :param unit_name: Name of unit to run action on
    :type unit_name: str
    :param services: List of services to check
    :type services: List[str]
    :param target_status: State services must be in (stopped or running)
    :type target_status: str
    :param model_name: Name of model to query.
    :type model_name: str
    :param timeout: Time to wait for status to be achieved
    :type timeout: int
    """
    async def _check_service():
        services_ok = True
        for service in services:
            command = r"systemctl is-active '{}'".format(service)
            out = await zaza_model.async_run_on_unit(
                unit_name,
                command,
                model_name=model_name,
                timeout=timeout)
            response = out['Stdout'].strip()

            if target_status == "running" and response == 'active':
                continue
            elif target_status == "stopped" and response == 'inactive':
                continue
            else:
                services_ok = False
                break

        return services_ok

    accepted_states = ('stopped', 'running')
    if target_status not in accepted_states:
        raise RuntimeError('Invalid target state "{}". Accepted states: '
                           '{}'.format(target_status, accepted_states))

    async with zaza_model.run_in_model(model_name):
        await zaza_model.async_block_until(_check_service, timeout=timeout)


wait_for_service = zaza_model.sync_wrapper(async_wait_for_service_status)


class ServiceTest(unittest.TestCase):
    """ceph-osd systemd service tests."""

    TESTED_UNIT = 'ceph-osd/0'  # This can be any ceph-osd unit in the model
    SERVICE_PATTERN = re.compile(r'ceph-osd@(?P<service_id>\d+)\.service')

    def __init__(self, methodName='runTest'):
        """Initialize Test Case."""
        super(ServiceTest, self).__init__(methodName)
        self._available_services = None

    @classmethod
    def setUpClass(cls):
        """Run class setup for running ceph service tests."""
        super(ServiceTest, cls).setUpClass()

    def setUp(self):
        """Run test setup."""
        # Skip 'service' action tests on systems without systemd
        result = zaza_model.run_on_unit(self.TESTED_UNIT, 'which systemctl')
        if not result['Stdout']:
            raise unittest.SkipTest("'service' action is not supported on "
                                    "systems without 'systemd'. Skipping "
                                    "tests.")
        # Note(mkalcok): This counter reset is needed because ceph-osd service
        #       is limited to 3 restarts per 30 mins which is insufficient
        #       when running functional tests for 'service' action. This
        #       limitation is defined in /lib/systemd/system/ceph-osd@.service
        #       in section [Service] with options 'StartLimitInterval' and
        #       'StartLimitBurst'
        reset_counter = 'systemctl reset-failed'
        zaza_model.run_on_unit(self.TESTED_UNIT, reset_counter)

    def tearDown(self):
        """Start ceph-osd services after each test.

        This ensures that the environment is ready for the next tests.
        """
        zaza_model.run_action_on_units([self.TESTED_UNIT, ], 'start',
                                       action_params={'osds': 'all'},
                                       raise_on_failure=False)

    @property
    def available_services(self):
        """Return list of all ceph-osd services present on the TESTED_UNIT."""
        if self._available_services is None:
            self._available_services = self._fetch_osd_services()
        return self._available_services

    def _fetch_osd_services(self):
        """Fetch all ceph-osd services present on the TESTED_UNIT."""
        service_list = []
        service_list_cmd = 'systemctl list-units --full --all ' \
                           '--no-pager -t service'
        result = zaza_model.run_on_unit(self.TESTED_UNIT, service_list_cmd)
        for line in result['Stdout'].split('\n'):
            service_name = self.SERVICE_PATTERN.search(line)
            if service_name:
                service_id = int(service_name.group('service_id'))
                service_list.append(OsdService(service_id))
        return service_list

    def test_start_stop_all_by_keyword(self):
        """Start and Stop all ceph-osd services using keyword 'all'."""
        service_list = [service.name for service in self.available_services]

        logging.info("Running 'service stop=all' action on {} "
                     "unit".format(self.TESTED_UNIT))
        zaza_model.run_action_on_units([self.TESTED_UNIT], 'stop',
                                       action_params={'osds': 'all'})
        wait_for_service(unit_name=self.TESTED_UNIT,
                         services=service_list,
                         target_status='stopped')

        logging.info("Running 'service start=all' action on {} "
                     "unit".format(self.TESTED_UNIT))
        zaza_model.run_action_on_units([self.TESTED_UNIT, ], 'start',
                                       action_params={'osds': 'all'})
        wait_for_service(unit_name=self.TESTED_UNIT,
                         services=service_list,
                         target_status='running')

    def test_start_stop_all_by_list(self):
        """Start and Stop all ceph-osd services using explicit list."""
        service_list = [service.name for service in self.available_services]
        service_ids = [str(service.id) for service in self.available_services]
        action_params = ','.join(service_ids)

        logging.info("Running 'service stop={}' action on {} "
                     "unit".format(action_params, self.TESTED_UNIT))
        zaza_model.run_action_on_units([self.TESTED_UNIT, ], 'stop',
                                       action_params={'osds': action_params})
        wait_for_service(unit_name=self.TESTED_UNIT,
                         services=service_list,
                         target_status='stopped')

        logging.info("Running 'service start={}' action on {} "
                     "unit".format(action_params, self.TESTED_UNIT))
        zaza_model.run_action_on_units([self.TESTED_UNIT, ], 'start',
                                       action_params={'osds': action_params})
        wait_for_service(unit_name=self.TESTED_UNIT,
                         services=service_list,
                         target_status='running')

    def test_stop_specific(self):
        """Stop only specified ceph-osd service."""
        if len(self.available_services) < 2:
            raise unittest.SkipTest('This test can be performed only if '
                                    'there\'s more than one ceph-osd service '
                                    'present on the tested unit')

        should_run = deepcopy(self.available_services)
        to_stop = should_run.pop()
        should_run = [service.name for service in should_run]

        logging.info("Running 'service stop={} on {} "
                     "unit".format(to_stop.id, self.TESTED_UNIT))

        zaza_model.run_action_on_units([self.TESTED_UNIT, ], 'stop',
                                       action_params={'osds': to_stop.id})

        wait_for_service(unit_name=self.TESTED_UNIT,
                         services=[to_stop.name, ],
                         target_status='stopped')
        wait_for_service(unit_name=self.TESTED_UNIT,
                         services=should_run,
                         target_status='running')

    def test_start_specific(self):
        """Start only specified ceph-osd service."""
        if len(self.available_services) < 2:
            raise unittest.SkipTest('This test can be performed only if '
                                    'there\'s more than one ceph-osd service '
                                    'present on the tested unit')

        service_names = [service.name for service in self.available_services]
        should_stop = deepcopy(self.available_services)
        to_start = should_stop.pop()
        should_stop = [service.name for service in should_stop]

        # Note: can't stop ceph-osd.target as restarting a single OSD will
        # cause this to start all of the OSDs when a single one starts.
        logging.info("Stopping all running ceph-osd services")
        service_stop_cmd = '; '.join(['systemctl stop {}'.format(service)
                                      for service in service_names])
        zaza_model.run_on_unit(self.TESTED_UNIT, service_stop_cmd)

        wait_for_service(unit_name=self.TESTED_UNIT,
                         services=service_names,
                         target_status='stopped')

        logging.info("Running 'service start={} on {} "
                     "unit".format(to_start.id, self.TESTED_UNIT))

        zaza_model.run_action_on_units([self.TESTED_UNIT, ], 'start',
                                       action_params={'osds': to_start.id})

        wait_for_service(unit_name=self.TESTED_UNIT,
                         services=[to_start.name, ],
                         target_status='running')

        wait_for_service(unit_name=self.TESTED_UNIT,
                         services=should_stop,
                         target_status='stopped')

    def test_active_after_pristine_block(self):
        """Test if we can get back to active state after pristine block.

        Set a non-pristine status, then trigger update-status to see if it
        clears.
        """
        logging.info('Setting Non-pristine status')
        zaza_model.run_on_leader(
            "ceph-osd",
            "status-set blocked 'Non-pristine'"
        )
        ceph_osd_states = {
            'ceph-osd': {
                'workload-status': 'blocked',
                'workload-status-message-prefix': 'Non-pristine'
            }
        }
        zaza_model.wait_for_application_states(states=ceph_osd_states)
        logging.info('Running update-status action')
        zaza_model.run_on_leader('ceph-osd', 'hooks/update-status')
        logging.info('Wait for idle/ready status')
        zaza_model.wait_for_application_states()
