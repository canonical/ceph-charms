#!/usr/bin/env python3
# Copyright 2019 Canonical Ltd
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

import collections
import json
import os
import subprocess
import sys

# Load basic layer module from $CHARM_DIR/lib
sys.path.append('lib')
from charms.layer import basic

# setup module loading from charm venv
basic.bootstrap_charm_deps()

import charms.reactive as reactive
import charmhelpers.core as ch_core
import charms_openstack.bus
import charms_openstack.charm

# load reactive interfaces
reactive.bus.discover()
# load Endpoint based interface data
ch_core.hookenv._run_atstart()

# load charm class
charms_openstack.bus.discover()


def get_pools():
    """Get the list of pools given as parameter to perform the actions on."""
    pools = ch_core.hookenv.action_get('pools')
    if pools:
        return [p.strip() for p in pools.split(',')]
    return None


def rbd_mirror_action(args):
    """Perform RBD command on pools in local Ceph endpoint."""
    action_name = os.path.basename(args[0])
    with charms_openstack.charm.provide_charm_instance() as charm:
        ceph_local = reactive.endpoint_from_name('ceph-local')
        pools = get_pools()
        if not pools:
            pools = charm.eligible_pools(ceph_local.pools)
        result = {}
        cmd = ['rbd', '--id', charm.ceph_id, 'mirror', 'pool', action_name]
        if ch_core.hookenv.action_get('force'):
            cmd += ['--force']
        if ch_core.hookenv.action_get('verbose'):
            cmd += ['--verbose']
        output_format = ch_core.hookenv.action_get('format')
        if output_format:
            cmd += ['--format', output_format]
        for pool in pools:
            output = subprocess.check_output(cmd + [pool],
                                             stderr=subprocess.STDOUT,
                                             universal_newlines=True)
            if output_format == 'json':
                result[pool] = json.loads(output)
            else:
                result[pool] = output.rstrip()
        if output_format == 'json':
            ch_core.hookenv.action_set({'output': json.dumps(result)})
        else:
            output_str = ''
            for pool, output in result.items():
                if output_str:
                    output_str += '\n'
                output_str += '{}: {}'.format(pool, output)
            ch_core.hookenv.action_set({'output': output_str})


def refresh_pools(args):
    """Refresh list of pools from Ceph.

    This is done by updating data on relations to ceph-mons which lead to them
    updating the relation data they have with us as a response.

    Due to how the reactive framework handles publishing of relation data we
    must do this by setting a flag and runnnig the reactive handlers, emulating
    a full hook execution.
    """
    if not reactive.is_flag_set('leadership.is_leader'):
        ch_core.hookenv.action_fail('run action on the leader unit')
        return

    # set and flush flag to disk
    reactive.set_flag('refresh.pools')
    ch_core.unitdata._KV.flush()

    # run reactive handlers to deal with flag
    return reactive.main()


def resync_pools(args):
    """Force image resync on pools in local Ceph endpoint."""
    if not ch_core.hookenv.action_get('i-really-mean-it'):
        ch_core.hookenv.action_fail('Required parameter not set')
        return
    with charms_openstack.charm.provide_charm_instance() as charm:
        ceph_local = reactive.endpoint_from_name('ceph-local')
        pools = get_pools()
        if not pools:
            pools = charm.eligible_pools(ceph_local.pools)
        result = collections.defaultdict(dict)
        for pool in pools:
            # list images in pool
            output = subprocess.check_output(
                ['rbd', '--id', charm.ceph_id, '--format', 'json',
                 '-p', pool, 'ls'], universal_newlines=True)
            images = json.loads(output)
            for image in images:
                output = subprocess.check_output(
                    ['rbd', '--id', charm.ceph_id, '--format', 'json', 'info',
                     '{}/{}'.format(pool, image)], universal_newlines=True)
                image_info = json.loads(output)
                if image_info['mirroring']['state'] == 'disabled':
                    continue
                output = subprocess.check_output(
                    ['rbd', '--id', charm.ceph_id, 'mirror', 'image', 'resync',
                     '{}/{}'.format(pool, image)], universal_newlines=True)
                result[pool][image] = output.rstrip()
        output_str = ''
        for pool in result:
            for image in result[pool]:
                if output_str:
                    output_str += '\n'
                output_str += '{}/{}: {}'.format(pool, image,
                                                 result[pool][image])
        ch_core.hookenv.action_set({'output': output_str})


ACTIONS = {
    'demote': rbd_mirror_action,
    'promote': rbd_mirror_action,
    'refresh-pools': refresh_pools,
    'resync-pools': resync_pools,
    'status': rbd_mirror_action,
}


def main(args):
    action_name = os.path.basename(args[0])
    try:
        action = ACTIONS[action_name]
    except KeyError:
        return 'Action {} is undefined'.format(action_name)

    try:
        action(args)
    except Exception as e:
        ch_core.hookenv.action_fail(str(e))


if __name__ == '__main__':
    sys.exit(main(sys.argv))
