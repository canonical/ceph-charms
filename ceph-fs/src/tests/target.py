# Copyright 2020 Canonical Ltd.
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

"""Encapsulate CephFS testing."""

import logging
import json
import subprocess
from tenacity import (
    retry, Retrying, RetryError, stop_after_attempt, stop_after_delay,
    wait_exponential, retry_if_exception_type, retry_if_result)
import unittest
import zaza
import zaza.model as model
import zaza.openstack.charm_tests.test_utils as test_utils
import zaza.openstack.utilities.generic as zaza_utils


class CephFSTests(unittest.TestCase):
    """Encapsulate CephFS tests."""

    mounts_share = False
    mount_dir = '/mnt/cephfs'
    CEPH_MON = 'ceph-mon'

    def tearDown(self):
        """Cleanup after running tests."""
        if self.mounts_share:
            for unit in ['ceph-osd/0', 'ceph-osd/1']:
                try:
                    zaza.utilities.generic.run_via_ssh(
                        unit_name=unit,
                        cmd='sudo fusermount -u {0} && sudo rmdir {0}'.format(
                            self.mount_dir))
                except subprocess.CalledProcessError:
                    logging.warning(
                        "Failed to cleanup mounts on {}".format(unit))

    def _mount_share(self, unit_name: str, perform_retry: bool = True):
        self._install_dependencies(unit_name)
        self._install_keyring(unit_name)
        cmd = 'sudo mkdir -p {0} && sudo ceph-fuse {0}'.format(
            self.mount_dir)

        if perform_retry:
            @retry(
                stop=stop_after_attempt(5),
                wait=wait_exponential(multiplier=3, min=2, max=10),
                retry=retry_if_result(lambda res: res.get('Code') != '0')
            )
            def _do_mount():
                logging.info(f"Mounting CephFS on {unit_name}")
                res = model.run_on_unit(unit_name, cmd)
                logging.info(f"Mount result: {res}")
                return res

            _do_mount()
        else:
            model.run_on_unit(unit_name, cmd)

        self.mounts_share = True

    def _install_keyring(self, unit_name: str):

        keyring = model.run_on_leader(
            self.CEPH_MON, 'cat /etc/ceph/ceph.client.admin.keyring')['Stdout']
        config = model.run_on_leader(
            self.CEPH_MON, 'cat /etc/ceph/ceph.conf')['Stdout']
        commands = [
            'sudo mkdir -p /etc/ceph',
            "echo '{}' | sudo tee /etc/ceph/ceph.conf".format(config),
            "echo '{}' | "
            'sudo tee /etc/ceph/ceph.client.admin.keyring'.format(keyring)
        ]
        for cmd in commands:
            zaza.utilities.generic.run_via_ssh(
                unit_name=unit_name,
                cmd=cmd)

    def _install_dependencies(self, unit: str):
        zaza.utilities.generic.run_via_ssh(
            unit_name=unit,
            cmd='sudo apt-get install -yq ceph-fuse')

    @classmethod
    def setUpClass(cls):
        """Run class setup for running tests."""
        super(CephFSTests, cls).setUpClass()

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=3, min=2, max=10))
    def _write_testing_file_on_instance(self, instance_name: str):
        zaza.utilities.generic.run_via_ssh(
            unit_name=instance_name,
            cmd='echo -n "test" | sudo tee {}/test'.format(self.mount_dir))

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=3, min=2, max=10))
    def _verify_testing_file_on_instance(self, instance_name: str):
        output = zaza.model.run_on_unit(
            instance_name, 'sudo cat {}/test'.format(self.mount_dir))['Stdout']
        self.assertEqual('test', output.strip())

    def test_cephfs_share(self):
        """Test that CephFS shares can be accessed on two instances.

        1. Spawn two servers
        2. mount it on both
        3. write a file on one
        4. read it on the other
        5. profit
        """
        self._mount_share('ceph-osd/0')
        self._mount_share('ceph-osd/1')

        self._write_testing_file_on_instance('ceph-osd/0')
        self._verify_testing_file_on_instance('ceph-osd/1')

    def test_conf(self):
        """Test ceph to ensure juju config options are properly set."""
        self.TESTED_UNIT = 'ceph-fs/0'

        def _get_conf():
            """get/parse ceph daemon response into dict.

            :returns dict: Current configuration of the Ceph MDS daemon
            :rtype: dict
            """
            cmd = "sudo ceph daemon mds.$HOSTNAME config show"
            last_stdout = ""

            @retry(
                wait=wait_exponential(multiplier=1, min=4, max=10),
                stop=stop_after_delay(300),
                retry=retry_if_result(lambda result: result is None)
            )
            def _attempt_conf_fetch():
                nonlocal last_stdout
                conf = model.run_on_unit(self.TESTED_UNIT, cmd)
                stdout = conf.get('Stdout', '')
                last_stdout = stdout
                try:
                    return json.loads(stdout)
                except json.JSONDecodeError:
                    logging.debug(
                        'ceph daemon config show returned invalid JSON'
                    )
                    return None

            try:
                return _attempt_conf_fetch()
            except RetryError:
                logging.error(
                    'Failed to parse ceph daemon config show output. '
                    'Last non-JSON payload: %s',
                    last_stdout)
                raise

        @retry(wait=wait_exponential(multiplier=1, min=4, max=10),
               stop=stop_after_attempt(10))
        def _change_conf_check(mds_config):
            """Change configs, then assert to ensure config was set.

            Doesn't return a value.
            """
            model.set_application_config('ceph-fs', mds_config)
            results = _get_conf()
            self.assertEqual(
                results['mds_cache_memory_limit'],
                mds_config['mds-cache-memory-limit'])
            self.assertAlmostEqual(
                float(results['mds_cache_reservation']),
                float(mds_config['mds-cache-reservation']))
            self.assertAlmostEqual(
                float(results['mds_health_cache_threshold']),
                float(mds_config['mds-health-cache-threshold']))

        # ensure defaults are set
        mds_config = {'mds-cache-memory-limit': '4294967296',
                      'mds-cache-reservation': '0.05',
                      'mds-health-cache-threshold': '1.5'}
        _change_conf_check(mds_config)

        # change defaults
        mds_config = {'mds-cache-memory-limit': '8589934592',
                      'mds-cache-reservation': '0.10',
                      'mds-health-cache-threshold': '2'}
        _change_conf_check(mds_config)

        # Restore config to keep tests idempotent
        mds_config = {'mds-cache-memory-limit': '4294967296',
                      'mds-cache-reservation': '0.05',
                      'mds-health-cache-threshold': '1.5'}
        _change_conf_check(mds_config)


