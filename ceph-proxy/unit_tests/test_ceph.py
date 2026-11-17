import collections
import subprocess
import unittest

from unittest import mock

import ceph


class CephTestCase(unittest.TestCase):
    def setUp(self):
        super(CephTestCase, self).setUp()

    @staticmethod
    def populated_config_side_effect(key):
        return {
            'user-keys':
            'client.cinder-ceph:AQAij2tbMNjMOhAAqInpXQLFrltDgmYid6KXbg== '
            'client.glance:AQCnjmtbuEACMxAA7joUmgLIGI4/3LKkPzUy8g== '
            'client.gnocchi:AQDk7qJb0csAFRAAQqPU6HchVW3PT6ymgXdI/A== '
            'client.nova-compute-kvm:'
            'AQBkjmtb1hWxLxAA3UhxSblgFSCtHVoZ8W6rNQ== '
            'client.radosgw.gateway:'
            'AQBljmtb65mrHhAAGy9VRkfsatWVLb9EpoWDfw==',
            'admin-user': 'client.myadmin'
        }.get(key)

    @staticmethod
    def empty_config_side_effect(key):
        return {
            'user-keys': '',
            'admin-user': 'client.myadmin'
        }.get(key)

    @mock.patch('ceph.config')
    def test_config_user_key_populated(self, mock_config):
        user_name = 'glance'
        user_key = 'AQCnjmtbuEACMxAA7joUmgLIGI4/3LKkPzUy8g=='

        mock_config.side_effect = self.populated_config_side_effect
        named_key = ceph._config_user_key(user_name)
        self.assertEqual(user_key, named_key)

    @mock.patch('ceph.config')
    def test_config_user_key_supports_entity_with_multiple_dots(
            self,
            mock_config):
        user_name = 'radosgw.gateway'
        user_key = 'AQBljmtb65mrHhAAGy9VRkfsatWVLb9EpoWDfw=='

        mock_config.side_effect = self.populated_config_side_effect
        named_key = ceph._config_user_key(user_name)
        self.assertEqual(user_key, named_key)

    @mock.patch('ceph.config')
    def test_config_user_key_supports_unqualified_username(self, mock_config):
        user_name = 'glance'
        user_key = 'AQCnjmtbuEACMxAA7joUmgLIGI4/3LKkPzUy8g=='

        mock_config.side_effect = lambda key: {
            'user-keys': 'glance:{} client.cinder-ceph:other'.format(user_key),
            'admin-user': 'client.myadmin',
        }.get(key)
        named_key = ceph._config_user_key(user_name)
        self.assertEqual(user_key, named_key)

    @mock.patch('ceph.log')
    @mock.patch('ceph.config')
    def test_config_user_key_ignores_malformed_entries(
            self,
            mock_config,
            mock_log):
        user_name = 'glance'
        user_key = 'AQCnjmtbuEACMxAA7joUmgLIGI4/3LKkPzUy8g=='

        mock_config.side_effect = lambda key: {
            'user-keys': (
                'malformed-entry '
                'client.radosgw.gateway '
                'client.glance:{}'
            ).format(user_key),
            'admin-user': 'client.myadmin',
        }.get(key)
        named_key = ceph._config_user_key(user_name)
        self.assertEqual(user_key, named_key)
        self.assertTrue(mock_log.called)

    @mock.patch('ceph.config')
    def test_config_empty_user_key(self, mock_config):
        user_name = 'cinder-ceph'

        mock_config.side_effect = self.empty_config_side_effect
        named_key = ceph._config_user_key(user_name)
        self.assertEqual(named_key, None)

    @mock.patch('ceph.get_named_entity_key')
    @mock.patch('ceph.config')
    def test_get_named_key_falls_back_when_user_keys_are_malformed(
            self, mock_config, mock_get_named_entity_key):
        user_name = 'cinder-ceph'
        expected_key = 'generated-key'

        mock_config.side_effect = lambda key: {
            'user-keys': 'broken-entry client.glance',
            'admin-user': 'client.myadmin',
        }.get(key)
        mock_get_named_entity_key.return_value = expected_key

        named_key = ceph.get_named_key(user_name)

        self.assertEqual(expected_key, named_key)
        mock_get_named_entity_key.assert_called_once_with(
            user_name, None, None, entity='client')

    @mock.patch('ceph.get_named_entity_key')
    @mock.patch('ceph.config')
    def test_get_named_key_prefers_configured_user_key(
            self, mock_config, mock_get_named_entity_key):
        user_name = 'glance'
        expected_key = 'AQCnjmtbuEACMxAA7joUmgLIGI4/3LKkPzUy8g=='

        mock_config.side_effect = self.populated_config_side_effect
        named_key = ceph.get_named_key(user_name)

        self.assertEqual(expected_key, named_key)
        mock_get_named_entity_key.assert_not_called()

    @mock.patch.object(ceph, 'ceph_user')
    @mock.patch('subprocess.check_output')
    @mock.patch('ceph.config')
    def test_get_named_key_new(self, mock_config, mock_check_output,
                               mock_ceph_user):
        mock_ceph_user.return_value = 'ceph'
        user_name = 'cinder-ceph'
        expected_key = 'AQCnjmtbuEACMxAA7joUmgLIGI4/3LKkPzUy8g=='
        expected_output = ('[client.testuser]\n        key = {}'
                           .format(expected_key))

        def check_output_side_effect(cmd):
            if 'get-or-create' in cmd:
                return expected_output.encode('utf-8')
            else:
                raise subprocess.CalledProcessError(1, "")

        mock_config.side_effect = self.empty_config_side_effect
        mock_check_output.side_effect = check_output_side_effect
        named_key = ceph.get_named_key(user_name)

        self.assertEqual(expected_key, named_key)

    @mock.patch('subprocess.check_output')
    @mock.patch('ceph.get_unit_hostname')
    @mock.patch('ceph.ceph_user')
    @mock.patch('ceph.config')
    def test_get_named_key_existing(self, mock_config, mock_ceph_user,
                                    mock_get_unit_hostname, mock_check_output):
        user_name = 'cinder-ceph'
        expected_key = 'AQCnjmtbuEACMxAA7joUmgLIGI4/3LKkPzUy8g=='
        expected_output = ('[client.testuser]\n        key = {}'
                           .format(expected_key))
        caps = collections.OrderedDict([('mon', ['allow rw']),
                                        ('osd', ['allow rwx'])])
        ceph_user = 'ceph'
        ceph_proxy_host = 'cephproxy'
        mock_get_unit_hostname.return_value = ceph_proxy_host

        mock_check_output.return_value = expected_output.encode('utf-8')
        mock_config.side_effect = self.empty_config_side_effect
        mock_ceph_user.return_value = ceph_user
        named_key = ceph.get_named_key(user_name, caps)
        self.assertEqual(named_key, expected_key)


