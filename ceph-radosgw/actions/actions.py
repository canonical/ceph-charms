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

import os
import subprocess
import sys

sys.path.append('hooks/')

import multisite

from charmhelpers.core.hookenv import (
    action_fail,
    config,
    action_set,
)
from utils import (
    pause_unit_helper,
    resume_unit_helper,
    register_configs,
)


def pause(args):
    """Pause the Ceilometer services.
    @raises Exception should the service fail to stop.
    """
    pause_unit_helper(register_configs())


def resume(args):
    """Resume the Ceilometer services.
    @raises Exception should the service fail to start."""
    resume_unit_helper(register_configs())


def promote(args):
    """Promote zone associated with local RGW units to master/default"""
    zone = config('zone')
    if not zone:
        action_fail('No zone configuration set, not promoting')
        return
    try:
        multisite.modify_zone(zone,
                              default=True, master=True)
        multisite.update_period()
        action_set(
            values={'message': 'zone:{} promoted to '
                    'master/default'.format(zone)}
        )
    except subprocess.CalledProcessError as cpe:
        action_fail('Unable to promote zone:{} '
                    'to master: {}'.format(zone, cpe.output))


def readonly(args):
    """Mark zone associated with local RGW units as read only"""
    zone = config('zone')
    if not zone:
        action_fail('No zone configuration set, not marking read only')
        return
    try:
        multisite.modify_zone(zone, readonly=True)
        multisite.update_period()
        action_set(
            values={
                'message': 'zone:{} marked as read only'.format(zone)
            }
        )
    except subprocess.CalledProcessError as cpe:
        action_fail('Unable mark zone:{} '
                    'as read only: {}'.format(zone, cpe.output))


def readwrite(args):
    """Mark zone associated with local RGW units as read write"""
    zone = config('zone')
    if not zone:
        action_fail('No zone configuration set, not marking read write')
        return
    try:
        multisite.modify_zone(zone, readonly=False)
        multisite.update_period()
        action_set(
            values={
                'message': 'zone:{} marked as read write'.format(zone)
            }
        )
    except subprocess.CalledProcessError as cpe:
        action_fail('Unable mark zone:{} '
                    'as read write: {}'.format(zone, cpe.output))


def tidydefaults(args):
    """Delete default zone and zonegroup metadata"""
    zone = config('zone')
    if not zone:
        action_fail('No zone configuration set, not deleting defaults')
        return
    try:
        multisite.tidy_defaults()
        action_set(
            values={
                'message': 'default zone and zonegroup deleted'
            }
        )
    except subprocess.CalledProcessError as cpe:
        action_fail('Unable delete default zone and zonegroup'
                    ': {} - {}'.format(zone, cpe.output))


# A dictionary of all the defined actions to callables (which take
# parsed arguments).
ACTIONS = {
    "pause": pause,
    "resume": resume,
    "promote": promote,
    "readonly": readonly,
    "readwrite": readwrite,
    "tidydefaults": tidydefaults,
}


def main(args):
    action_name = os.path.basename(args[0])
    try:
        action = ACTIONS[action_name]
    except KeyError:
        return "Action %s undefined" % action_name
    else:
        try:
            action(args)
        except Exception as e:
            action_fail(str(e))


if __name__ == "__main__":
    sys.exit(main(sys.argv))
