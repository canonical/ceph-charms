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

import json
import functools
import subprocess
import socket
import utils

import charmhelpers.core.hookenv as hookenv
import charmhelpers.core.decorators as decorators

RGW_ADMIN = 'radosgw-admin'


@decorators.retry_on_exception(num_retries=5, base_delay=3,
                               exc_type=subprocess.CalledProcessError)
def _check_output(cmd):
    """Logging wrapper for subprocess.check_ouput"""
    hookenv.log("Executing: {}".format(' '.join(cmd)), level=hookenv.DEBUG)
    return subprocess.check_output(cmd, stderr=subprocess.PIPE).decode('UTF-8')


@decorators.retry_on_exception(num_retries=5, base_delay=3,
                               exc_type=subprocess.CalledProcessError)
def _check_call(cmd):
    """Logging wrapper for subprocess.check_call"""
    hookenv.log("Executing: {}".format(' '.join(cmd)), level=hookenv.DEBUG)
    return subprocess.check_call(cmd)


def _call(cmd):
    """Logging wrapper for subprocess.call"""
    hookenv.log("Executing: {}".format(' '.join(cmd)), level=hookenv.DEBUG)
    return subprocess.call(cmd)


def _key_name():
    """Determine the name of the cephx key for the local unit"""
    if utils.request_per_unit_key():
        return 'rgw.{}'.format(socket.gethostname())
    else:
        return 'radosgw.gateway'


def _list(key):
    """
    Internal implementation for list_* functions

    :param key: string for required entity (zone, zonegroup, realm, user)
    :type key: str
    :return: List of specified entities found
    :rtype: list
    """
    cmd = [
        RGW_ADMIN, '--id={}'.format(_key_name()),
        key, 'list'
    ]
    try:
        result = json.loads(_check_output(cmd))
        hookenv.log("Results: {}".format(
            result),
            level=hookenv.DEBUG)
        if isinstance(result, dict):
            return result['{}s'.format(key)]
        else:
            return result
    except TypeError:
        return []


@decorators.retry_on_exception(num_retries=5, base_delay=3,
                               exc_type=ValueError)
def list_zones(retry_on_empty=False):
    """
    List zones

    :param retry_on_empty: Whether to retry if no zones are returned.
    :type retry_on_empty: bool
    :return: List of specified entities found
    :rtype: list
    :raises: ValueError
    """
    _zones = _list('zone')
    if retry_on_empty and not _zones:
        hookenv.log("No zones found", level=hookenv.DEBUG)
        raise ValueError("No zones found")
    return _zones


list_realms = functools.partial(_list, 'realm')
list_zonegroups = functools.partial(_list, 'zonegroup')
list_users = functools.partial(_list, 'user')


def create_realm(name, default=False):
    """
    Create a new RADOS Gateway Realm.

    :param name: name of realm to create
    :type name: str
    :param default: set new realm as the default realm
    :type default: boolean
    :return: realm configuration
    :rtype: dict
    """
    cmd = [
        RGW_ADMIN, '--id={}'.format(_key_name()),
        'realm', 'create',
        '--rgw-realm={}'.format(name)
    ]
    if default:
        cmd += ['--default']
    try:
        return json.loads(_check_output(cmd))
    except TypeError:
        return None


def set_default_realm(name):
    """
    Set the default RADOS Gateway Realm

    :param name: name of realm to create
    :type name: str
    """
    cmd = [
        RGW_ADMIN, '--id={}'.format(_key_name()),
        'realm', 'default',
        '--rgw-realm={}'.format(name)
    ]
    _check_call(cmd)


