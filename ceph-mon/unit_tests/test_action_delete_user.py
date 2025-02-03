# Copyright 2022 Canonical Ltd
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

"""Tests for delete_user action."""

from actions import delete_user
from test_utils import CharmTestCase


class DeleteUserTestCase(CharmTestCase):
    _stderr = b"""updated"""

    def setUp(self):
        super(DeleteUserTestCase, self).setUp(
            delete_user, ["check_output", "action_get", "action_fail",
                          "action_set", "log"])
        self.action_get.return_value = "sandbox"  # username=sandbox
        self.check_output.return_value = self._stderr

    def test_delete_user(self):
        """Test getting status updated."""
        self.user = None

        def _action_set(message):
            self.user = message["message"]
        self.action_set.side_effect = _action_set
        delete_user.main()
        self.action_get.assert_called_once_with("username")
        self.assertEqual(self.user, "updated")
