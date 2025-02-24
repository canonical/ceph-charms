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
from subprocess import check_output, CalledProcessError

from charmhelpers.core.hookenv import (
    ERROR,
    log,
    function_fail,
    function_get,
    function_set
)


def get_list_crush_rules(output_format="text"):
    """Get list of Ceph crush rules.

    :param output_format: specify output format
    :type output_format: str
    :returns: text: list of tuple (<rule_id> <rule_name>) or
              yaml: list of crush rules in yaml format
              json: list of crush rules in json format
    :rtype: str
    """
    crush_rules = check_output(["ceph", "--id", "admin", "osd", "crush",
                                "rule", "dump", "-f", "json"]).decode("UTF-8")
    crush_rules = json.loads(crush_rules)

    if output_format == "text":
        return ",".join(["({}, {})".format(rule["rule_id"], rule["rule_name"])
                         for rule in crush_rules])
    elif output_format == "yaml":
        return yaml.dump(crush_rules)
    else:
        return json.dumps(crush_rules)


def main():
    try:
        list_crush_rules = get_list_crush_rules(function_get("format"))
        function_set({"message": list_crush_rules})
    except CalledProcessError as error:
        log(error, ERROR)
        function_fail("List crush rules failed with error: {}".format(error))


if __name__ == "__main__":
    main()
