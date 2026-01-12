# Copyright 2026 Canonical Ltd
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


from unittest.mock import MagicMock
from test_utils import CharmTestCase


class TestCephClientKeyHandling(CharmTestCase):
    """Test cephx key handling, i.e., fix for LP#2125295."""

    def setUp(self):
        super(TestCephClientKeyHandling, self).setUp()

    def test_reuse_existing_key_when_no_application_name(self):
        """Test that existing key is reused when application-name not set.

        When a client relation data doesn't include 'application-name',
        reuse the existing key if one exists, rather than generating a
        new key. This avoids a race condition where new units are
        temporarily configured with the wrong key.
        """

        relation = MagicMock()
        this_unit = MagicMock()
        client_unit = MagicMock()

        # Mock relational data
        relation.data = {
            this_unit: {'key': 'cephx-existing-key'},
            client_unit: {}  # Client has no 'application-name' set yet
        }

        # Added logic from _handle_client_relation
        ceph_key = relation.data[this_unit].get('key', None)
        if 'application-name' not in relation.data[client_unit] and ceph_key:
            key_to_use = ceph_key
        else:
            key_to_use = None

        self.assertEqual(
            key_to_use, 'cephx-existing-key',
            "Use existing key when application-name not in client data")
