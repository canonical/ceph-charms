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

"""Removes an OSD from a cluster map.

Runs the ceph osd purge command, or earlier equivalents, removing an OSD from
the cluster map, removes its authentication key, removes the OSD from the OSD
map.
"""

from subprocess import (
    check_call,
    CalledProcessError,
)

import sys
sys.path.append('lib')
sys.path.append('hooks')


from charmhelpers.core.hookenv import (
    function_get,
    log,
    function_fail
)
from charmhelpers.core.host import cmp_pkgrevno
from charmhelpers.contrib.storage.linux import ceph
from charms_ceph.utils import get_osd_weight


def purge_osd(osd):
    """Run the OSD purge action.

    :param osd: the OSD ID to operate on
    """
    svc = 'admin'
    osd_str = str(osd)
    osd_name = "osd.{}".format(osd_str)
    current_osds = ceph.get_osds(svc)
    if osd not in current_osds:
        function_fail("OSD {} is not in the current list of OSDs".format(osd))
        return

    osd_weight = get_osd_weight(osd_name)
    if osd_weight > 0:
        function_fail("OSD has weight {}, must have zero weight before "
                      "this operation".format(osd_weight))
        return

    luminous_or_later = cmp_pkgrevno('ceph-common', '12.0.0') >= 0
    if not function_get('i-really-mean-it'):
        function_fail('i-really-mean-it is a required parameter')
        return
    if luminous_or_later:
        cmds = [
            ["ceph", "osd", "out", osd_name],
            ['ceph', 'osd', 'purge', osd_str, '--yes-i-really-mean-it']
        ]
    else:
        cmds = [
            ["ceph", "osd", "out", osd_name],
            ["ceph", "osd", "crush", "remove", "osd.{}".format(osd)],
            ["ceph", "auth", "del", osd_name],
            ['ceph', 'osd', 'rm', osd_str],
        ]
    for cmd in cmds:
        try:
            check_call(cmd)
        except CalledProcessError as e:
            log(e)
            function_fail("OSD Purge for OSD {} failed".format(osd))
            return


if __name__ == '__main__':
    osd = function_get("osd")
    purge_osd(osd)