class UserKeysSecretTestCase(unittest.TestCase):
    URI = 'secret:d0erdgfmp25c762i8np0'
    GLANCE_KEY = 'AQCnjmtbuEACMxAA7joUmgLIGI4/3LKkPzUy8g=='

    def _config(self, **values):
        return lambda key: values.get(key.replace('-', '_'))

    @mock.patch('subprocess.check_output')
    @mock.patch('ceph.config')
    def test_user_key_from_secret(self, mock_config, mock_check_output):
        mock_config.side_effect = self._config(user_keys_secret=self.URI)
        mock_check_output.return_value = 'client.glance:{}\n'.format(
            self.GLANCE_KEY)
        self.assertEqual(self.GLANCE_KEY, ceph._config_user_key('glance'))
        mock_check_output.assert_called_once_with(
            ['secret-get', '--refresh', self.URI, 'user-keys'],
            universal_newlines=True)

    @mock.patch('subprocess.check_output')
    @mock.patch('ceph.config')
    def test_user_keys_merges_option_and_secret(self, mock_config,
                                                mock_check_output):
        mock_config.side_effect = self._config(
            user_keys='client.cinder:AAA', user_keys_secret=self.URI)
        mock_check_output.return_value = 'client.glance:BBB'
        self.assertEqual('client.cinder:AAA client.glance:BBB',
                         ceph.user_keys())

    @mock.patch('subprocess.check_output')
    @mock.patch('ceph.config')
    def test_no_secret_runs_no_command(self, mock_config, mock_check_output):
        mock_config.side_effect = self._config(user_keys='client.cinder:AAA')
        self.assertEqual('client.cinder:AAA', ceph.user_keys())
        mock_check_output.assert_not_called()

    @mock.patch('ceph.log')
    @mock.patch('subprocess.check_output')
    @mock.patch('ceph.config')
    def test_unreadable_secret(self, mock_config, mock_check_output,
                               mock_log):
        mock_config.side_effect = self._config(user_keys_secret=self.URI)
        mock_check_output.side_effect = subprocess.CalledProcessError(
            1, 'secret-get', output='client.glance:SECRET')
        with self.assertRaises(ceph.UserKeysSecretError):
            ceph.user_keys()
        self.assertIsNone(ceph._config_user_key('glance'))
        for call in mock_log.call_args_list:
            self.assertNotIn('SECRET', str(call))
