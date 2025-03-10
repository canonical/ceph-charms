# Copyright 2021 OpenStack Charmers
# See LICENSE file for licensing details.
#
# Learn more about testing at: https://juju.is/docs/sdk/testing


import unittest
import sys

sys.path.append('lib')  # noqa
sys.path.append('src')  # noqa

from unittest.mock import patch, Mock

from charm import CephNFSCharm
# from ops.model import ActiveStatus
from ops.testing import Harness

with patch('charmhelpers.core.host_factory.ubuntu.cmp_pkgrevno',
           Mock(return_value=1)):
    import charm


class CharmTestCase(unittest.TestCase):

    def setUp(self, obj, patches):
        super().setUp()
        self.patches = patches
        self.obj = obj
        self.patch_all()

    def patch(self, method):
        _m = patch.object(self.obj, method)
        mock = _m.start()
        self.addCleanup(_m.stop)
        return mock

    def patch_all(self):
        for method in self.patches:
            setattr(self, method, self.patch(method))


class _CephNFSCharm(CephNFSCharm):

    @staticmethod
    def get_bluestore_compression():
        return {}


class TestCephNFSCharmBase(CharmTestCase):

    PATCHES = [
        'ch_templating',
        'os',
        'subprocess',
    ]

    def setUp(self):
        super().setUp(charm, self.PATCHES)
        self.harness = Harness(
            _CephNFSCharm,
        )
        self.addCleanup(self.harness.cleanup)

    def test_init(self):
        self.harness.begin()
        self.assertFalse(self.harness.charm._stored.is_started)
