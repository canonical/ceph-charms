# Copyright 2024 Canonical Ltd
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

from charms import reactive

import charmhelpers.core as ch_core

from charmhelpers.core.hookenv import (
    service_name,
    config)

import charms_openstack.bus
import charms_openstack.charm as charm

import os
import subprocess


charms_openstack.bus.discover()


charm.use_defaults(
    'charm.installed',
    'config.changed',
    'config.rendered',
    'upgrade-charm',
    'update-status',
)


def _read_keyring_key(keyring_path):
    """Read the key value from a Ceph keyring file.

    Ceph keyring files have the format:
        [mds.hostname]
            key = AQD...==
            caps ...

    :param keyring_path: Path to the keyring file
    :type keyring_path: str
    :returns: The key value if found, None otherwise
    :rtype: str or None
    """
    if not os.path.exists(keyring_path):
        return None

    try:
        with open(keyring_path, 'r') as f:
            for line in f:
                line = line.strip()
                if line.startswith('key') and '=' in line:
                    # Format: "key = AQD...=="
                    return line.split('=', 1)[1].strip()
    except (IOError, OSError) as e:
        ch_core.hookenv.log('Failed to read keyring file {}: {}'.format(
            keyring_path, str(e)), level=ch_core.hookenv.WARNING)
        return None

    return None


@reactive.when_none('charm.paused', 'is-update-status-hook')
@reactive.when('ceph-mds.pools.available')
def config_changed():
    ceph_mds = reactive.endpoint_from_flag('ceph-mds.pools.available')
    with charm.provide_charm_instance() as cephfs_charm:
        host = cephfs_charm.hostname
        keyring_path = '/var/lib/ceph/mds/ceph-%s/keyring' % host

        # Read the existing key before updating the keyring
        old_key = _read_keyring_key(keyring_path)

        # Get the new key and configure the keyring
        new_key = ceph_mds.mds_key()
        cephfs_charm.configure_ceph_keyring(new_key)
        cephfs_charm.render_with_interfaces([ceph_mds])

        if reactive.is_flag_set('config.changed.source'):
            # update system source configuration and check for upgrade
            cephfs_charm.install()
            cephfs_charm.upgrade_if_available([ceph_mds])
            reactive.clear_flag('config.changed.source')

        reactive.set_flag('cephfs.configured')
        reactive.set_flag('config.rendered')
        cephfs_charm.assess_status()

        # Only restart the MDS service if the key actually changed.
        if old_key is not None and old_key != new_key:
            svc = 'ceph-mds@%s.service' % host
            ch_core.hookenv.log('MDS key has changed, restarting service')
            try:
                # Reset the failure count first, as the service may fail
                # to come up due to the way the restart-map is handled.
                subprocess.check_call(['sudo', 'systemctl',
                                       'reset-failed', svc])
                subprocess.check_call(['sudo', 'systemctl', 'restart', svc])
            except subprocess.CalledProcessError as exc:
                # The service can be temporarily masked when booting, so
                # skip that class of errors.
                ch_core.hookenv.log('Failed to restart MDS service: %s' %
                                    str(exc))


