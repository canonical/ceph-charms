# Copyright 2019 Canonical Ltd
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#  http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import mock
import subprocess

import test_utils
import create_crush_rule
import copy_pool


class CopyPoolTestCase(test_utils.CharmTestCase):

    TO_PATCH = [
        'hookenv',
    ]

    def setUp(self):
        super(CopyPoolTestCase, self).setUp(
            copy_pool,
            self.TO_PATCH
        )

    @mock.patch.object(create_crush_rule.subprocess, 'check_call')
    def test_copy_pool(self, mock_check_call):
        _action_data = {
            'source': 'source-pool',
            'target': 'target-pool',
        }
        self.hookenv.action_get.side_effect = lambda k: _action_data.get(k)
        copy_pool.copy_pool()
        mock_check_call.assert_called_with([
            'rados', 'cppool',
            'source-pool', 'target-pool',
        ])

    @mock.patch.object(create_crush_rule.subprocess, 'check_call')
    def test_copy_pool_failed(self, mock_check_call):
        _action_data = {
            'source': 'source-pool',
            'target': 'target-pool',
        }
        self.hookenv.action_get.side_effect = lambda k: _action_data.get(k)
        mock_check_call.side_effect = subprocess.CalledProcessError(1, 'rados')
        copy_pool.copy_pool()
        mock_check_call.assert_called_with([
            'rados', 'cppool',
            'source-pool', 'target-pool',
        ])
        self.hookenv.action_fail.assert_called_once_with(mock.ANY)


class CreateCrushRuleTestCase(test_utils.CharmTestCase):

    TO_PATCH = [
        'hookenv',
    ]

    def setUp(self):
        super(CreateCrushRuleTestCase, self).setUp(
            create_crush_rule,
            self.TO_PATCH
        )

    @mock.patch.object(create_crush_rule.subprocess, 'check_call')
    def test_create_crush_rule(self, mock_check_call):
        _action_data = {
            'name': 'replicated_nvme',
            'failure-domain': 'host',
            'device-class': 'nvme',
        }
        self.hookenv.action_get.side_effect = lambda k: _action_data.get(k)
        create_crush_rule.create_crush_rule()
        mock_check_call.assert_called_with([
            'ceph', 'osd', 'crush', 'rule',
            'create-replicated',
            'replicated_nvme',
            'default',
            'host',
            'nvme',
        ])

    @mock.patch.object(create_crush_rule.subprocess, 'check_call')
    def test_create_crush_rule_no_class(self, mock_check_call):
        _action_data = {
            'name': 'replicated_whoknows',
            'failure-domain': 'disk',
        }
        self.hookenv.action_get.side_effect = lambda k: _action_data.get(k)
        create_crush_rule.create_crush_rule()
        mock_check_call.assert_called_with([
            'ceph', 'osd', 'crush', 'rule',
            'create-replicated',
            'replicated_whoknows',
            'default',
            'disk',
        ])

    @mock.patch.object(create_crush_rule.subprocess, 'check_call')
    def test_create_crush_rule_failed(self, mock_check_call):
        _action_data = {
            'name': 'replicated_nvme',
            'failure-domain': 'host',
            'device-class': 'nvme',
        }
        self.hookenv.action_get.side_effect = lambda k: _action_data.get(k)
        mock_check_call.side_effect = subprocess.CalledProcessError(1, 'test')
        create_crush_rule.create_crush_rule()
        mock_check_call.assert_called_with([
            'ceph', 'osd', 'crush', 'rule',
            'create-replicated',
            'replicated_nvme',
            'default',
            'host',
            'nvme',
        ])
        self.hookenv.action_fail.assert_called_once_with(mock.ANY)
