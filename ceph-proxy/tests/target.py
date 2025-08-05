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