def create_zonegroup(name, endpoints, default=False, master=False, realm=None):
    """
    Create a new RADOS Gateway Zone Group

    :param name: name of zonegroup to create
    :type name: str
    :param endpoints: list of URLs to endpoints for zonegroup
    :type endpoints: list[str]
    :param default: set new zonegroup as the default zonegroup
    :type default: boolean
    :param master: set new zonegroup as the master zonegroup
    :type master: boolean
    :param realm: realm to use for zonegroup
    :type realm: str
    :return: zonegroup configuration
    :rtype: dict
    """
    cmd = [
        RGW_ADMIN, '--id={}'.format(_key_name()),
        'zonegroup', 'create',
        '--rgw-zonegroup={}'.format(name),
        '--endpoints={}'.format(','.join(endpoints)),
    ]
    if realm:
        cmd.append('--rgw-realm={}'.format(realm))
    if default:
        cmd.append('--default')
    if master:
        cmd.append('--master')
    try:
        return json.loads(_check_output(cmd))
    except TypeError:
        return None


def create_zone(name, endpoints, default=False, master=False, zonegroup=None,
                access_key=None, secret=None, readonly=False):
    """
    Create a new RADOS Gateway Zone

    :param name: name of zone to create
    :type name: str
    :param endpoints: list of URLs to endpoints for zone
    :type endpoints: list[str]
    :param default: set new zone as the default zone
    :type default: boolean
    :param master: set new zone as the master zone
    :type master: boolean
    :param zonegroup: zonegroup to use for zone
    :type zonegroup: str
    :param access_key: access-key to use for the zone
    :type access_key: str
    :param secret: secret to use with access-key for the zone
    :type secret: str
    :param readonly: set zone as read only
    :type: readonly: boolean
    :return: dict of zone configuration
    :rtype: dict
    """
    cmd = [
        RGW_ADMIN, '--id={}'.format(_key_name()),
        'zone', 'create',
        '--rgw-zone={}'.format(name),
        '--endpoints={}'.format(','.join(endpoints)),
    ]
    if zonegroup:
        cmd.append('--rgw-zonegroup={}'.format(zonegroup))
    if default:
        cmd.append('--default')
    if master:
        cmd.append('--master')
    if access_key and secret:
        cmd.append('--access-key={}'.format(access_key))
        cmd.append('--secret={}'.format(secret))
    cmd.append('--read-only={}'.format(1 if readonly else 0))
    try:
        return json.loads(_check_output(cmd))
    except TypeError:
        return None


def modify_zone(name, endpoints=None, default=False, master=False,
                access_key=None, secret=None, readonly=False):
    """
    Modify an existing RADOS Gateway zone

    :param name: name of zone to create
    :type name: str
    :param endpoints: list of URLs to endpoints for zone
    :type endpoints: list[str]
    :param default: set zone as the default zone
    :type default: boolean
    :param master: set zone as the master zone
    :type master: boolean
    :param access_key: access-key to use for the zone
    :type access_key: str
    :param secret: secret to use with access-key for the zone
    :type secret: str
    :param readonly: set zone as read only
    :type: readonly: boolean
    :return: zone configuration
    :rtype: dict
    """
    cmd = [
        RGW_ADMIN, '--id={}'.format(_key_name()),
        'zone', 'modify',
        '--rgw-zone={}'.format(name),
    ]
    if endpoints:
        cmd.append('--endpoints={}'.format(','.join(endpoints)))
    if access_key and secret:
        cmd.append('--access-key={}'.format(access_key))
        cmd.append('--secret={}'.format(secret))
    if master:
        cmd.append('--master')
    if default:
        cmd.append('--default')
    cmd.append('--read-only={}'.format(1 if readonly else 0))
    try:
        return json.loads(_check_output(cmd))
    except TypeError:
        return None


def update_period(fatal=True):
    """
    Update RADOS Gateway configuration period
    """
    cmd = [
        RGW_ADMIN, '--id={}'.format(_key_name()),
        'period', 'update', '--commit'
    ]
    if fatal:
        _check_call(cmd)
    else:
        _call(cmd)


