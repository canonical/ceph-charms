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
import sys

sys.path.append('hooks')

from charmhelpers.contrib.storage.linux.ceph import Pool, pool_exists
from charmhelpers.core.hookenv import action_get, log, action_fail


def make_cache_tier():
    backer_pool = action_get("backer-pool")
    cache_pool = action_get("cache-pool")
    cache_mode = action_get("cache-mode")

    # Pre flight checks
    if not pool_exists('admin', backer_pool):
        log("Please create {} pool before calling create-cache-tier".format(
            backer_pool))
        action_fail("create-cache-tier failed. Backer pool {} must exist "
                    "before calling this".format(backer_pool))

    if not pool_exists('admin', cache_pool):
        log("Please create {} pool before calling create-cache-tier".format(
            cache_pool))
        action_fail("create-cache-tier failed. Cache pool {} must exist "
                    "before calling this".format(cache_pool))

    pool = Pool(service='admin', name=backer_pool)
    try:
        pool.add_cache_tier(cache_pool=cache_pool, mode=cache_mode)
    except CalledProcessError as err:
        log("Add cache tier failed with message: {}"
            .format(str(err)))
        action_fail("create-cache-tier failed.  Add cache tier failed with "
                    "message: {}".format(str(err)))


if __name__ == '__main__':
    make_cache_tier()
