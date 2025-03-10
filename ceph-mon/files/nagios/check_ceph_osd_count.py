#!/usr/bin/env python3

# Copyright (C) 2021 Canonical
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
import time


EXIT_OK = 0
EXIT_WARN = 1
EXIT_CRIT = 2
EXIT_UNKNOWN = 3
EXIT_CODE_TEXT = ["OK", "WARN", "CRITICAL", "UNKNOWN"]

CURRENT_OSD_COUNT_FILE = "/var/lib/nagios/current-ceph-osd-count.json"


class CriticalError(Exception):
    """This indicates a critical error."""


def check_file_freshness(filename, newer_than=3600):
    """Check a file exists, is readable and is newer than <n> seconds.

    :param filename: The filename to check
    :type filename: str
    :param newer_than: The file should be newer than n seconds, default 3600
    :type: newer_than: int
    :raises CriticalError: If file is not readable or older then <n> seconds
    """
    # First check the file exists and is readable
    if not os.path.exists(filename):
        raise CriticalError("%s: does not exist." % (filename))
    if os.access(filename, os.R_OK) == 0:
        raise CriticalError("%s: is not readable." % (filename))

    # Then ensure the file is up-to-date enough
    mtime = os.stat(filename).st_mtime
    last_modified = time.time() - mtime
    if last_modified > newer_than:
        raise CriticalError("%s: was last modified on %s and is too old "
                            "(> %s seconds)."
                            % (filename, time.ctime(mtime), newer_than))
    if last_modified < 0:
        raise CriticalError("%s: was last modified on %s which is in the "
                            "future."
                            % (filename, time.ctime(mtime)))


def check_ceph_osd_count(host_osd_count_report):

    with open(host_osd_count_report, "r") as f:
        expected_osd_map = json.load(f)

    current_osd_map = get_osd_tree()

    exit_code = EXIT_OK
    err_msgs = []
    for host, osd_list in expected_osd_map.items():
        if host not in current_osd_map:
            err_msgs.append("Missing host {}".format(host))
            current_osd_map[host] = {}

        if len(osd_list) <= len(current_osd_map[host]):
            continue

        missing_osds = list(set(osd_list) - set(current_osd_map[host]))
        if missing_osds:
            osd_ids = [str(osd) for osd in missing_osds]
            err_msgs.append("Missing osds on "
                            "{}: {}".format(host,
                                            ", ".join(osd_ids)))
            exit_code = EXIT_CRIT

    return (exit_code, err_msgs)


def get_osd_tree():
    """Read CURRENT_OSD_COUNT_FILE to get the host osd map.

    :return: The map of node and osd ids.
    :rtype: Dict[str: List[str]]
    """
    check_file_freshness(CURRENT_OSD_COUNT_FILE)
    with open(CURRENT_OSD_COUNT_FILE, "r") as f:
        current_osd_counts = json.load(f)

    host_osd_map = {}
    for node in current_osd_counts["nodes"]:
        if node["type"] != "host":
            continue

        host_osd_map[node["name"]] = node["children"]

    return host_osd_map


if __name__ == "__main__":
    host_osd_report = sys.argv[1]
    if not os.path.isfile(host_osd_report):
        print("UNKNOWN: report file missing: {}".format(host_osd_report))
        sys.exit(EXIT_UNKNOWN)

    (exit_code, err_msgs) = check_ceph_osd_count(host_osd_report)
    print("{} {}".format(EXIT_CODE_TEXT[exit_code],
                         ", ".join(err_msgs)))
    sys.exit(exit_code)