@reactive.when_none('charm.paused', 'is-update-status-hook')
@reactive.when('ceph-mds.connected')
def storage_ceph_connected(ceph):
    ceph_mds = reactive.endpoint_from_flag('ceph-mds.connected')
    ceph_mds.announce_mds_name()
    service = service_name()
    weight = config('ceph-pool-weight')
    replicas = config('ceph-osd-replication-count')

    if config('rbd-pool-name'):
        pool_name = config('rbd-pool-name')
    else:
        pool_name = "{}_data".format(service)

    # The '_' rather than '-' in the default pool name
    # maintains consistency with previous versions of the
    # charm but is inconsistent with ceph-client charms.
    metadata_pool_name = (
        config('metadata-pool') or
        "{}_metadata".format(service)
    )
    # Metadata sizing is approximately 20% of overall data weight
    # https://ceph.io/planet/cephfs-ideal-pg-ratio-between-metadata-and-data-pools/
    metadata_weight = weight * 0.20
    # Resize data pool weight to accomodate metadata weight
    weight = weight - metadata_weight
    extra_pools = []

    bluestore_compression = None
    with charm.provide_charm_instance() as cephfs_charm:
        # TODO: move this whole method into the charm class and add to the
        # common pool creation logic in charms.openstack. For now we reuse
        # the common bluestore compression wrapper here.
        try:
            bluestore_compression = cephfs_charm._get_bluestore_compression()
        except ValueError as e:
            ch_core.hookenv.log('Invalid value(s) provided for Ceph BlueStore '
                                'compression: "{}"'
                                .format(str(e)))

    if config('pool-type') == 'erasure-coded':
        # General EC plugin config
        plugin = config('ec-profile-plugin')
        technique = config('ec-profile-technique')
        device_class = config('ec-profile-device-class')
        bdm_k = config('ec-profile-k')
        bdm_m = config('ec-profile-m')
        # LRC plugin config
        bdm_l = config('ec-profile-locality')
        crush_locality = config('ec-profile-crush-locality')
        # SHEC plugin config
        bdm_c = config('ec-profile-durability-estimator')
        # CLAY plugin config
        bdm_d = config('ec-profile-helper-chunks')
        scalar_mds = config('ec-profile-scalar-mds')
        # Weight for EC pool
        ec_pool_weight = config('ec-pool-weight')
        # Profile name
        profile_name = (
            config('ec-profile-name') or "{}-profile".format(service)
        )
        # Create erasure profile
        ceph_mds.create_erasure_profile(
            name=profile_name,
            k=bdm_k, m=bdm_m,
            lrc_locality=bdm_l,
            lrc_crush_locality=crush_locality,
            shec_durability_estimator=bdm_c,
            clay_helper_chunks=bdm_d,
            clay_scalar_mds=scalar_mds,
            device_class=device_class,
            erasure_type=plugin,
            erasure_technique=technique
        )

        # Create EC data pool
        ec_pool_name = 'ec_{}'.format(pool_name)

        # NOTE(fnordahl): once we deprecate Python 3.5 support we can do
        # the unpacking of the BlueStore compression arguments as part of
        # the function arguments. Until then we need to build the dict
        # prior to the function call.
        kwargs = {
            'name': ec_pool_name,
            'erasure_profile': profile_name,
            'weight': ec_pool_weight,
            'app_name': ceph_mds.ceph_pool_app_name,
            'allow_ec_overwrites': True,
        }
        if bluestore_compression:
            kwargs.update(bluestore_compression)
        ceph_mds.create_erasure_pool(**kwargs)

        # NOTE(fnordahl): once we deprecate Python 3.5 support we can do
        # the unpacking of the BlueStore compression arguments as part of
        # the function arguments. Until then we need to build the dict
        # prior to the function call.
        kwargs = {
            'name': pool_name,
            'weight': weight,
            'app_name': ceph_mds.ceph_pool_app_name,
        }
        if bluestore_compression:
            kwargs.update(bluestore_compression)
        ceph_mds.create_replicated_pool(**kwargs)
        ceph_mds.create_replicated_pool(
            name=metadata_pool_name,
            weight=metadata_weight,
            app_name=ceph_mds.ceph_pool_app_name
        )
        extra_pools = [ec_pool_name]
    else:
        # NOTE(fnordahl): once we deprecate Python 3.5 support we can do
        # the unpacking of the BlueStore compression arguments as part of
        # the function arguments. Until then we need to build the dict
        # prior to the function call.
        kwargs = {
            'name': pool_name,
            'replicas': replicas,
            'weight': weight,
            'app_name': ceph_mds.ceph_pool_app_name,
        }
        if bluestore_compression:
            kwargs.update(bluestore_compression)
        ceph_mds.create_replicated_pool(**kwargs)
        ceph_mds.create_replicated_pool(
            name=metadata_pool_name,
            replicas=replicas,
            weight=metadata_weight,
            app_name=ceph_mds.ceph_pool_app_name)
    ceph_mds.request_cephfs(service, extra_pools=extra_pools)
