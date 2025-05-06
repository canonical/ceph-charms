# Copyright 2021 Canonical Ltd
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

"""Tests for the list_inconsistent_objs action."""

from actions import list_inconsistent_objs as action
from unittest import mock
from test_utils import CharmTestCase


class ListInconsistentTestCase(CharmTestCase):
    """Run tests for the action."""

    def setUp(self):
        """Init mocks for test cases."""
        super(ListInconsistentTestCase, self).setUp(
            action, ["get_health_detail", "get_rados_inconsistent"]
        )

    @mock.patch("actions.list_inconsistent_objs.get_rados_inconsistent")
    @mock.patch("actions.list_inconsistent_objs.get_health_detail")
    def test_inconsistent_empty(
        self, _get_health_detail, _get_rados_inconsistent
    ):
        """Test that the returned object is empty."""
        _get_health_detail.return_value = "nothing to see here"
        _get_rados_inconsistent.return_value = """
            {"epoch": 0, "inconsistents": {1: 1}}
        """
        ret = action.get_inconsistent_objs()
        _get_health_detail.assert_called_once()
        _get_rados_inconsistent.assert_not_called()
        self.assertEqual(len(ret), 0)
        self.assertEqual('', action.text_format(ret))

    @mock.patch("actions.list_inconsistent_objs.get_rados_inconsistent")
    @mock.patch("actions.list_inconsistent_objs.get_health_detail")
    def test_inconsistent_entry(
        self, _get_health_detail, _get_rados_inconsistent
    ):
        """Test that expected PG is in the returned value."""
        pg_id = '3.9'
        _get_health_detail.return_value = """
        pg 2.1 is active
        pg {} is active+inconsistent+clean
        """.format(pg_id)

        _get_rados_inconsistent.return_value = """{
            "epoch": 95,
            "inconsistents": [ { "errors": [ "size_mismatch" ],
                                 "object": { "locator": "", "name": "testfile",
                                             "nspace": "", "snap": "head" },
                                 "shards": [ { "data_digest": "0xa3ba020a",
                                               "errors": [ "size_mismatch" ],
                                               "omap_digest": "0xffffffff",
                                               "osd": 0, "size": 21 },
                                            { "data_digest": "0xa3ba020a",
                                              "errors": [ "size_mismatch" ],
                                              "omap_digest": "0xffffffff",
                                              "osd": 1, "size": 22 },
                                            { "data_digest": "0xa3ba020a",
                                              "errors": [],
                                              "omap_digest": "0xffffffff",
                                              "osd": 2, "size": 23 }
                                           ]}]
        }"""

        ret = action.get_inconsistent_objs()
        _get_health_detail.assert_called_once()
        _get_rados_inconsistent.assert_called()
        self.assertNotEqual(len(ret), 0)
        self.assertIn(pg_id, ret)

        js = action.json.loads(_get_rados_inconsistent.return_value)
        obj_name = js["inconsistents"][0]["object"]["name"]

        self.assertIn(obj_name, ret[pg_id])
        self.assertEqual(action.text_format(ret),
                         '{}: {}'.format(pg_id, obj_name))
