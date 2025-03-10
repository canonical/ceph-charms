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

from charmhelpers.core.hookenv import action_get, action_fail, action_set, log
from subprocess import CalledProcessError, check_output, STDOUT


def delete_user():
    username = action_get("username")
    client = "client.{}".format(username)
    try:
        log(f'Attempting to delete credentials for entity {client}.')
        output = check_output(['ceph', 'auth', 'del', client],
                              stderr=STDOUT).decode("utf-8")
        return output
    except CalledProcessError as e:
        log(f'Failed to delete credentials for entity {client}.')
        action_fail("User creation failed because of a failed process. "
                    "Ret Code: {} Message: {}".format(e.returncode, str(e)))


def main():
    action_set({"message": delete_user()})


if __name__ == "__main__":
    main()
