from mock import patch
from ceph_hooks import check_for_upgrade
from test_utils import CharmTestCase


__author__ = 'Chris Holcombe <chris.holcombe@canonical.com>'


def config_side_effect(*args):
    if args[0] == 'source':
        return 'cloud:trusty-kilo'
    elif args[0] == 'key':
        return 'key'
    elif args[0] == 'release-version':
        return 'cloud:trusty-kilo'


class UpgradeRollingTestCase(CharmTestCase):

    @patch('ceph_hooks.ceph.is_bootstrapped')
    @patch('ceph_hooks.hookenv')
    @patch('ceph_hooks.ceph.roll_monitor_cluster')
    def test_check_for_upgrade(self, roll_monitor_cluster, hookenv,
                               is_bootstrapped):
        is_bootstrapped.return_value = True
        self.test_config.set_previous('source', 'cloud:trusty-juno')
        self.test_config.set('source', 'cloud:trusty-kilo')
        hookenv.config.side_effect = self.test_config
        check_for_upgrade()

        roll_monitor_cluster.assert_called_with(
            new_version='hammer',
            upgrade_key='admin')

    @patch('ceph_hooks.ceph.is_bootstrapped')
    @patch('ceph_hooks.hookenv')
    @patch('ceph_hooks.ceph.roll_monitor_cluster')
    def test_check_for_upgrade_not_bootstrapped(self, roll_monitor_cluster,
                                                hookenv, is_bootstrapped):
        is_bootstrapped.return_value = False
        self.test_config.set_previous('source', 'cloud:trusty-juno')
        self.test_config.set('source', 'cloud:trusty-kilo')
        hookenv.config.side_effect = self.test_config
        check_for_upgrade()

        roll_monitor_cluster.assert_not_called()

    @patch('ceph_hooks.add_source')
    @patch('ceph_hooks.ceph.is_bootstrapped')
    @patch('ceph_hooks.hookenv')
    @patch('ceph_hooks.ceph.roll_monitor_cluster')
    def test_check_for_upgrade_from_pike_to_queens(self, roll_monitor_cluster,
                                                   hookenv, is_bootstrapped,
                                                   add_source):
        is_bootstrapped.return_value = True
        hookenv.config.side_effect = self.test_config
        self.test_config.set('key', 'some-key')
        self.test_config.set_previous('source', 'cloud:xenial-pike')
        self.test_config.set('source', 'cloud:xenial-queens')
        check_for_upgrade()
        roll_monitor_cluster.assert_not_called()
        add_source.assert_called_with('cloud:xenial-queens', 'some-key')

    @patch('ceph_hooks.add_source')
    @patch('ceph_hooks.ceph.is_bootstrapped')
    @patch('ceph_hooks.hookenv')
    @patch('ceph_hooks.ceph.roll_monitor_cluster')
    def test_check_for_upgrade_from_rocky_to_stein(self, roll_monitor_cluster,
                                                   hookenv, is_bootstrapped,
                                                   add_source):
        is_bootstrapped.return_value = True
        hookenv.config.side_effect = self.test_config
        self.test_config.set('key', 'some-key')
        self.test_config.set_previous('source', 'cloud:bionic-rocky')
        self.test_config.set('source', 'cloud:bionic-stein')
        check_for_upgrade()
        roll_monitor_cluster.assert_not_called()
        add_source.assert_called_with('cloud:bionic-stein', 'some-key')
