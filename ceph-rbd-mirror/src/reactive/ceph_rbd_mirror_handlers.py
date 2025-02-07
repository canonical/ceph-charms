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

import charms.reactive as reactive

import charms_openstack.bus
import charms_openstack.charm as charm

import charmhelpers.core as ch_core
import charmhelpers.contrib.storage.linux.ceph as ch_ceph


charms_openstack.bus.discover()

# Use the charms.openstack defaults for common states and hooks
charm.use_defaults(
    'charm.installed',
    'config.rendered',
    'update-status',
    'upgrade-charm')


@reactive.when_none('is-update-status-hook',
                    'ceph-local.available',
                    'ceph-remote.available')
@reactive.when('ceph-local.connected',
               'ceph-remote.connected')
def request_keys():
    with charm.provide_charm_instance() as charm_instance:
        for flag in ('ceph-local.connected', 'ceph-remote.connected'):
            endpoint = reactive.endpoint_from_flag(flag)
            ch_core.hookenv.log('Ceph endpoint "{}" connected, requesting key'
                                .format(endpoint.endpoint_name),
                                level=ch_core.hookenv.INFO)
            endpoint.request_key()
        charm_instance.assess_status()


@reactive.when_none('is-update-status-hook')
@reactive.when('config.changed',
               'ceph-local.available',
               'ceph-remote.available')
def config_changed():
    with charm.provide_charm_instance() as charm_instance:
        charm_instance.upgrade_if_available([
            reactive.endpoint_from_flag('ceph-local.available'),
            reactive.endpoint_from_flag('ceph-remote.available'),
        ])
        charm_instance.assess_status()


@reactive.when_none('is-update-status-hook')
@reactive.when('ceph-local.available',
               'ceph-remote.available')
def render_stuff(*args):
    with charm.provide_charm_instance() as charm_instance:
        for endpoint in args:
            if not endpoint.key:
                ch_core.hookenv.log('Ceph endpoint "{}" flagged available yet '
                                    'no key.  Relation is probably departing.',
                                    level=ch_core.hookenv.INFO)
                return
            ch_core.hookenv.log('Ceph endpoint "{}" available, configuring '
                                'keyring'.format(endpoint.endpoint_name),
                                level=ch_core.hookenv.INFO)
            ch_core.hookenv.log('Pools: "{}"'.format(endpoint.pools),
                                level=ch_core.hookenv.INFO)

            cluster_name = (
                'remote') if endpoint.endpoint_name == 'ceph-remote' else None
            charm_instance.configure_ceph_keyring(endpoint.key,
                                                  cluster_name=cluster_name)
        charm_instance.render_with_interfaces(args)
        reactive.set_flag('config.rendered')


@reactive.when_none('is-update-status-hook')
@reactive.when('leadership.is_leader',
               'refresh.pools',
               'ceph-local.available',
               'ceph-remote.available')
def refresh_pools():
    for endpoint in 'ceph-local', 'ceph-remote':
        endpoint = reactive.endpoint_from_name(endpoint)
        endpoint.refresh_pools()
    reactive.clear_flag('refresh.pools')


@reactive.when_none('is-update-status-hook')
@reactive.when('leadership.is_leader',
               'config.rendered',
               'ceph-local.available',
               'ceph-remote.available')
def configure_pools():
    local = reactive.endpoint_from_flag('ceph-local.available')
    remote = reactive.endpoint_from_flag('ceph-remote.available')
    with charm.provide_charm_instance() as charm_instance:
        rq = charm_instance.collapse_and_filter_broker_requests(
            local.broker_requests, set(('create-pool',)),
            require_vp={'app-name': 'rbd'})
        remote_rq = charm_instance.collapse_and_filter_broker_requests(
            remote.broker_requests, set(('create-pool',)),
            require_vp={'app-name': 'rbd'})
        pools_in_rq = charm_instance.pools_in_broker_request(
            rq) if rq else set()
        pools_in_rq |= charm_instance.pools_in_broker_request(
            remote_rq) if remote_rq else set()
        for pool, attrs in charm_instance.eligible_pools(local.pools).items():
            pool_mirroring_mode = charm_instance.pool_mirroring_mode(
                pool, [rq, remote_rq])
            mirroring_enabled = charm_instance.mirror_pool_enabled(
                pool, pool_mirroring_mode)
            has_peers = charm_instance.mirror_pool_has_peers(pool)
            if not (mirroring_enabled and has_peers):
                ch_core.hookenv.log('Enabling mirroring for pool "{}"'
                                    .format(pool),
                                    level=ch_core.hookenv.INFO)
                charm_instance.mirror_pool_enable(pool, pool_mirroring_mode)
            if (pool not in pools_in_rq and
                    'erasure_code_profile' not in attrs['parameters']):
                # A pool exists that there is no broker request for which means
                # it is a manually created pool. We will forward creation of
                # replicated pools but forwarding of manually created Erasure
                # Coded pools is not supported.
                pg_num = attrs['parameters'].get('pg_num')
                max_bytes = attrs['quota'].get('max_bytes')
                max_objects = attrs['quota'].get('max_objects')
                size = attrs['parameters'].get('size')
                ch_core.hookenv.log('Adding manually created pool "{}" to '
                                    'request.'
                                    .format(pool),
                                    level=ch_core.hookenv.INFO)
                if not rq:
                    rq = ch_ceph.CephBrokerRq()
                rq.add_op_create_replicated_pool(
                    pool,
                    replica_count=size if not size else int(size),
                    pg_num=pg_num if not pg_num else int(pg_num),
                    app_name='rbd',
                    max_bytes=max_bytes if not max_bytes else int(max_bytes),
                    max_objects=max_objects if not max_objects else int(
                        max_objects),
                )
        ch_core.hookenv.log('Request for evaluation: "{}"'
                            .format(rq),
                            level=ch_core.hookenv.DEBUG)
        if rq:
            remote.maybe_send_rq(rq)
