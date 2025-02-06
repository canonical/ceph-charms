#!/usr/bin/env python3
#
# Copyright 2022 Canonical Ltd
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

"""Creates a new CRUSH rule."""

import logging
import subprocess

logger = logging.getLogger(__name__)


def create_crush_rule(event) -> None:
    """Create a new CRUSH rule."""

    rule_name = event.params.get('name')
    failure_domain = event.params.get('failure-domain')
    device_class = event.params.get('device-class')

    cmd = [
        'ceph', 'osd', 'crush', 'rule',
        'create-replicated',
        rule_name,
        'default',
        failure_domain
    ]
    if device_class:
        cmd.append(device_class)
    try:
        subprocess.check_call(cmd)
    except subprocess.CalledProcessError as e:
        logger.warn(e)
        event.fail("rule creation failed due to exception")
        return

    event.set_results({'message': 'success'})
