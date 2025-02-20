# Copyright 2024 Canonical Ltd.
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

import unittest
import logging
import tenacity
import json
import subprocess

import zaza
import zaza.charm_lifecycle.utils as lifecycle_utils
import zaza.model as zaza_model
import zaza.openstack.utilities.ceph as zaza_ceph
import zaza.openstack.utilities.exceptions as zaza_exceptions


def setup_ceph_proxy():
    """
    Configure ceph proxy with ceph metadata.

    Fetches admin_keyring and FSID from ceph-mon and
    uses those to configure ceph-proxy.
    """
    raw_admin_keyring = zaza_model.run_on_leader(
        "ceph-mon", 'cat /etc/ceph/ceph.client.admin.keyring')["Stdout"]
    admin_keyring = [
        line for line in raw_admin_keyring.split("\n") if "key" in line
    ][0].split(' = ')[-1].rstrip()
    fsid = zaza_model.run_on_leader("ceph-mon", "leader-get fsid")["Stdout"]
    cluster_ips = zaza_model.get_app_ips("ceph-mon")

    proxy_config = {
        'auth-supported': 'cephx',
        'admin-key': admin_keyring,
        'fsid': fsid,
        'monitor-hosts': ' '.join(cluster_ips)
    }

    logging.debug('Config: {}'.format(proxy_config))

    zaza_model.set_application_config("ceph-proxy", proxy_config)


class CephProxyTest(unittest.TestCase):
    """Test ceph via proxy."""

    @classmethod
    def setUpClass(cls):
        """Run class setup for running tests."""
        super(CephProxyTest, cls).setUpClass()

        test_config = lifecycle_utils.get_charm_config(fatal=False)
        cls.target_deploy_status = test_config.get('target_deploy_status', {})

    def test_ceph_health(self):
        """Make sure ceph-proxy can communicate with ceph."""
        logging.info('Wait for idle/ready status...')
        zaza_model.wait_for_application_states(
            states=self.target_deploy_status)

        self.assertEqual(
            zaza_model.run_on_leader("ceph-proxy", "sudo ceph health")["Code"],
            "0"
        )

    def test_cinder_ceph_restrict_pool_setup(self):
        """Make sure cinder-ceph restrict pool was created successfully."""
        try:
            zaza_model.get_application('cinder-ceph')
        except KeyError:
            raise unittest.SkipTest("Skipping OpenStack dependent test")
        logging.info('Wait for idle/ready status...')
        zaza_model.wait_for_application_states(
            states=self.target_deploy_status)

        for attempt in tenacity.Retrying(
            wait=tenacity.wait_exponential(multiplier=2, max=32),
            reraise=True, stop=tenacity.stop_after_attempt(8),
        ):
            with attempt:
                pools = zaza_ceph.get_ceph_pools('ceph-mon/0')
                if 'cinder-ceph' not in pools:
                    msg = ('cinder-ceph pool not found querying ceph-mon/0,'
                           'got: {}'.format(pools))
                    raise zaza_exceptions.CephPoolNotFound(msg)

        # Checking for cinder-ceph specific permissions makes
        # the test more rugged when we add additional relations
        # to ceph for other applications (such as glance and nova).
        expected_permissions = [
            "allow rwx pool=cinder-ceph",
            "allow class-read object_prefix rbd_children",
        ]
        cmd = "sudo ceph auth get client.cinder-ceph"
        result = zaza_model.run_on_unit('ceph-mon/0', cmd)
        output = result.get('Stdout').strip()

        for expected in expected_permissions:
            if expected not in output:
                msg = ('cinder-ceph pool restriction ({}) was not'
                       ' configured correctly.'
                       ' Found: {}'.format(expected, output))
                raise zaza_exceptions.CephPoolNotConfigured(msg)


class CephFSWithCephProxyTests(unittest.TestCase):
    """Encapsulate CephFS tests."""

    mounts_share = False
    mount_dir = '/mnt/cephfs'
    CEPH_MON = 'ceph-proxy'

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

    def _mount_share(self, unit_name: str,
                     retry: bool = True):
        self._install_dependencies(unit_name)
        self._install_keyring(unit_name)
        ssh_cmd = (
            'sudo mkdir -p {0} && '
            'sudo ceph-fuse {0}'.format(self.mount_dir)
        )
        if retry:
            for attempt in tenacity.Retrying(
                    stop=tenacity.stop_after_attempt(5),
                    wait=tenacity.wait_exponential(multiplier=3,
                                                   min=2, max=10)):
                with attempt:
                    zaza.utilities.generic.run_via_ssh(
                        unit_name=unit_name,
                        cmd=ssh_cmd)
        else:
            zaza.utilities.generic.run_via_ssh(
                unit_name=unit_name,
                cmd=ssh_cmd)
        self.mounts_share = True

    def _install_keyring(self, unit_name: str):

        keyring = zaza_model.run_on_leader(
            self.CEPH_MON, 'cat /etc/ceph/ceph.client.admin.keyring')['Stdout']
        config = zaza_model.run_on_leader(
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
        super(CephFSWithCephProxyTests, cls).setUpClass()

    @tenacity.retry(
        stop=tenacity.stop_after_attempt(5),
        wait=tenacity.wait_exponential(multiplier=3, min=2, max=10))
    def _write_testing_file_on_instance(self, instance_name: str):
        zaza.utilities.generic.run_via_ssh(
            unit_name=instance_name,
            cmd='echo -n "test" | sudo tee {}/test'.format(self.mount_dir))

    @tenacity.retry(
        stop=tenacity.stop_after_attempt(5),
        wait=tenacity.wait_exponential(multiplier=3, min=2, max=10))
    def _verify_testing_file_on_instance(self, instance_name: str):
        output = zaza_model.run_on_unit(
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
            conf = zaza_model.run_on_unit(self.TESTED_UNIT, cmd)
            return json.loads(conf['Stdout'])

        @tenacity.retry(
            wait=tenacity.wait_exponential(multiplier=1, min=4, max=10),
            stop=tenacity.stop_after_attempt(10))
        def _change_conf_check(mds_config):
            """Change configs, then assert to ensure config was set.

            Doesn't return a value.
            """
            zaza_model.set_application_config('ceph-fs', mds_config)
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
