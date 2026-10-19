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


JOHNNY_KEY = 'AQCnjmtbuEACMxAA7joUmgLIGI4/3LKkPzUy8g=='

KO_POOL = 'ceph-proxy-ko'
KO_APPS = {
    'ceph-proxy-ko-a': ('johnny-ko-a', 'johnny-ko-a', 'user-keys'),
    'ceph-proxy-ko-b': ('johnny-ko-b', 'johnny-ko-b', 'user-keys-secret'),
}
KO_SECRET = 'ceph-proxy-ko-b-keys'


@tenacity.retry(
    wait=tenacity.wait_exponential(multiplier=1, min=2, max=10),
    stop=tenacity.stop_after_attempt(10),
    reraise=True,
)
def _get_proxy_config():
    """Return Ceph metadata for configuring ceph-proxy."""
    admin_key_result = zaza_model.run_on_leader(
        'ceph-mon',
        'sudo ceph auth get-key client.admin',
    )
    fsid_result = zaza_model.run_on_leader(
        'ceph-mon',
        'sudo ceph fsid',
    )
    admin_key = admin_key_result.get('Stdout', '').strip()
    fsid = fsid_result.get('Stdout', '').strip()
    cluster_ips = [ip for ip in zaza_model.get_app_ips('ceph-mon') if ip]

    if admin_key_result.get('Code') != '0' or not admin_key:
        raise AssertionError(
            'Unable to fetch client.admin key from ceph-mon leader: '
            f"{admin_key_result}"
        )
    if fsid_result.get('Code') != '0' or not fsid:
        raise AssertionError(
            'Unable to fetch fsid from ceph-mon leader: '
            f"{fsid_result}"
        )
    if not cluster_ips:
        raise AssertionError('Unable to determine ceph-mon application IPs')

    return {
        'auth-supported': 'cephx',
        'admin-key': admin_key,
        'fsid': fsid,
        'monitor-hosts': ' '.join(cluster_ips),
        'user-keys': 'client.johnny:{}'.format(JOHNNY_KEY),
    }


def setup_ceph_proxy():
    """Configure ceph-proxy with metadata from the backing Ceph cluster."""
    proxy_config = _get_proxy_config()
    logging.info('Configuring ceph-proxy with %s', proxy_config)
    zaza_model.set_application_config('ceph-proxy', proxy_config)


def _juju(*args):
    """Run a juju CLI command against the test model; returns stdout."""
    cmd = ['juju'] + list(args) + ['-m', zaza_model.get_juju_model()]
    return subprocess.check_output(cmd, text=True).strip()


def _run_on_mon(cmd):
    """Run cmd on the ceph-mon leader; return stdout or raise."""
    result = zaza_model.run_on_leader('ceph-mon', cmd)
    if str(result.get('Code')) != '0':
        raise AssertionError(f'{cmd} failed: {result}')
    return result.get('Stdout', '').strip()


def _create_ko_user(user):
    """(Re)create client.<user> scoped to KO_POOL; return its key."""
    return _run_on_mon(
        f"sudo ceph auth get-or-create-key client.{user} "
        f"mon 'profile rbd' osd 'profile rbd pool={KO_POOL}'"
    )


def setup_ceph_proxy_keys_only():
    """Configure the keys-only proxies: fsid + mons + user keys, no admin.

    ko-a gets its key through plain user-keys, ko-b through secret.
    The pool is created up front because keys-only proxies cannot
    create pools.
    """
    _run_on_mon(f'sudo ceph osd pool create {KO_POOL} 8')
    _run_on_mon(f'sudo rbd pool init {KO_POOL}')

    base = _get_proxy_config()
    common = {
        'auth-supported': 'cephx',
        'fsid': base['fsid'],
        'monitor-hosts': base['monitor-hosts'],
    }

    _, user_a, _ = KO_APPS['ceph-proxy-ko-a']
    key_a = _create_ko_user(user_a)
    zaza_model.set_application_config('ceph-proxy-ko-a', dict(
        common, **{'user-keys': f'client.{user_a}:{key_a}'}))

    _, user_b, _ = KO_APPS['ceph-proxy-ko-b']
    key_b = _create_ko_user(user_b)
    uri = _juju('add-secret', KO_SECRET,
                f'user-keys=client.{user_b}:{key_b}')
    _juju('grant-secret', KO_SECRET, 'ceph-proxy-ko-b')
    zaza_model.set_application_config('ceph-proxy-ko-b', dict(
        common, **{'user-keys-secret': uri}))

    logging.info('Configured keys-only proxies (keys not logged)')