def tidy_defaults():
    """
    Purge any default zonegroup and zone definitions
    """
    if ('default' in list_zonegroups() and
            'default' in list_zones()):
        cmd = [
            RGW_ADMIN, '--id={}'.format(_key_name()),
            'zonegroup', 'remove',
            '--rgw-zonegroup=default',
            '--rgw-zone=default'
        ]
        _call(cmd)
        update_period()

    if 'default' in list_zones():
        cmd = [
            RGW_ADMIN, '--id={}'.format(_key_name()),
            'zone', 'delete',
            '--rgw-zone=default'
        ]
        _call(cmd)
        update_period()

    if 'default' in list_zonegroups():
        cmd = [
            RGW_ADMIN, '--id={}'.format(_key_name()),
            'zonegroup', 'delete',
            '--rgw-zonegroup=default'
        ]
        _call(cmd)
        update_period()


def get_user_creds(username):
    cmd = [
        RGW_ADMIN, '--id={}'.format(_key_name()),
        'user', 'info',
        '--uid={}'.format(username)
    ]
    result = json.loads(_check_output(cmd))
    return (result['keys'][0]['access_key'],
            result['keys'][0]['secret_key'])


def suspend_user(username):
    """
    Suspend a RADOS Gateway user

    :param username: username of user to create
    :type username: str
    """
    if username not in list_users():
        hookenv.log(
            "Cannot suspended user {}. User not found.".format(username),
            level=hookenv.DEBUG)
        return
    cmd = [
        RGW_ADMIN, '--id={}'.format(_key_name()),
        'user', 'suspend',
        '--uid={}'.format(username)
    ]
    _check_output(cmd)
    hookenv.log(
        "Suspended user {}".format(username),
        level=hookenv.DEBUG)


def create_user(username, system_user=False):
    """
    Create a RADOS Gateway user

    :param username: username of user to create
    :type username: str
    :param system_user: Whether to grant system user role
    :type system_user: bool
    :return: access key and secret
    :rtype: (str, str)
    """
    cmd = [
        RGW_ADMIN, '--id={}'.format(_key_name()),
        'user', 'create',
        '--uid={}'.format(username),
        '--display-name=Synchronization User'
    ]
    if system_user:
        cmd.append('--system')
    try:
        result = json.loads(_check_output(cmd))
        return (result['keys'][0]['access_key'],
                result['keys'][0]['secret_key'])
    except TypeError:
        return (None, None)


def create_system_user(username):
    """
    Create a RADOS Gateway system user

    :param username: username of user to create
    :type username: str
    :return: access key and secret
    :rtype: (str, str)
    """
    create_user(username, system_user=True)


def pull_realm(url, access_key, secret):
    """
    Pull in a RADOS Gateway Realm from a master RGW instance

    :param url: url of remote rgw deployment
    :type url: str
    :param access_key: access-key for remote rgw deployment
    :type access_key: str
    :param secret: secret for remote rgw deployment
    :type secret: str
    :return: realm configuration
    :rtype: dict
    """
    cmd = [
        RGW_ADMIN, '--id={}'.format(_key_name()),
        'realm', 'pull',
        '--url={}'.format(url),
        '--access-key={}'.format(access_key),
        '--secret={}'.format(secret),
    ]
    try:
        return json.loads(_check_output(cmd))
    except TypeError:
        return None


def pull_period(url, access_key, secret):
    """
    Pull in a RADOS Gateway period from a master RGW instance

    :param url: url of remote rgw deployment
    :type url: str
    :param access_key: access-key for remote rgw deployment
    :type access_key: str
    :param secret: secret for remote rgw deployment
    :type secret: str
    :return: realm configuration
    :rtype: dict
    """
    cmd = [
        RGW_ADMIN, '--id={}'.format(_key_name()),
        'period', 'pull',
        '--url={}'.format(url),
        '--access-key={}'.format(access_key),
        '--secret={}'.format(secret),
    ]
    try:
        return json.loads(_check_output(cmd))
    except TypeError:
        return None
