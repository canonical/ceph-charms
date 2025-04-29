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

import sys
import subprocess

sys.path.append('hooks')

from charmhelpers.core.hookenv import action_get, log, action_fail


def set_mon_allow_pool_delete(delete=False):
    subprocess.check_call([
        'ceph', 'tell', 'mon.*',
        'injectargs',
        '--mon-allow-pool-delete={}'.format('true' if delete else 'false')
    ])


def remove_pool():
    try:
        pool_name = action_get("name")
        set_mon_allow_pool_delete(delete=True)
        subprocess.check_call([
            'ceph', 'osd', 'pool', 'delete',
            pool_name, pool_name,
            '--yes-i-really-really-mean-it',
        ])
    except subprocess.CalledProcessError as e:
        log(e)
        action_fail("Error deleting pool: {}".format(str(e)))
    finally:
        set_mon_allow_pool_delete(delete=False)


if __name__ == '__main__':
    remove_pool()
