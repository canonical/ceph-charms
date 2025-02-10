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

"""Tests for get_or_create_user action."""

import json

from actions import get_or_create_user
from test_utils import CharmTestCase


class GetOrCreateUserTestCase(CharmTestCase):
    _keyring = b"""
        [
            {
                "entity": "client.sandbox",
                "key": "AQCnGXxiOkueGBAAsWX27MV8PNwuyMhPSzSCPg==",
                "caps": {
                    "mon": "allow r",
                    "osd": "allow r"
                }
            }
        ]"""

    def setUp(self):
        super(GetOrCreateUserTestCase, self).setUp(
            get_or_create_user, ["check_output", "action_get", "action_fail",
                                 "action_set", "log"])
        self.action_get.return_value = "sandbox"  # username=sandbox
        self.check_output.return_value = self._keyring

    def test_get_or_create_user(self):
        """Test getting resulting keyring."""
        self.user = None

        def _action_set(message):
            self.user = json.loads(message["message"])
        self.action_set.side_effect = _action_set
        get_or_create_user.main()
        self.action_get.assert_called_once_with("username")
        self.assertEqual(self.user[0]["entity"], "client.sandbox")
        self.assertEqual(
            self.user[0]["key"],
            "AQCnGXxiOkueGBAAsWX27MV8PNwuyMhPSzSCPg=="
        )
        self.assertEqual(self.user[0]["caps"]["mon"], "allow r")
        self.assertEqual(self.user[0]["caps"]["osd"], "allow r")
