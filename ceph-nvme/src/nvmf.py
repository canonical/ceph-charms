#! /usr/bin/env python3
#
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

import json
import os
import sys

sys.path.append(os.path.dirname(os.path.abspath(__name__)))
import utils


def check_hugepages():
    try:
        with open(utils.HUGEPAGES, 'r') as file:
            return int(file.read()) >= 2048
    except Exception:
        return False


def main():
    xname = sys.argv[1]
    config_path = sys.argv[2]
    args = [os.path.basename(xname)]

    with open(config_path, 'r') as file:
        config = json.loads(file.read())

    if not check_hugepages():
        print("warning: running without huge pages. expect a performance hit")
        # Aim for at least 4G for the target.
        args.extend(['--no-huge', '-s', str(4096)])

    cpuset = utils.compute_cpuset(config.get('cpuset'))
    args.extend(['-m', str(hex(utils.compute_cpumask(cpuset)))])

    # Start in wait-for-rpc mode so that the proxy can configure
    # iobuf pool sizes via iobuf_set_options RPC before SPDK
    # completes initialization.  This is required because SPDK v24.05
    # does not expose iobuf pool sizing as CLI flags.
    args.append('--wait-for-rpc')

    # Replace the current process with a call to the target.
    os.execv(xname, args)


if __name__ == '__main__':
    main()
