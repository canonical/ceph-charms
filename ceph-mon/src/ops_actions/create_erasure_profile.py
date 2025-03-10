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

from subprocess import CalledProcessError

from charmhelpers.contrib.storage.linux.ceph import create_erasure_profile
import logging

logger = logging.getLogger(__name__)


def create_erasure_profile_action(event):
    name = event.params.get("name")
    plugin = event.params.get("plugin")
    failure_domain = event.params.get("failure-domain")
    device_class = event.params.get("device-class")
    k = event.params.get("data-chunks")
    m = event.params.get("coding-chunks")

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
            logger.warning(e)
            event.fail("Create erasure profile failed with "
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
            logger.warning(e)
            event.fail("Create erasure profile failed with "
                       "message: {}".format(str(e)))
    elif plugin == "lrc":
        locality_chunks = event.params.get("locality-chunks")
        crush_locality = event.params.get('crush-locality')
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
            logger.warning(e)
            event.fail("Create erasure profile failed with "
                       "message: {}".format(str(e)))
    elif plugin == "shec":
        c = event.params.get("durability-estimator")
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
            logger.warning(e)
            event.fail("Create erasure profile failed with "
                       "message: {}".format(str(e)))
    elif plugin == "clay":
        d = event.params.get("helper-chunks")
        scalar_mds = event.params.get('scalar-mds')
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
            logger.warning(e)
            event.fail("Create erasure profile failed with "
                       "message: {}".format(str(e)))
    else:
        # Unknown erasure plugin
        event.fail("Unknown erasure-plugin type of {}. "
                   "Only jerasure, isa, lrc, shec or clay is "
                   "allowed".format(plugin))
