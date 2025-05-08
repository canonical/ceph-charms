#!/usr/bin/env python3
#
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

"""Run action to collect Ceph quorum_status output."""
import json
import sys

from subprocess import CalledProcessError

sys.path.append('hooks')

from ceph_ops import get_quorum_status
from charmhelpers.core.hookenv import function_fail, function_get, function_set

if __name__ == "__main__":
    """Run action to collect Ceph quorum_status output."""
    try:
        function_set(get_quorum_status(function_get("format")))
    except CalledProcessError as error:
        function_fail("Failed to run ceph quorum_status, {}".format(error))
    except (json.decoder.JSONDecodeErro, KeyError) as error:
        function_fail(
            "Failed to parse ceph quorum_status output. {}".format(error)
        )
