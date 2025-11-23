import sys
import unittest

sys.path.append('lib')  # noqa
sys.path.append('src')  # noqa

from unittest.mock import patch, MagicMock
from ops.testing import Harness

import charm


class MockActionEvent:
    def __init__(self, params=None):
        self.params = params or {}
        self.fail = MagicMock()
        self.set_results = MagicMock()


class TestCreateShareAction(unittest.TestCase):

    def setUp(self):
        self.harness = Harness(charm.CephNFSCharm)
        self.addCleanup(self.harness.cleanup)

    def test_not_leader_fails(self):
        self.harness.begin()
        event = MockActionEvent({
            'name': 'share',
            'size': 1000,
            'allowed-ips': '10.10.0.10',
            'squash-access': 'root',
        })
        # By default the unit is not leader
        self.harness.charm.create_share_action(event)
        event.fail.assert_called_once_with(
            'Share creation needs to be run from the application leader')

    def test_invalid_squash_fails(self):
        self.harness.begin()
        self.harness.set_leader(True)
        event = MockActionEvent({
            'name': 'share',
            'size': 1000,
            'allowed-ips': '10.10.0.10',
            'squash-access': 'invalid-value',
        })
        self.harness.charm.create_share_action(event)
        event.fail.assert_called_once_with(
            'Invalid squash-access value: invalid-value')

    def test_missing_squash_defaults_to_none(self):
        """If squash-access is omitted the action should default to 'none'."""
        self.harness.begin()
        self.harness.set_leader(True)

        mock_ganesha = MagicMock()
        with patch('charm.GaneshaNFS', return_value=mock_ganesha):
            self.harness.charm.peers.trigger_reload = MagicMock()
            self.harness.charm.access_address = MagicMock(
                return_value='1.2.3.4')

            # no squash-access key provided
            params = {
                'name': 'testshare',
                'size': 5,
                'allowed-ips': '10.0.0.1',
            }
            event = MockActionEvent(params)
            self.harness.charm.create_share_action(event)

        mock_ganesha.create_share.assert_called_once_with(
            size=5,
            name='testshare',
            access_ips=['10.0.0.1'],
            squash_access='none')
        event.set_results.assert_called_once()

    def test_success_creates_share_and_sets_results(self):
        self.harness.begin()
        self.harness.set_leader(True)
        mock_ganesha = MagicMock()
        # Patch the GaneshaNFS class so the property returns our mock
        with patch('charm.GaneshaNFS', return_value=mock_ganesha):
            self.harness.charm.peers.trigger_reload = MagicMock()
            self.harness.charm.access_address = MagicMock(
                return_value='1.2.3.4')

            params = {
                'name': 'testshare',
                'size': 1000,
                'allowed-ips': '10.0.0.1,10.0.0.2',
                'squash-access': 'root',
            }
            event = MockActionEvent(params)
            self.harness.charm.create_share_action(event)

        mock_ganesha.create_share.assert_called_once_with(
            size=1000,
            name='testshare',
            access_ips=['10.0.0.1', '10.0.0.2'],
            squash_access='root')
        event.set_results.assert_called_once()
