# Copyright 2018 Canonical Ltd
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
import socket
import subprocess

import charms.reactive as reactive

import charms_openstack.charm
import charms_openstack.adapters
import charms_openstack.plugins

import charmhelpers.core as ch_core
import charmhelpers.contrib.storage.linux.ceph as ch_ceph


class CephRBDMirrorCharmRelationAdapters(
        charms_openstack.adapters.OpenStackRelationAdapters):
    relation_adapters = {
        'ceph_local': charms_openstack.plugins.CephRelationAdapter,
        'ceph_remote': charms_openstack.plugins.CephRelationAdapter,
    }


class CephRBDMirrorCharm(charms_openstack.plugins.CephCharm):
    # We require Ceph 12.2 Luminous or later for HA support in the Ceph
    # rbd-mirror daemon.  Luminous appears in UCA at pike.
    release = 'pike'
    name = 'ceph-rbd-mirror'
    python_version = 3
    packages = ['rbd-mirror']
    required_relations = ['ceph-local', 'ceph-remote']
    user = 'ceph'
    group = 'ceph'
    adapters_class = CephRBDMirrorCharmRelationAdapters
    ceph_service_name_override = 'rbd-mirror'
    ceph_key_per_unit_name = True

    def __init__(self, **kwargs):
        self.ceph_id = 'rbd-mirror.{}'.format(socket.gethostname())
        self.services = [
            'ceph-rbd-mirror@{}'.format(self.ceph_id),
        ]
        self.restart_map = {
            '/etc/ceph/ceph.conf': self.services,
            '/etc/ceph/remote.conf': self.services,
        }
        super().__init__(**kwargs)

    def eligible_pools(self, pools):
        """Filter eligible pools.

        :param pools: Dictionary with detailed pool information as provided
                      over the ``ceph-rbd-mirror`` interface provided by the
                      ``ceph-mon`` charm.
        :type pools: dict
        :returns: Dictionary with detailed pool information for pools eligible
                  for mirroring.
        :rtype: dict
        """
        eligible = dict()

        # Loop through all pools provided and only enable those pools
        # which have the rbd application enabled and does not use
        # erasure coding for the data durability in the pool.
        for pool, attrs in pools.items():
            # Skip pools which are not enabled for RBD
            if 'rbd' not in attrs.get('applications', {}):
                ch_core.hookenv.log(
                    "'{}' has been excluded due to not having rbd "
                    "applications enabled".format(pool)
                )
                continue

            # Skip pools which have an active erasure coding profile
            parameters = attrs.get('parameters', {})
            erasure_code_profile = parameters.get('erasure_code_profile', '')
            if erasure_code_profile:
                ch_core.hookenv.log(
                    "'{}' has been excluded due to erasure coding being "
                    "enabled".format(pool)
                )
                continue

            eligible[pool] = attrs

        return eligible

    def custom_assess_status_check(self):
        """Provide mirrored pool statistics through juju status."""
        if (reactive.is_flag_set('config.rendered') and
                reactive.is_flag_set('ceph-local.available') and
                reactive.is_flag_set('ceph-remote.available')):
            endpoint = reactive.endpoint_from_flag('ceph-local.available')
            try:
                stats = self.mirror_pools_summary(
                    self.eligible_pools(endpoint.pools))
            except subprocess.CalledProcessError as e:
                ch_core.hookenv.log('Unable to retrieve mirror pool status: '
                                    '"{}"'.format(e))
                return None, None
            ch_core.hookenv.log('mirror_pools_summary = "{}"'
                                .format(stats),
                                level=ch_core.hookenv.DEBUG)
            status = 'active'
            pool_msg = ''
            image_msg = ''
            for health, count in stats['pool_health'].items():
                if not pool_msg:
                    pool_msg = 'Pools '
                pool_msg += '{} ({}) '.format(health, count)

                # Disabling blocked state until
                # https://bugs.launchpad.net/charm-ceph-rbd-mirror/+bug/1879749
                # is resolved
                # if health != 'OK':
                #     status = 'blocked'
            for state, count in stats['image_states'].items():
                if not image_msg:
                    image_msg = 'Images '
                if state == 'stopped':
                    state_name = 'Primary'
                elif state == 'replaying':
                    state_name = 'Secondary'
                else:
                    state_name = state
                image_msg += '{} ({}) '.format(state_name, count)
            msg = ''
            if pool_msg:
                msg = 'Unit is ready ({})'.format(
                    pool_msg + image_msg.rstrip())
            else:
                status = 'waiting'
                msg = 'Waiting for pools to be created'
            return status, msg
        return None, None

    def _mirror_pool_info(self, pool):
        output = subprocess.check_output(['rbd', '--id', self.ceph_id,
                                          'mirror', 'pool', 'info', '--format',
                                          'json', pool],
                                         universal_newlines=True)
        return json.loads(output)

    def mirror_pool_enabled(self, pool, mode='pool'):
        return self._mirror_pool_info(pool).get('mode', None) == mode

    def mirror_pool_has_peers(self, pool):
        return len(self._mirror_pool_info(pool).get('peers', [])) > 0

    def mirror_pool_status(self, pool):
        output = subprocess.check_output(['rbd', '--id', self.ceph_id,
                                          'mirror', 'pool', 'status',
                                          '--format', 'json', '--verbose',
                                          pool],
                                         universal_newlines=True)
        return json.loads(output)

    def mirror_pools_summary(self, pools):
        stats = {}
        stats['pool_health'] = collections.defaultdict(int)
        stats['image_states'] = collections.defaultdict(int)
        for pool in pools:
            pool_stat = self.mirror_pool_status(pool)
            stats['pool_health'][pool_stat['summary']['health']] += 1
            for state, value in pool_stat['summary']['states'].items():
                stats['image_states'][state] += value
        return stats

    def mirror_pool_enable(self, pool, mode='pool'):
        base_cmd = ['rbd', '--id', self.ceph_id, 'mirror', 'pool']
        subprocess.check_call(base_cmd + ['enable', pool, mode])
        subprocess.check_call(base_cmd + ['peer', 'add', pool,
                                          'client.{}@remote'
                                          .format(self.ceph_id)])

    def pools_in_broker_request(self, rq, ops_to_check=None):
        """Extract pool names touched by a broker request.

        :param rq: Ceph Broker Request Object
        :type rq: ch_ceph.CephBrokerRq
        :param ops_to_check: Set providing which ops to check
        :type ops_to_check: Optional[Set[str]]
        :returns: Set of pool names
        :rtype: Set[str]
        """
        assert rq.api_version == 1
        ops_to_check = ops_to_check or set(('create-pool',))
        result_set = set()
        for op in rq.ops:
            if op['op'] in ops_to_check:
                result_set.add(op['name'])
        return result_set

    def pool_mirroring_mode(self, pool, broker_requests=[]):
        """Get the Ceph RBD mirroring mode for the pool.

        Checks if the pool RBD mirroring mode was explicitly set as part of
        the 'create-pool' operation into any of the given broker requests.
        If this is true, its value is returned, otherwise the default 'pool'
        mirroring mode is used.

        :param pool: Pool name
        :type pool: str
        :param broker_requests: List of broker requests
        :type broker_requests: List[ch_ceph.CephBrokerRq]
        :returns: Ceph RBD mirroring mode
        :rtype: str
        """
        default_mirroring_mode = 'pool'
        for rq in broker_requests:
            if not rq:
                continue
            assert rq.api_version == 1
            for op in rq.ops:
                if op['op'] == 'create-pool' and op['name'] == pool:
                    return op.get(
                        'rbd-mirroring-mode', default_mirroring_mode)
        return default_mirroring_mode

    def collapse_and_filter_broker_requests(self, broker_requests,
                                            allowed_ops, require_vp=None):
        """Extract allowed ops from broker requests into one collapsed request.

        :param broker_requests: List of broker requests
        :type broker_requests: List[ch_ceph.CephBrokerRq]
        :param allowed_ops: Set of ops to allow
        :type allowed_ops: Set
        :param require_vp: Map of required key-value pairs in op
        :type require_vp: Optional[Dict[str,any]]
        :returns: Collapsed broker request
        :rtype: Optional[ch_ceph.CephBrokerRq]
        """
        require_vp = require_vp or {}
        new_rq = ch_ceph.CephBrokerRq()
        for rq in broker_requests:
            assert rq['api-version'] == 1
            for op in rq['ops']:
                if op['op'] in allowed_ops:
                    for k, v in require_vp.items():
                        if k not in op or op[k] != v:
                            break
                    else:
                        new_rq.add_op(op)
        if len(new_rq.ops):
            return new_rq