def _get_relation_info(unit_name: str, endpoint: str,
                       related_endpoint: str = None,
                       related_application: str = None):
    cmd = ['juju', 'show-unit', '--format=json', unit_name]
    output = subprocess.check_output(cmd, text=True)
    unit_data = json.loads(output)[unit_name]

    for relation_info in unit_data.get('relation-info', []):
        if relation_info.get('endpoint') != endpoint:
            continue
        if (related_endpoint and
                relation_info.get('related-endpoint') != related_endpoint):
            continue
        if related_application:
            related_units = relation_info.get('related-units', {})
            if not any(
                    related_unit.split('/')[0] == related_application
                    for related_unit in related_units):
                continue
        return relation_info

    raise KeyError(
        'Unable to find relation info for {}:{} -> {}'.format(
            unit_name, endpoint, related_endpoint))


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


class CephProxyUserKeyTests(unittest.TestCase):
    """Test configured user-keys with a generic ceph client."""

    @tenacity.retry(
        wait=tenacity.wait_exponential(multiplier=1, min=2, max=10),
        stop=tenacity.stop_after_attempt(10),
        reraise=True)
    def _get_johnny_relation_data(self):
        relation_info = _get_relation_info(
            'johnny/0',
            endpoint='ceph',
            related_endpoint='client',
            related_application='ceph-proxy')
        remote_units = relation_info.get('related-units', {})
        data = remote_units['ceph-proxy/0']['data']
        self.assertEqual(JOHNNY_KEY, data.get('key'))
        self.assertEqual('cephx', data.get('auth'))
        self.assertTrue(data.get('ceph-public-address'))
        return data

    def test_user_keys_with_generic_ceph_client(self):
        """Verify configured user-keys are shared with a ceph-client app."""
        self._get_johnny_relation_data()

        for attempt in tenacity.Retrying(
            wait=tenacity.wait_exponential(multiplier=2, max=32),
            reraise=True, stop=tenacity.stop_after_attempt(8),
        ):
            with attempt:
                pools = zaza_ceph.get_ceph_pools('ceph-mon/0')
                if 'johnny-metadata' not in pools:
                    msg = ('johnny-metadata pool not found querying '
                           'ceph-mon/0, got: {}'.format(pools))
                    raise zaza_exceptions.CephPoolNotFound(msg)

        result = zaza_model.run_on_unit(
            'ceph-mon/0',
            'sudo ceph auth get client.johnny')
        self.assertNotEqual(0, int(result.get('Code')))
        self.assertIn(
            'failed to find client.johnny',
            result.get('Stderr', ''))


