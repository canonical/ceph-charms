#!/usr/bin/python

#
# Copyright 2012 Canonical Ltd.
#
# Authors:
#  Paul Collins <paul.collins@canonical.com>
#  James Page <james.page@ubuntu.com>
#

import glob
import os
import subprocess
import shutil
import sys

import ceph
#import utils
from charmhelpers.core.hookenv import (
        log,
        ERROR,
        config,
        relation_ids,
        related_units,
        relation_get,
        relation_set,
        remote_unit,
        Hooks,
        UnregisteredHookError
        )
from charmhelpers.core.host import (
        apt_install,
        apt_update,
        filter_installed_packages,
        mkdir
        )

from utils import (
        render_template,
        configure_source,
        get_host_ip,
        get_unit_hostname
        )

hooks = Hooks()


def install_upstart_scripts():
    # Only install upstart configurations for older versions
    if ceph.get_ceph_version() < "0.55.1":
        for x in glob.glob('files/upstart/*.conf'):
            shutil.copy(x, '/etc/init/')


@hooks.hook('install')
def install():
    log('Begin install hook.')
    configure_source()
    apt_update(fatal=True)
    apt_install(packages=ceph.PACKAGES, fatal=True)
    install_upstart_scripts()
    log('End install hook.')


def emit_cephconf():
    cephcontext = {
        'auth_supported': config('auth-supported'),
        'mon_hosts': ' '.join(get_mon_hosts()),
        'fsid': config('fsid'),
        'version': ceph.get_ceph_version()
        }

    with open('/etc/ceph/ceph.conf', 'w') as cephconf:
        cephconf.write(render_template('ceph.conf', cephcontext))

JOURNAL_ZAPPED = '/var/lib/ceph/journal_zapped'


@hooks.hook('config-changed')
def config_changed():
    log('Begin config-changed hook.')

    log('Monitor hosts are ' + repr(get_mon_hosts()))

    # Pre-flight checks
    if not config('fsid'):
        log('No fsid supplied, cannot proceed.', level=ERROR)
        sys.exit(1)
    if not config('monitor-secret'):
        log('No monitor-secret supplied, cannot proceed.', level=ERROR)
        sys.exit(1)
    if config('osd-format') not in ceph.DISK_FORMATS:
        log('Invalid OSD disk format configuration specified', level=ERROR)
        sys.exit(1)

    emit_cephconf()

    e_mountpoint = config('ephemeral-unmount')
    if (e_mountpoint and
        filesystem_mounted(e_mountpoint)):
        subprocess.call(['umount', e_mountpoint])

    osd_journal = config('osd-journal')
    if (osd_journal and
        not os.path.exists(JOURNAL_ZAPPED) and
        os.path.exists(osd_journal)):
        ceph.zap_disk(osd_journal)
        with open(JOURNAL_ZAPPED, 'w') as zapped:
            zapped.write('DONE')

    for dev in config('osd-devices').split(' '):
        osdize(dev)

    # Support use of single node ceph
    if (not ceph.is_bootstrapped() and
        int(config('monitor-count')) == 1):
        bootstrap_monitor_cluster()
        ceph.wait_for_bootstrap()

    if ceph.is_bootstrapped():
        ceph.rescan_osd_devices()

    log('End config-changed hook.')


def get_mon_hosts():
    hosts = []
    hosts.append('{}:6789'.format(get_host_ip()))

    for relid in relation_ids('mon'):
        for unit in related_units(relid):
            hosts.append(
                '{}:6789'.format(get_host_ip(
                                    relation_get('private-address',
                                                 unit, relid)))
                )

    hosts.sort()
    return hosts


def update_monfs():
    hostname = get_unit_hostname()
    monfs = '/var/lib/ceph/mon/ceph-{}'.format(hostname)
    upstart = '{}/upstart'.format(monfs)
    if (os.path.exists(monfs) and
        not os.path.exists(upstart)):
        # Mark mon as managed by upstart so that
        # it gets start correctly on reboots
        with open(upstart, 'w'):
            pass


def bootstrap_monitor_cluster():
    hostname = get_unit_hostname()
    path = '/var/lib/ceph/mon/ceph-{}'.format(hostname)
    done = '{}/done'.format(path)
    upstart = '{}/upstart'.format(path)
    secret = config('monitor-secret')
    keyring = '/var/lib/ceph/tmp/{}.mon.keyring'.format(hostname)

    if os.path.exists(done):
        log('bootstrap_monitor_cluster: mon already initialized.')
    else:
        # Ceph >= 0.61.3 needs this for ceph-mon fs creation
        mkdir('/var/run/ceph', perms=0755)
        mkdir(path)
        # end changes for Ceph >= 0.61.3
        try:
            subprocess.check_call(['ceph-authtool', keyring,
                                   '--create-keyring', '--name=mon.',
                                   '--add-key={}'.format(secret),
                                   '--cap', 'mon', 'allow *'])

            subprocess.check_call(['ceph-mon', '--mkfs',
                                   '-i', hostname,
                                   '--keyring', keyring])

            with open(done, 'w'):
                pass
            with open(upstart, 'w'):
                pass

            subprocess.check_call(['start', 'ceph-mon-all-starter'])
        except:
            raise
        finally:
            os.unlink(keyring)


