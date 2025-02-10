#!/usr/bin/env python3
#
# Copyright 2022 Canonical Ltd
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
import json
import yaml

from actions import list_crush_rules
from test_utils import CharmTestCase


class ListCrushRulesTestCase(CharmTestCase):
    ceph_osd_crush_rule_dump = b"""
    [
        {
            "rule_id": 0,
            "rule_name": "replicated_rule",
            "ruleset": 0,
            "type": 1,
            "min_size": 1,
            "max_size": 10,
            "steps": [
                {
                    "op": "take",
                    "item": -1,
                    "item_name": "default"
                },
                {
                    "op": "chooseleaf_firstn",
                    "num": 0,
                    "type": "host"
                },
                {
                    "op": "emit"
                }
            ]
        },
        {
            "rule_id": 1,
            "rule_name": "test-host",
            "ruleset": 1,
            "type": 1,
            "min_size": 1,
            "max_size": 10,
            "steps": [
                {
                    "op": "take",
                    "item": -1,
                    "item_name": "default"
                },
                {
                    "op": "chooseleaf_firstn",
                    "num": 0,
                    "type": "host"
                },
                {
                    "op": "emit"
                }
            ]
        },
        {
            "rule_id": 2,
            "rule_name": "test-chassis",
            "ruleset": 2,
            "type": 1,
            "min_size": 1,
            "max_size": 10,
            "steps": [
                {
                    "op": "take",
                    "item": -1,
                    "item_name": "default"
                },
                {
                    "op": "chooseleaf_firstn",
                    "num": 0,
                    "type": "chassis"
                },
                {
                    "op": "emit"
                }
            ]
        },
        {
            "rule_id": 3,
            "rule_name": "test-rack-hdd",
            "ruleset": 3,
            "type": 1,
            "min_size": 1,
            "max_size": 10,
            "steps": [
                {
                    "op": "take",
                    "item": -2,
                    "item_name": "default~hdd"
                },
                {
                    "op": "chooseleaf_firstn",
                    "num": 0,
                    "type": "rack"
                },
                {
                    "op": "emit"
                }
            ]
        }
    ]
    """

    def setUp(self):
        super(ListCrushRulesTestCase, self).setUp(
            list_crush_rules, ["check_output", "function_fail", "function_get",
                               "function_set"])
        self.function_get.return_value = "json"  # format=json
        self.check_output.return_value = self.ceph_osd_crush_rule_dump

    def test_getting_list_crush_rules_text_format(self):
        """Test getting list of crush rules in text format."""
        self.function_get.return_value = "text"
        list_crush_rules.main()
        self.function_get.assert_called_once_with("format")
        self.function_set.assert_called_once_with(
            {"message": "(0, replicated_rule),(1, test-host),"
                        "(2, test-chassis),(3, test-rack-hdd)"})

    def test_getting_list_crush_rules_json_format(self):
        """Test getting list of crush rules in json format."""
        crush_rules = self.ceph_osd_crush_rule_dump.decode("UTF-8")
        crush_rules = json.loads(crush_rules)
        self.function_get.return_value = "json"
        list_crush_rules.main()
        self.function_get.assert_called_once_with("format")
        self.function_set.assert_called_once_with(
            {"message": json.dumps(crush_rules)})

    def test_getting_list_crush_rules_yaml_format(self):
        """Test getting list of crush rules in yaml format."""
        crush_rules = self.ceph_osd_crush_rule_dump.decode("UTF-8")
        crush_rules = json.loads(crush_rules)
        self.function_get.return_value = "yaml"
        list_crush_rules.main()
        self.function_get.assert_called_once_with("format")
        self.function_set.assert_called_once_with(
            {"message": yaml.dump(crush_rules)})
