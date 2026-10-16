#!/usr/bin/env python3

#
# Copyright 2012 Canonical Ltd.
#
# Authors:
#  Paul Collins <paul.collins@canonical.com>
#  James Page <james.page@ubuntu.com>
#

import glob
import json
import os
import shutil
import sys


_path = os.path.dirname(os.path.realpath(__file__))
_root = os.path.abspath(os.path.join(_path, '..'))
_lib = os.path.abspath(os.path.join(_path, '../lib'))


def _add_path(path):
    if path not in sys.path:
        sys.path.insert(1, path)


_add_path(_root)
_add_path(_lib)

import ceph
from charmhelpers.core.hookenv import (
    log,
    DEBUG,
    INFO,
    WARNING,
    config,
    is_leader,
    relation_ids,
    related_units,
    relation_get,
    relation_set,
    remote_unit,
    Hooks, UnregisteredHookError,
    service_name,
    status_set,)
from charmhelpers.core.host import (
    cmp_pkgrevno,
    CompareHostReleases,
    lsb_release,
    mkdir,
)
from charmhelpers.fetch import (
    apt_install,
    apt_update,
    filter_installed_packages,
    add_source
)
from charmhelpers.payload.execd import execd_preinstall
from charmhelpers.contrib.openstack.alternatives import install_alternative
from charmhelpers.contrib.openstack.utils import (
    clear_unit_paused,
    clear_unit_upgrading,
    is_unit_upgrading_set,
    set_unit_paused,
    set_unit_upgrading,
)

from charmhelpers.core.templating import render

from charms_ceph.broker import (
    process_requests
)

from utils import get_unit_hostname

hooks = Hooks()


def install_upstart_scripts():
    # Only install upstart configurations for older versions
    if cmp_pkgrevno('ceph', "0.55.1") < 0:
        for x in glob.glob('files/upstart/*.conf'):
            shutil.copy(x, '/etc/init/')


@hooks.hook('install.real')
def install():
    execd_preinstall()
    if keys_only():
        log('keys-only mode: not installing ceph packages', level=INFO)
        return
    package_install()
    install_upstart_scripts()


def ceph_packages():
    _release = lsb_release()['DISTRIB_CODENAME'].lower()
    if CompareHostReleases(_release) >= "focal":
        return ceph.PACKAGES_FOCAL
    return ceph.PACKAGES


def package_install():
    add_source(config('source'), config('key'))
    apt_update(fatal=True)
    apt_install(packages=ceph_packages(), fatal=True)


def emit_cephconf():
    cephcontext = {
        'auth_supported': config('auth-supported'),
        'mon_hosts': config('monitor-hosts'),
        'fsid': config('fsid'),
        'use_syslog': str(config('use-syslog')).lower(),
        'loglevel': config('loglevel'),
    }

    # Install ceph.conf as an alternative to support
    # co-existence with other charms that write this file
    charm_ceph_conf = "/var/lib/charm/{}/ceph.conf".format(service_name())
    mkdir(os.path.dirname(charm_ceph_conf), owner=ceph.ceph_user(),
          group=ceph.ceph_user())
    render('ceph.conf', charm_ceph_conf, cephcontext, perms=0o644)
    install_alternative('ceph.conf', '/etc/ceph/ceph.conf',
                        charm_ceph_conf, 100)

    keyring_template = 'ceph.keyring'
    keyring = 'ceph.{}.keyring'.format(config('admin-user'))
    keyring_path = '/etc/ceph/' + keyring
    ctx = {
        'admin_key': config('admin-key'),
        'admin_user': config('admin-user'),
    }
    user = ceph.ceph_user()
    render(keyring_template, keyring_path, ctx, owner=user, perms=0o600)

    keyring = 'keyring'
    keyring_path = (
        '/var/lib/ceph/mon/ceph-' +
        get_unit_hostname() +
        '/' +
        keyring)
    render('mon.keyring', keyring_path, ctx, owner=user, perms=0o600)

    notify_radosgws()
    notify_client()
    notify_cephfs_mds()


@hooks.hook('config-changed')
def config_changed():
    if keys_only():
        log('keys-only mode: not writing /etc/ceph files', level=INFO)
        notify_client()
        return
    c = config()
    if c.previous('source') != config('source') or \
        c.previous('key') != config('key') or \
        filter_installed_packages(ceph_packages()):
            package_install()
    emit_cephconf()


@hooks.hook('secret-changes')
def secret_changed():
    """Republish client keys after the user-keys-secret is rotated."""
    notify_client()


def notify_radosgws():
    for relid in relation_ids('radosgw'):
        for unit in related_units(relid):
            radosgw_relation(relid=relid, unit=unit)


