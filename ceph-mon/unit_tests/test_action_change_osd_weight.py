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

"""Tests for reweight_osd action."""

import unittest.mock as mock
from test_utils import CharmTestCase, MockActionEvent
from ops.testing import Harness

with mock.patch('charmhelpers.contrib.hardening.harden.harden') as mock_dec:
    mock_dec.side_effect = (lambda *dargs, **dkwargs: lambda f:
                            lambda *args, **kwargs: f(*args, **kwargs))
    # src.charm imports ceph_hooks, so we need to workaround the inclusion
    # of the 'harden' decorator.
    from src.charm import CephMonCharm


class ReweightTestCase(CharmTestCase):
    """Run tests for action."""

    def setUp(self):
        self.harness = Harness(CephMonCharm)

    @mock.patch("ops_actions.change_osd_weight.ceph_utils.reweight_osd")
    def test_reweight_osd(self, _reweight_osd):
        """Test reweight_osd action has correct calls."""
        _reweight_osd.return_value = True
        self.harness.begin()
        self.harness.charm.on_change_osd_weight_action(
            MockActionEvent({'osd': 4, 'weight': 1.2}))
        _reweight_osd.assert_has_calls([mock.call("4", "1.2")])
