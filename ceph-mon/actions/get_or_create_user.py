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

import json

from charmhelpers.core.hookenv import action_get, action_fail, action_set, log
from subprocess import CalledProcessError, check_output


def get_or_create_user():
    username = action_get("username")
    client = "client.{}".format(username)
    try:
        log(f'Attempting to retrieve existing credentials for entity {client}')
        keyring = json.loads(
            check_output(["ceph", "auth", "get", client,
                          "--format=json"]).decode("utf-8")
        )
        log(f'Found existing credentials for entity {client}')
        return json.dumps(keyring, indent=2)
    except CalledProcessError:
        log(f'Credentials for entity {client} not found')
        pass
    try:
        log(f'Attempting to create new credentials for entity {client}')
        mon_caps = action_get("mon-caps")
        osd_caps = action_get("osd-caps")
        log(f'with the following mon capabilities: {mon_caps},')
        log(f'and osd capabilities: {osd_caps}.')
        keyring = json.loads(
            check_output(["ceph", "auth", "get-or-create",
                          client, "mon", mon_caps, "osd", osd_caps,
                          "--format=json"]).decode("utf-8")
        )
        log(f'New credentials for entity {client} created')
        return json.dumps(keyring, indent=2)
    except CalledProcessError as e:
        log(f'Failed to get or create credentials for entity {client}.')
        action_fail("User creation failed because of a failed process. "
                    "Ret Code: {} Message: {}".format(e.returncode, str(e)))


def main():
    action_set({"message": get_or_create_user()})


if __name__ == "__main__":
    main()
