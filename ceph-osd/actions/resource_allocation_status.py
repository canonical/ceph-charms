#!/usr/bin/env python3
#
# Copyright 2026 Canonical Ltd
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

"""Report the OSD resource allocation for this unit.

This action is strictly read-only: it queries the profile, the EPA
orchestrator and the host, and never claims CPUs, writes enforcement or
restarts OSDs.  With `dry-run=<profile>` it previews the allocation
another profile would produce, using the same calculation the charm uses
when applying one.
"""

import json
import sys

import yaml

sys.path.append('hooks')
sys.path.append('lib')

import charmhelpers.core.hookenv as hookenv  # noqa: E402

import resource_manager  # noqa: E402


def resource_allocation_status():
    """Collect and report the allocation state."""
    dry_run = hookenv.action_get('dry-run')
    output_format = hookenv.action_get('format') or 'yaml'
    try:
        report = resource_manager.status_report(dry_run=dry_run)
    except Exception as exc:
        hookenv.action_fail(
            'cannot report resource allocation: {}'.format(exc))
        return
    if output_format == 'json':
        rendered = json.dumps(report, indent=2, sort_keys=True)
    else:
        rendered = yaml.safe_dump(report, default_flow_style=False)
    hookenv.action_set({'message': rendered})


if __name__ == '__main__':
    resource_allocation_status()
