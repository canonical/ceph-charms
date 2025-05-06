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

from actions import change_osd_weight as action
from unittest import mock
from test_utils import CharmTestCase


class ReweightTestCase(CharmTestCase):
    """Run tests for action."""

    def setUp(self):
        """Init mocks for test cases."""
        super(ReweightTestCase, self).setUp(
            action, ["function_get", "function_fail"]
        )

    @mock.patch("actions.change_osd_weight.reweight_osd")
    def test_reweight_osd(self, _reweight_osd):
        """Test reweight_osd action has correct calls."""
        _reweight_osd.return_value = True
        osd_num = 4
        new_weight = 1.2
        action.crush_reweight(osd_num, new_weight)
        print(_reweight_osd.calls)
        _reweight_osd.assert_has_calls([mock.call("4", "1.2")])
