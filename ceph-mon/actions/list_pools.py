#!/usr/bin/env python3
#
# Copyright 2016 Canonical Ltd
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
import os
import sys
from subprocess import check_output, CalledProcessError

_path = os.path.dirname(os.path.realpath(__file__))
_hooks = os.path.abspath(os.path.join(_path, "../hooks"))


def _add_path(path):
    if path not in sys.path:
        sys.path.insert(1, path)


_add_path(_hooks)


from charmhelpers.core.hookenv import (
    log,
    function_fail,
    function_get,
    function_set
)


def get_list_pools(output_format="text"):
    """Get list of Ceph pools.

    :param output_format: specify output format
    :type output_format: str
    :returns: joined list of string <pool_id> <pool_name> or
              dump list of pools with details
    :rtype: str
    """
    if output_format == "text":
        return check_output(["ceph", "--id", "admin", "osd",
                             "lspools"]).decode("UTF-8")

    ceph_osd_dump = check_output(["ceph", "--id", "admin", "osd", "dump",
                                  "--format=json"]).decode("UTF-8")
    pools = json.loads(ceph_osd_dump).get("pools", [])
    return json.dumps(pools,
                      indent=2 if output_format == "text-full" else None)


def main():
    try:
        list_pools = get_list_pools(function_get("format"))
        function_set({"message": list_pools})
    except CalledProcessError as e:
        log(e)
        function_fail("List pools failed with error: {}".format(str(e)))


if __name__ == "__main__":
    main()