def notify_client():
    for relid in relation_ids('client'):
        for unit in related_units(relid):
            client_relation_joined(relid=relid, unit=unit)


def notify_cephfs_mds():
    for relid in relation_ids('mds'):
        for unit in related_units(relid):
            mds_relation_joined(relid=relid, unit=unit)


@hooks.hook('radosgw-relation-changed')
@hooks.hook('radosgw-relation-joined')
def radosgw_relation(relid=None, unit=None):
    if not unit:
        unit = remote_unit()

    # NOTE: radosgw needs some usage OSD storage, so defer key
    #       provision until OSD units are detected.
    if ready():
        # Install radosgw for admin tools
        apt_install(packages=filter_installed_packages(['radosgw']))
        log('mon cluster in quorum and osds related '
            '- providing radosgw with keys')
        ceph_addrs = config('monitor-hosts')
        data = {
            'fsid': config('fsid'),
            'auth': config('auth-supported'),
            'ceph-public-address': ceph_addrs,
        }
        key_name = relation_get('key_name', unit=unit, rid=relid)
        if key_name:
            # New style, per unit keys
            data['{}_key'.format(key_name)] = (
                ceph.get_radosgw_key(name=key_name)
            )
        else:
            # Old style global radosgw key
            data['radosgw_key'] = ceph.get_radosgw_key()

        settings = relation_get(rid=relid, unit=unit) or {}
        """Process broker request(s)."""
        if 'broker_req' in settings:
            rsp = process_requests(settings['broker_req'])
            unit_id = unit.replace('/', '-')
            unit_response_key = 'broker-rsp-' + unit_id
            data[unit_response_key] = rsp

        log('relation_set (%s): %s' % (relid, str(data)), level=DEBUG)
        relation_set(relation_id=relid, relation_settings=data)
    else:
        log('FSID or admin key not provided, please configure them')


@hooks.hook('mds-relation-joined')
@hooks.hook('mds-relation-changed')
def mds_relation_joined(relid=None, unit=None):
    if not ready():
        log('MDS: FSID or admin key not provided, please configure them',
            level=INFO)
        return

    log('ceph-proxy config ok - providing mds client with keys')
    if not unit:
        unit = remote_unit()

    mds_name = relation_get(attribute='mds-name',
                            rid=relid, unit=unit)
    ceph_addrs = config('monitor-hosts')
    data = {
        'fsid': config('fsid'),
        'auth': config('auth-supported'),
        'ceph-public-address': ceph_addrs,
    }
    if mds_name:
        data['{}_mds_key'.format(mds_name)] = (
            ceph.get_mds_key(name=mds_name)
        )

    settings = relation_get(rid=relid, unit=unit) or {}
    if 'broker_req' in settings:
        rsp = process_requests(settings['broker_req'])
        unit_id = unit.replace('/', '-')
        unit_response_key = 'broker-rsp-' + unit_id
        data[unit_response_key] = rsp
    log('MDS: relation_set (%s): %s' % (relid, str(data)), level=DEBUG)
    relation_set(relation_id=relid, relation_settings=data)


@hooks.hook('client-relation-joined')
def client_relation_joined(relid=None, unit=None):
    if client_ready():
        service_name = None
        if relid is None:
            units = [remote_unit()]
            service_name = units[0].split('/')[0]
        else:
            units = related_units(relid)
            if len(units) > 0:
                service_name = units[0].split('/')[0]
        if unit is None:
            unit = units[0]
        if service_name is not None:
            key = client_key(service_name)
            if not key:
                log(f'keys-only mode: no user key for client.{service_name}',
                    level=WARNING)
                return
            ceph_addrs = config('monitor-hosts')
            data = {'key': ceph.get_named_key(service_name),
                    'auth': config('auth-supported'),
                    'ceph-public-address': ceph_addrs}

            settings = relation_get(rid=relid, unit=unit) or {}
            data_update = {}
            if 'broker_req' in settings:
                rsp = handle_broker_request(settings['broker_req'])
                unit_id = unit.replace('/', '-')
                unit_response_key = 'broker-rsp-' + unit_id
                data_update[unit_response_key] = rsp
            data.update(data_update)

            log('relation_set (%s): %s' % (relid, str(data)), level=DEBUG)
            relation_set(relation_id=relid,
                         relation_settings=data)
    else:
        log('FSID or admin key not provided, please configure them')


