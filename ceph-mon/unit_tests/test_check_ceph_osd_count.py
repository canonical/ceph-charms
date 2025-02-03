# Copyright 2021 Canonical Ltd
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#  http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import os
import sys
import unittest

from unittest.mock import patch, mock_open
from src.ceph_hooks import update_host_osd_count_report

os.sys.path.insert(1, os.path.join(sys.path[0], 'lib'))
os.sys.path.insert(1, os.path.join(sys.path[0], 'files/nagios'))

import check_ceph_osd_count

from charms_ceph.utils import CrushLocation


class CheckCephOsdCountTestCase(unittest.TestCase):

    @patch("check_ceph_osd_count.get_osd_tree")
    def test_check_equal_ceph_osd_trees(self, mock_get_osd_tree):
        """Check that if current and expected osd trees match return OK exit"""

        current_osd_tree = {"host1": [0]}
        mock_get_osd_tree.return_value = current_osd_tree
        expected_osd_tree = """{"host1": [0]}"""
        with patch(
            "check_ceph_osd_count.open",
            mock_open(read_data=expected_osd_tree),
        ) as file:
            (exit_code, _) = check_ceph_osd_count.check_ceph_osd_count(file)
        self.assertEqual(exit_code, check_ceph_osd_count.EXIT_OK)

        # change osd order
        current_osd_tree = {"host1": [0, 1]}
        mock_get_osd_tree.return_value = current_osd_tree
        expected_osd_tree = """{"host1": [1, 0]}"""
        with patch(
            "check_ceph_osd_count.open",
            mock_open(read_data=expected_osd_tree),
        ) as file:
            (exit_code, _) = check_ceph_osd_count.check_ceph_osd_count(file)
        self.assertEqual(exit_code, check_ceph_osd_count.EXIT_OK)

    @patch("check_ceph_osd_count.get_osd_tree")
    def test_check_missing_expected_osd(self, mock_get_osd_tree):
        """Check that missing expected osd returns appropriate exit code."""
        current_osd_tree = {"host1": [0]}
        mock_get_osd_tree.return_value = current_osd_tree
        expected_osd_tree = """{"host1": [0, 1]}"""
        with patch(
            "check_ceph_osd_count.open",
            mock_open(read_data=expected_osd_tree),
        ) as file:

            (exit_code, _) = check_ceph_osd_count.check_ceph_osd_count(file)
        self.assertEqual(exit_code, check_ceph_osd_count.EXIT_CRIT)

    @patch("check_ceph_osd_count.get_osd_tree")
    def test_check_missing_expected_host(self,
                                         mock_get_osd_tree):
        """Check that missing expected host returns appropriate exit code."""
        current_osd_tree = {"host1": [0]}
        mock_get_osd_tree.return_value = current_osd_tree
        expected_osd_tree = """{"host1": [0], "host2": [1]}"""
        with patch(
            "check_ceph_osd_count.open",
            mock_open(read_data=expected_osd_tree),
        ) as file:

            (exit_code, _) = check_ceph_osd_count.check_ceph_osd_count(file)
        self.assertEqual(exit_code, check_ceph_osd_count.EXIT_CRIT)

    @patch("check_ceph_osd_count.get_osd_tree")
    def test_check_change_osd_ids(self, mock_get_osd_tree):
        """Check that a change in osd ids (of same length) is OK."""
        current_osd_tree = {"host1": [1], "host2": [3]}
        mock_get_osd_tree.return_value = current_osd_tree
        expected_osd_tree = """{"host1": [0], "host2": [1]}"""
        with patch(
            "check_ceph_osd_count.open",
            mock_open(read_data=expected_osd_tree),
        ) as file:
            (exit_code, _) = check_ceph_osd_count.check_ceph_osd_count(file)
        self.assertEqual(exit_code, check_ceph_osd_count.EXIT_OK)

    @patch("check_ceph_osd_count.get_osd_tree")
    def test_osd_tree_current_gt_expected(self, mock_get_osd_tree):
        """Check that growing osd list is added to expected."""
        current_osd_tree = {"host1": [0, 1], "host2": [2]}
        mock_get_osd_tree.return_value = current_osd_tree
        expected_osd_tree = """{"host1": [0]}"""
        with patch(
            "check_ceph_osd_count.open",
            mock_open(read_data=expected_osd_tree),
        ) as file:
            (exit_code, _) = check_ceph_osd_count.check_ceph_osd_count(file)
        self.assertEqual(exit_code, check_ceph_osd_count.EXIT_OK)

    @patch("json.dumps")
    @patch("src.ceph_hooks.write_file")
    @patch("src.ceph_hooks.pathlib")
    @patch("charms_ceph.utils.get_osd_tree")
    def test_update_report_fresh_tree(self,
                                      mock_get_osd_tree,
                                      mock_pathlib,
                                      mock_write_file,
                                      mock_json_dumps):
        """Check that an empty expected tree triggers an update to expected."""
        new_osd_tree = [CrushLocation(0, "osd.0", osd="osd.0", host="host1"),
                        CrushLocation(1, "osd.1", osd="osd.1", host="host1")]
        new_osd_dict = {"host1": [0, 1]}
        mock_get_osd_tree.return_value = new_osd_tree

        with patch(
            "src.ceph_hooks.open",
            mock_open(read_data="{}"),
        ):
            update_host_osd_count_report()
        mock_json_dumps.assert_called_with(new_osd_dict)

    @patch("json.dumps")
    @patch("src.ceph_hooks.write_file")
    @patch("src.ceph_hooks.pathlib")
    @patch("charms_ceph.utils.get_osd_tree")
    def test_update_report_new_host(self,
                                    mock_get_osd_tree,
                                    mock_pathlib,
                                    mock_write_file,
                                    mock_json_dumps):
        """Check that adding new host adds new host to expected tree."""
        new_osd_tree = [CrushLocation(0, "osd.0", osd="osd.0", host="host1"),
                        CrushLocation(1, "osd.1", osd="osd.1", host="host1"),
                        CrushLocation(2, "osd.2", osd="osd.2", host="host2")]
        mock_get_osd_tree.return_value = new_osd_tree
        with patch(
            "src.ceph_hooks.open",
            mock_open(read_data="""{"host1": [0, 1]}"""),
        ):
            update_host_osd_count_report()
        mock_json_dumps.assert_called_with(
            {"host1": [0, 1], "host2": [2]})

    @patch("json.dumps")
    @patch("src.ceph_hooks.write_file")
    @patch("src.ceph_hooks.pathlib")
    @patch("charms_ceph.utils.get_osd_tree")
    def test_update_report_missing_host(self,
                                        mock_get_osd_tree,
                                        mock_pathlib,
                                        mock_write_file,
                                        mock_json_dumps):
        """Check that missing host is not removed from expected tree."""
        new_osd_tree = [CrushLocation(0, "osd.0", osd="osd.0", host="host1"),
                        CrushLocation(2, "osd.2", osd="osd.2", host="host1")]
        mock_get_osd_tree.return_value = new_osd_tree
        with patch(
            "src.ceph_hooks.open",
            mock_open(read_data="""{"host1": [0], "host2": [1]}"""),
        ):
            update_host_osd_count_report()
        mock_json_dumps.assert_called_with(
            {"host1": [0, 2], "host2": [1]})

    @patch("json.dumps")
    @patch("src.ceph_hooks.write_file")
    @patch("src.ceph_hooks.pathlib")
    @patch("charms_ceph.utils.get_osd_tree")
    def test_update_report_fewer_osds(self,
                                      mock_get_osd_tree,
                                      mock_pathlib,
                                      mock_write_file,
                                      mock_json_dumps):
        """Check that report isn't updated when osd list shrinks."""
        new_osd_tree = [CrushLocation(0, "osd.0", osd="osd.0", host="host1")]
        mock_get_osd_tree.return_value = new_osd_tree
        with patch(
            "src.ceph_hooks.open",
            mock_open(read_data="""{"host1": [0, 1]}"""),
        ):
            update_host_osd_count_report()
        mock_json_dumps.assert_called_with(
            {"host1": [0, 1]})

    @patch("json.dumps")
    @patch("src.ceph_hooks.write_file")
    @patch("src.ceph_hooks.pathlib")
    @patch("charms_ceph.utils.get_osd_tree")
    def test_update_report_diff_osd_ids(self,
                                        mock_get_osd_tree,
                                        mock_write_file,
                                        mock_pathlib,
                                        mock_json_dumps):
        """Check that new osdid list (of same length) becomes new expected."""
        new_osd_tree = [CrushLocation(2, "osd.2", osd="osd.2", host="host1"),
                        CrushLocation(3, "osd.3", osd="osd.3", host="host1")]
        mock_get_osd_tree.return_value = new_osd_tree
        with patch(
            "src.ceph_hooks.open",
            mock_open(read_data="""{"host1": [0, 1]}"""),
        ):
            update_host_osd_count_report()
        mock_json_dumps.assert_called_with(
            {"host1": [2, 3]})
