#! /usr/bin/env python3
#
# Copyright 2020 Canonical Ltd
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

"""Changes the crush weight of an OSD."""

import sys

sys.path.append("lib")
sys.path.append("hooks")

from charmhelpers.core.hookenv import function_fail, function_get, log
from charms_ceph.utils import reweight_osd


def crush_reweight(osd_num, new_weight):
    """Run reweight_osd to change OSD weight."""
    try:
        result = reweight_osd(str(osd_num), str(new_weight))
    except Exception as e:
        log(e)
        function_fail("Reweight failed due to exception")
        return

    if not result:
        function_fail("Reweight failed to complete")
        return


if __name__ == "__main__":
    osd_num = function_get("osd")
    new_weight = function_get("weight")
    crush_reweight(osd_num, new_weight)
