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

import unittest.mock as mock
from ops.testing import Harness
with mock.patch('charmhelpers.contrib.hardening.harden.harden') as mock_dec:
    mock_dec.side_effect = (lambda *dargs, **dkwargs: lambda f:
                            lambda *args, **kwargs: f(*args, **kwargs))
    # src.charm imports ceph_hooks, so we need to workaround the inclusion
    # of the 'harden' decorator.
    from src.charm import CephMonCharm


relation_id = int


def add_ceph_client_relation(harness: Harness[CephMonCharm]) -> relation_id:
    rel_id = harness.add_relation(
        'client',
        'glance')
    harness.add_relation_unit(
        rel_id,
        'glance/0')
    harness.update_relation_data(
        rel_id,
        'glance/0',
        {'ingress-address': '10.0.0.3'})
    return rel_id


def add_ceph_mds_relation(harness: Harness[CephMonCharm]) -> relation_id:
    rel_id = harness.add_relation(
        'mds',
        'ceph-fs')
    harness.add_relation_unit(
        rel_id,
        'ceph-fs/0')
    harness.update_relation_data(
        rel_id,
        'ceph-fs/0',
        {'ingress-address': '10.0.0.3'})
    return rel_id