@hooks.hook('client-relation-changed')
def client_relation_changed():
    """Process broker requests from ceph client relations."""
    if client_ready():
        settings = relation_get() or {}
        if 'broker_req' in settings:
            # the request is processed only by the leader as reported by juju
            if not is_leader():
                log("Not leader - ignoring broker request", level=DEBUG)
            else:
                # client.nova-compute is created when joined
                # client.nova-compute-ceph-auth-c91ce26f which is from
                # nova-compute charm need to be created.
                # refer to charm-nova-compute
                # commit#650f3a5d511690ec27648b30f3b24532378a33a1
                # in changed hook otherwise the key will not be created
                # get_named_key is creating auth if there is no one.
                if not keys_only():
                    ceph.get_named_key(settings["application-name"])

                rsp = handle_broker_request(settings['broker_req'])
                unit_id = remote_unit().replace('/', '-')
                unit_response_key = 'broker-rsp-' + unit_id
                # broker_rsp is being left for backward compatibility,
                # unit_response_key superscedes it
                data = {
                    'broker_rsp': rsp,
                    unit_response_key: rsp,
                }
                log('relation_set: %s' % str(data), level=DEBUG)
                relation_set(relation_settings=data)
    else:
        log('FSID or admin key not provided, please configure them')


def ready():
    return config('fsid') and config('admin-key')


def keys_only():
    """No admin key: serve clients from user keys only.

    In this mode the charm installs no ceph packages, writes no /etc/ceph
    files and runs no ceph commands.
    """
    return not config('admin-key')


def client_ready():
    """Whether the ceph-client relation can be answered."""
    if not config('fsid'):
        return False
    if config('admin-key'):
        return True
    try:
        return bool(ceph.user_keys())
    except ceph.UserKeysSecretError as e:
        log(str(e), level=WARNING)
        return False


def client_key(name):
    """Key for client.<name>; never creates one in keys-only mode."""
    if keys_only():
        return ceph._config_user_key(name)
    return ceph.get_named_key(name)


KEYS_ONLY_BROKER_ERROR = (
    'keys-only mode (no admin-key): broker operations are not supported; '
    'pools must already exist')


def handle_broker_request(req):
    """Process a broker request, or refuses in keys-only mode.
    
    A request without operations (e.g. client that only wants a key)
    is answered normally; it runs no ceph commands.
    """
    if keys_only():
        try:
            parsed = json.loads(req)
        except (TypeError, ValueError):
            parsed = None
        if isinstance(parsed, dict) and parsed.get('ops'):
            rsp = {'exit-code': 1, 'stderr': KEYS_ONLY_BROKER_ERROR}
            if parsed.get('request-id'):
                rsp['request-id'] = parsed['request-id']
            log(KEYS_ONLY_BROKER_ERROR, level=WARNING)
            return json.dumps(rsp)
    return process_requests(req)


def clients_missing_keys():
    """Related client application without a user key (keys-only mode)."""
    missing = set()
    for relid in relation_ids('client'):
        for unit in related_units(relid):
            name = unit.split('/')[0]
            if not ceph._config_user_key(name):
                missing.add(name)
    return sorted(missing)


def assess_status():
    '''Assess status of current unit'''
    if is_unit_upgrading_set():
        status_set("blocked",
                   "Ready for do-release-upgrade and reboot. "
                   "Set complete when finished.")
        return

    if ready():
        status_set('active', 'Ready to proxy settings')
        return

    if config('fsid') and config('user-keys-secret'):
        try:
            ceph.user_keys()
        except ceph.UserKeysSecretError as e:
            status_set('blocked', str(e))
            return

    if client_ready():
        missing = clients_missing_keys()
        if missing:
            status_set('blocked', f'keys-only: no user key for {
                ', '.join(missing)}')
        else:
            status_set('active', 'Ready to proxy settings (keys-only)')
    else:
        status_set('blocked', 'Ensure FSID and admin-key are set')


@hooks.hook('update-status')
def update_status():
    log('Updating status.')


@hooks.hook('pre-series-upgrade')
def pre_series_upgrade():
    log("Running prepare series upgrade hook", "INFO")
    # NOTE: The Ceph packages handle the series upgrade gracefully.
    # In order to indicate the step of the series upgrade process for
    # administrators and automated scripts, the charm sets the paused and
    # upgrading states.
    set_unit_paused()
    set_unit_upgrading()


@hooks.hook('post-series-upgrade')
def post_series_upgrade():
    log("Running complete series upgrade hook", "INFO")
    # In order to indicate the step of the series upgrade process for
    # administrators and automated scripts, the charm clears the paused and
    # upgrading states.
    clear_unit_paused()
    clear_unit_upgrading()


if __name__ == '__main__':
    try:
        hooks.execute(sys.argv)
    except UnregisteredHookError as e:
        log('Unknown hook {} - skipping.'.format(e))
    assess_status()
