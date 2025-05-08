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

from subprocess import CalledProcessError

__author__ = 'chris'
import sys

sys.path.append('hooks')

from charmhelpers.contrib.storage.linux.ceph import remove_erasure_profile
from charmhelpers.core.hookenv import action_get, log, action_fail


def delete_erasure_profile():
    name = action_get("name")

    try:
        remove_erasure_profile(service='admin', profile_name=name)
    except CalledProcessError as e:
        log("Remove erasure profile failed with error {}".format(str(e)),
            level="ERROR")
        action_fail("Remove erasure profile failed with error: {}"
                    .format(str(e)))


if __name__ == '__main__':
    delete_erasure_profile()