class CharmOperationTest(test_utils.BaseCharmTest):
    """CephFS Charm operation tests."""

    def test_pause_resume(self):
        """Run pause and resume tests.

        Pause service and check services are stopped, then resume and check
        they are started.
        """
        services = ['ceph-mds']
        with self.pause_resume(services):
            logging.info('Testing pause resume (services="{}")'
                         .format(services))


class CephKeyRotationTests(test_utils.BaseCharmTest):
    """Tests for the rotate-key action."""

    def _get_all_keys(self, unit, entity_filter):
        cmd = 'sudo ceph auth ls'
        result = model.run_on_unit(unit, cmd)
        # Don't use json formatting, as it's buggy upstream.
        data = result['Stdout'].split()
        ret = set()

        for ix, line in enumerate(data):
            # Structure:
            # $ENTITY
            # key:
            # key contents
            # That's why we need to move one position ahead.
            if 'key:' in line and entity_filter(data[ix - 1]):
                ret.add((data[ix - 1], data[ix + 1]))
        return ret

    def _check_key_rotation(self, entity, unit):
        def entity_filter(name):
            return name.startswith(entity)

        old_keys = self._get_all_keys(unit, entity_filter)
        action_obj = model.run_action(
            unit_name=unit,
            action_name='rotate-key',
            action_params={'entity': entity}
        )
        zaza_utils.assertActionRanOK(action_obj)
        # NOTE(lmlg): There's a nasty race going on here. Essentially,
        # since this action involves 2 different applications, what
        # happens is as follows:
        #          (1)            (2)               (3)              (4)
        # ceph-mon rotates key | (idle) | remote-unit rotates key | (idle)
        # Between (2) and (3), there's a window where all units are
        # idle, _but_ the key hasn't been rotated in the other unit.
        # As such, we retry a few times instead of using the
        # `wait_for_application_states` interface.

        for attempt in Retrying(
            wait=wait_exponential(multiplier=2, max=32),
            reraise=True, stop=stop_after_attempt(20),
            retry=retry_if_exception_type(AssertionError)
        ):
            with attempt:
                new_keys = self._get_all_keys(unit, entity_filter)
                self.assertNotEqual(old_keys, new_keys)

        diff = new_keys - old_keys
        self.assertEqual(len(diff), 1)
        first = next(iter(diff))
        # Check that the entity matches. The 'entity_filter'
        # callable will return a true-like value if it
        # matches the type of entity we're after (i.e: 'mgr')
        self.assertTrue(entity_filter(first[0]))

    def _get_fs_client(self, unit):
        def _filter_fs(name):
            return (name.startswith('mds.') and
                    name not in ('mds.ceph-fs', 'mds.None'))

        ret = self._get_all_keys(unit, _filter_fs)
        if not ret:
            return None
        return next(iter(ret))[0]

    def test_key_rotate(self):
        """Test that rotating the keys actually changes them."""
        unit = 'ceph-mon/0'
        fs_svc = self._get_fs_client(unit)

        if fs_svc is not None:
            self._check_key_rotation(fs_svc, unit)
        else:
            logging.info('ceph-fs units present, but no MDS service')
