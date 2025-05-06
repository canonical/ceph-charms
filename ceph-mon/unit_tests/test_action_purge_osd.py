# Copyright 2020 Canonical Ltd
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#  http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Tests for purge_osd action."""

from actions import purge_osd as action
from unittest import mock
from test_utils import CharmTestCase


class PurgeTestCase(CharmTestCase):
    """Run tests for action."""

    def setUp(self):
        """Init mocks for test cases."""
        super(PurgeTestCase, self).setUp(
            action, ["check_call", "function_get", "function_fail", "open"]
        )

    @mock.patch("actions.purge_osd.get_osd_weight")
    @mock.patch("actions.purge_osd.cmp_pkgrevno")
    @mock.patch("charmhelpers.contrib.storage.linux.ceph.get_osds")
    def test_purge_osd(self, _get_osds, _cmp_pkgrevno, _get_osd_weight):
        """Test purge_osd action has correct calls."""
        _get_osds.return_value = [0, 1, 2, 3, 4, 5]
        _cmp_pkgrevno.return_value = 1
        _get_osd_weight.return_value = 0
        osd = 4
        action.purge_osd(osd)
        cmds = [
            mock.call(["ceph", "osd", "out", "osd.4"]),
            mock.call(
                ["ceph", "osd", "purge", str(osd), "--yes-i-really-mean-it"]
            ),
        ]
        self.check_call.assert_has_calls(cmds)

    @mock.patch("actions.purge_osd.get_osd_weight")
    @mock.patch("actions.purge_osd.cmp_pkgrevno")
    @mock.patch("charmhelpers.contrib.storage.linux.ceph.get_osds")
    def test_purge_invalid_osd(
        self, _get_osds, _cmp_pkgrevno, _get_osd_weight
    ):
        """Test purge_osd action captures bad OSD string."""
        _get_osds.return_value = [0, 1, 2, 3, 4, 5]
        _cmp_pkgrevno.return_value = 1
        _get_osd_weight.return_value = 0
        osd = 99
        action.purge_osd(osd)
        self.function_fail.assert_called()

    @mock.patch("actions.purge_osd.get_osd_weight")
    @mock.patch("actions.purge_osd.cmp_pkgrevno")
    @mock.patch("charmhelpers.contrib.storage.linux.ceph.get_osds")
    def test_purge_osd_weight_high(
        self, _get_osds, _cmp_pkgrevno, _get_osd_weight
    ):
        """Test purge_osd action fails when OSD has weight >0."""
        _get_osds.return_value = [0, 1, 2, 3, 4, 5]
        _cmp_pkgrevno.return_value = 1
        _get_osd_weight.return_value = 2.5
        osd = "4"
        action.purge_osd(osd)
        self.function_fail.assert_called()
