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

from subprocess import CalledProcessError
import sys

sys.path.append('hooks')

from charmhelpers.contrib.storage.linux.ceph import create_erasure_profile
from charmhelpers.core.hookenv import action_get, log, action_fail


def make_erasure_profile():
    name = action_get("name")
    plugin = action_get("plugin")
    failure_domain = action_get("failure-domain")
    device_class = action_get("device-class")
    k = action_get("data-chunks")
    m = action_get("coding-chunks")

    # jerasure requires k+m
    # isa requires k+m
    # local requires k+m+l
    # shec requires k+m+c

    if plugin == "jerasure":
        try:
            create_erasure_profile(service='admin',
                                   erasure_plugin_name=plugin,
                                   profile_name=name,
                                   data_chunks=k,
                                   coding_chunks=m,
                                   failure_domain=failure_domain,
                                   device_class=device_class)
        except CalledProcessError as e:
            log(e)
            action_fail("Create erasure profile failed with "
                        "message: {}".format(str(e)))
    elif plugin == "isa":
        try:
            create_erasure_profile(service='admin',
                                   erasure_plugin_name=plugin,
                                   profile_name=name,
                                   data_chunks=k,
                                   coding_chunks=m,
                                   failure_domain=failure_domain,
                                   device_class=device_class)
        except CalledProcessError as e:
            log(e)
            action_fail("Create erasure profile failed with "
                        "message: {}".format(str(e)))
    elif plugin == "lrc":
        locality_chunks = action_get("locality-chunks")
        crush_locality = action_get('crush-locality')
        try:
            create_erasure_profile(service='admin',
                                   erasure_plugin_name=plugin,
                                   profile_name=name,
                                   data_chunks=k,
                                   coding_chunks=m,
                                   locality=locality_chunks,
                                   crush_locality=crush_locality,
                                   failure_domain=failure_domain,
                                   device_class=device_class)
        except CalledProcessError as e:
            log(e)
            action_fail("Create erasure profile failed with "
                        "message: {}".format(str(e)))
    elif plugin == "shec":
        c = action_get("durability-estimator")
        try:
            create_erasure_profile(service='admin',
                                   erasure_plugin_name=plugin,
                                   profile_name=name,
                                   data_chunks=k,
                                   coding_chunks=m,
                                   durability_estimator=c,
                                   failure_domain=failure_domain,
                                   device_class=device_class)
        except CalledProcessError as e:
            log(e)
            action_fail("Create erasure profile failed with "
                        "message: {}".format(str(e)))
    elif plugin == "clay":
        d = action_get("helper-chunks")
        scalar_mds = action_get('scalar-mds')
        try:
            create_erasure_profile(service='admin',
                                   erasure_plugin_name=plugin,
                                   profile_name=name,
                                   data_chunks=k,
                                   coding_chunks=m,
                                   helper_chunks=d,
                                   scalar_mds=scalar_mds,
                                   failure_domain=failure_domain,
                                   device_class=device_class)
        except CalledProcessError as e:
            log(e)
            action_fail("Create erasure profile failed with "
                        "message: {}".format(str(e)))
    else:
        # Unknown erasure plugin
        action_fail("Unknown erasure-plugin type of {}. "
                    "Only jerasure, isa, lrc, shec or clay is "
                    "allowed".format(plugin))


if __name__ == '__main__':
    make_erasure_profile()