class CephProxyKeysOnlyTests(unittest.TestCase):
    """ceph-proxy without admin-key: keys only, nothing on the host."""

    KO_MACHINE_UNIT = 'ceph-proxy-ko-a/0'   # both proxies share its machine

    @classmethod
    def setUpClass(cls):
        super(CephProxyKeysOnlyTests, cls).setUpClass()
        test_config = lifecycle_utils.get_charm_config(fatal=False)
        states = dict(test_config.get('target_deploy_status', {}))
        states.update({
            app: {'workload-status': 'active',
                  'workload-status-message-prefix':
                      'Ready to proxy settings (keys-only)'}
            for app in KO_APPS})
        zaza_model.wait_for_application_states(states=states)

    def _client_data(self, proxy):
        client, _, _ = KO_APPS[proxy]
        info = _get_relation_info(
            '{}/0'.format(client), endpoint='ceph',
            related_endpoint='client', related_application=proxy)
        return info['related-units']['{}/0'.format(proxy)]['data']

    def _mon_key(self, user):
        return _run_on_mon(
            'sudo ceph auth get-key client.{}'.format(user))

    def test_nothing_on_host(self):
        """Active without admin-key; no ceph package or conf."""
        checks = {
            "dpkg-query -W -f='${Status}' ceph 2>/dev/null "
            "| grep -q 'install ok installed'": 'ceph package installed',
            'test -e /etc/ceph/ceph.conf': '/etc/ceph/ceph.conf exists',
            'test -e /var/lib/charm/ceph-proxy-ko-a/ceph.conf':
                'ko-a charm ceph.conf exists',
            'test -e /var/lib/charm/ceph-proxy-ko-b/ceph.conf':
                'ko-b charm ceph.conf exists',
        }
        for cmd, failure in checks.items():
            result = zaza_model.run_on_unit(self.KO_MACHINE_UNIT, cmd)
            self.assertNotEqual('0', str(result.get('Code')), failure)

    @tenacity.retry(
        wait=tenacity.wait_exponential(multiplier=1, min=2, max=10),
        stop=tenacity.stop_after_attempt(10),
        reraise=True)
    def _assert_key_published(self, proxy):
        _, user, _ = KO_APPS[proxy]
        data = self._client_data(proxy)
        self.assertEqual(self._mon_key(user), data.get('key'))
        self.assertEqual('cephx', data.get('auth'))
        self.assertTrue(data.get('ceph-public-address'))

    def test_key_from_user_keys(self):
        """Plain user-keys reach the client."""
        self._assert_key_published('ceph-proxy-ko-a')

    def test_key_from_secret(self):
        """User-keys-secret reaches the client."""
        self._assert_key_published('ceph-proxy-ko-b')

    def test_keys_work(self):
        """The published keys can use the pre-created pool."""
        mons = _get_proxy_config()['monitor-hosts'].replace(' ', ',')
        for proxy in KO_APPS:
            _, user, _ = KO_APPS[proxy]
            key = self._client_data(proxy)['key']
            # Explicit --id/--key/-m override the mon's admin conf.
            # `K=...;` (with the semicolon) so $K is set before expansion.
            cmd = ('K={}; CEPH_ARGS="--id {} --key $K -m {}" '
                   'rados -p {} ls').format(key, user, mons, KO_POOL)
            result = zaza_model.run_on_leader('ceph-mon', cmd)
            self.assertEqual('0', str(result.get('Code')),
                             '{} key rejected: {}'.format(
                                 user, result.get('Stderr')))

    def test_secret_rotation(self):
        """A new secret revision re-publishes the new key."""
        _, user, _ = KO_APPS['ceph-proxy-ko-b']
        _run_on_mon('sudo ceph auth rm client.{}'.format(user))
        new_key = _create_ko_user(user)
        _juju('update-secret', KO_SECRET,
              'user-keys=client.{}:{}'.format(user, new_key))

        for attempt in tenacity.Retrying(
            wait=tenacity.wait_exponential(multiplier=2, max=32),
            reraise=True, stop=tenacity.stop_after_attempt(8),
        ):
            with attempt:
                self.assertEqual(
                    new_key, self._client_data('ceph-proxy-ko-b')['key'])

    def test_option_rotation(self):
        """A new key in plain user-keys re-publishes the new key."""
        _, user, _ = KO_APPS['ceph-proxy-ko-a']
        _run_on_mon('sudo ceph auth rm client.{}'.format(user))
        new_key = _create_ko_user(user)
        zaza_model.set_application_config(
            'ceph-proxy-ko-a',
            {'user-keys': 'client.{}:{}'.format(user, new_key)})

        for attempt in tenacity.Retrying(
            wait=tenacity.wait_exponential(multiplier=2, max=32),
            reraise=True, stop=tenacity.stop_after_attempt(8),
        ):
            with attempt:
                self.assertEqual(
                    new_key, self._client_data('ceph-proxy-ko-a')['key'])

    def test_broker_ops_refused(self):
        """Broker operations get a clear refusal, not a crash.

        Only meaningful if johnny sends a request with operations (the
        admin-key suite shows it creates johnny-metadata, so it should).
        Skips otherwise.
        """
        data = self._client_data('ceph-proxy-ko-a')
        rsp = data.get('broker-rsp-johnny-ko-a-0')
        if not rsp:
            raise unittest.SkipTest('johnny-ko-a sent no broker request')
        rsp = json.loads(rsp)
        if rsp.get('exit-code') == 0:
            raise unittest.SkipTest('johnny-ko-a sent a request without ops')
        self.assertEqual(1, rsp['exit-code'])
        self.assertIn('pools must already exist', rsp.get('stderr', ''))


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

        def _wait_for_mds_ready():
            """Wait for the MDS daemon to be running and responsive.

            After a config change, the ceph-fs charm may restart the MDS
            daemon multiple times. If restarts happen too quickly, systemd
            may hit its start-limit and refuse to restart the service.
            This function resets any failed state and ensures the daemon
            is running before attempting to query it.
            """
            reset_cmd = (
                "sudo systemctl reset-failed ceph-mds@$(hostname) 2>&1;"
                " sudo systemctl start ceph-mds@$(hostname) 2>&1;"
                " sleep 5;"
                " sudo ceph daemon mds.$(hostname) version 2>&1"
            )
            zaza_model.run_on_unit(self.TESTED_UNIT, reset_cmd)

        def _get_conf():
            """get/parse ceph daemon response into dict.

            :returns dict: Current configuration of the Ceph MDS daemon
            :rtype: dict
            """
            cmd = "sudo ceph daemon mds.$HOSTNAME config show"
            conf = zaza_model.run_on_unit(self.TESTED_UNIT, cmd)
            stdout = conf.get('Stdout', '')
            if not stdout.strip():
                _wait_for_mds_ready()
                raise json.JSONDecodeError(
                    "Empty response from ceph daemon", stdout, 0)
            return json.loads(stdout)

        @tenacity.retry(
            wait=tenacity.wait_exponential(multiplier=1, min=4, max=10),
            stop=tenacity.stop_after_attempt(10))
        def _change_conf_check(mds_config):
            """Change configs, then assert to ensure config was set.

            Doesn't return a value.
            """
            zaza_model.set_application_config('ceph-fs', mds_config)
            _wait_for_mds_ready()
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