def reformat_osd():
    if config('osd-reformat'):
        return True
    else:
        return False


def osdize(dev):
    if not os.path.exists(dev):
        log('Path {} does not exist - bailing'.format(dev))
        return

    if (ceph.is_osd_disk(dev) and not
        reformat_osd()):
        log('Looks like {} is already an OSD, skipping.'
                       .format(dev))
        return

    if device_mounted(dev):
        log('Looks like {} is in use, skipping.'.format(dev))
        return

    cmd = ['ceph-disk-prepare']
    # Later versions of ceph support more options
    if ceph.get_ceph_version() >= "0.48.3":
        osd_format = config('osd-format')
        if osd_format:
            cmd.append('--fs-type')
            cmd.append(osd_format)
        cmd.append(dev)
        osd_journal = config('osd-journal')
        if (osd_journal and
            os.path.exists(osd_journal)):
            cmd.append(osd_journal)
    else:
        # Just provide the device - no other options
        # for older versions of ceph
        cmd.append(dev)
    subprocess.call(cmd)


def device_mounted(dev):
    return subprocess.call(['grep', '-wqs', dev + '1', '/proc/mounts']) == 0


def filesystem_mounted(fs):
    return subprocess.call(['grep', '-wqs', fs, '/proc/mounts']) == 0


@hooks.hook('mon-relation-departed',
            'mon-relation-joined')
def mon_relation():
    log('Begin mon-relation hook.')
    emit_cephconf()

    moncount = int(config('monitor-count'))
    if len(get_mon_hosts()) >= moncount:
        bootstrap_monitor_cluster()
        ceph.wait_for_bootstrap()
        ceph.rescan_osd_devices()
        notify_osds()
        notify_radosgws()
        notify_client()
    else:
        log('Not enough mons ({}), punting.'.format(
                            len(get_mon_hosts())))

    log('End mon-relation hook.')


def notify_osds():
    log('Begin notify_osds.')

    for relid in relation_ids('osd'):
        relation_set(relation_id=relid,
                     fsid=config('fsid'),
                     osd_bootstrap_key=ceph.get_osd_bootstrap_key(),
                     auth=config('auth-supported'))

    log('End notify_osds.')


def notify_radosgws():
    log('Begin notify_radosgws.')

    for relid in relation_ids('radosgw'):
        relation_set(relation_id=relid,
                     radosgw_key=ceph.get_radosgw_key(),
                     auth=config('auth-supported'))

    log('End notify_radosgws.')


def notify_client():
    log('Begin notify_client.')

    for relid in relation_ids('client'):
        units = related_units(relid)
        if len(units) > 0:
            service_name = units[0].split('/')[0]
            relation_set(relation_id=relid,
                         key=ceph.get_named_key(service_name),
                         auth=config('auth-supported'))

    log('End notify_client.')


@hooks.hook('osd-relation-joined')
def osd_relation():
    log('Begin osd-relation hook.')

    if ceph.is_quorum():
        log('mon cluster in quorum - providing fsid & keys')
        relation_set(fsid=config('fsid'),
                     osd_bootstrap_key=ceph.get_osd_bootstrap_key(),
                     auth=config('auth-supported'))
    else:
        log('mon cluster not in quorum - deferring fsid provision')

    log('End osd-relation hook.')


@hooks.hook('radosgw-relation-joined')
def radosgw_relation():
    log('Begin radosgw-relation hook.')

    # Install radosgw for admin tools
    apt_install(packages=filter_installed_packages(['radosgw']))

    if ceph.is_quorum():
        log('mon cluster in quorum - providing radosgw with keys')
        relation_set(radosgw_key=ceph.get_radosgw_key(),
                     auth=config('auth-supported'))
    else:
        log('mon cluster not in quorum - deferring key provision')

    log('End radosgw-relation hook.')


@hooks.hook('client-relation-joined')
def client_relation():
    log('Begin client-relation hook.')

    if ceph.is_quorum():
        log('mon cluster in quorum - providing client with keys')
        service_name = remote_unit().split('/')[0]
        relation_set(key=ceph.get_named_key(service_name),
                     auth=config('auth-supported'))
    else:
        log('mon cluster not in quorum - deferring key provision')

    log('End client-relation hook.')


@hooks.hook('upgrade-charm')
def upgrade_charm():
    log('Begin upgrade-charm hook.')
    emit_cephconf()
    apt_install(packages=filter_installed_packages(ceph.PACKAGES), fatal=True)
    install_upstart_scripts()
    update_monfs()
    log('End upgrade-charm hook.')


@hooks.hook('start')
def start():
    # In case we're being redeployed to the same machines, try
    # to make sure everything is running as soon as possible.
    subprocess.call(['start', 'ceph-mon-all'])
    ceph.rescan_osd_devices()


if __name__ == '__main__':
    try:
        hooks.execute(sys.argv)
    except UnregisteredHookError as e:
        log('Unknown hook {} - skipping.'.format(e))
