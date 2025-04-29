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

import mock
from mock import patch

from test_utils import CharmTestCase

with patch('utils.register_configs') as configs:
    configs.return_value = 'test-config'
    import actions


class PauseTestCase(CharmTestCase):

    def setUp(self):
        super(PauseTestCase, self).setUp(
            actions, ["pause_unit_helper"])

    def test_pauses_services(self):
        actions.pause([])
        self.pause_unit_helper.assert_called_once_with('test-config')


class ResumeTestCase(CharmTestCase):

    def setUp(self):
        super(ResumeTestCase, self).setUp(
            actions, ["resume_unit_helper"])

    def test_pauses_services(self):
        actions.resume([])
        self.resume_unit_helper.assert_called_once_with('test-config')


class MainTestCase(CharmTestCase):

    def setUp(self):
        super(MainTestCase, self).setUp(actions, ["action_fail"])

    def test_invokes_action(self):
        dummy_calls = []

        def dummy_action(args):
            dummy_calls.append(True)

        with mock.patch.dict(actions.ACTIONS, {"foo": dummy_action}):
            actions.main(["foo"])
        self.assertEqual(dummy_calls, [True])

    def test_unknown_action(self):
        """Unknown actions aren't a traceback."""
        exit_string = actions.main(["foo"])
        self.assertEqual("Action foo undefined", exit_string)

    def test_failing_action(self):
        """Actions which traceback trigger action_fail() calls."""
        dummy_calls = []

        self.action_fail.side_effect = dummy_calls.append

        def dummy_action(args):
            raise ValueError("uh oh")

        with mock.patch.dict(actions.ACTIONS, {"foo": dummy_action}):
            actions.main(["foo"])
        self.assertEqual(dummy_calls, ["uh oh"])


class MultisiteActionsTestCase(CharmTestCase):

    TO_PATCH = [
        'action_fail',
        'action_set',
        'multisite',
        'config',
    ]

    def setUp(self):
        super(MultisiteActionsTestCase, self).setUp(actions,
                                                    self.TO_PATCH)
        self.config.side_effect = self.test_config.get

    def test_promote(self):
        self.test_config.set('zone', 'testzone')
        actions.promote([])
        self.multisite.modify_zone.assert_called_once_with(
            'testzone',
            default=True,
            master=True,
        )
        self.multisite.update_period.assert_called_once_with()

    def test_promote_unconfigured(self):
        self.test_config.set('zone', None)
        actions.promote([])
        self.action_fail.assert_called_once()

    def test_readonly(self):
        self.test_config.set('zone', 'testzone')
        actions.readonly([])
        self.multisite.modify_zone.assert_called_once_with(
            'testzone',
            readonly=True,
        )
        self.multisite.update_period.assert_called_once_with()

    def test_readonly_unconfigured(self):
        self.test_config.set('zone', None)
        actions.readonly([])
        self.action_fail.assert_called_once()

    def test_readwrite(self):
        self.test_config.set('zone', 'testzone')
        actions.readwrite([])
        self.multisite.modify_zone.assert_called_once_with(
            'testzone',
            readonly=False,
        )
        self.multisite.update_period.assert_called_once_with()

    def test_readwrite_unconfigured(self):
        self.test_config.set('zone', None)
        actions.readwrite([])
        self.action_fail.assert_called_once()

    def test_tidydefaults(self):
        self.test_config.set('zone', 'testzone')
        actions.tidydefaults([])
        self.multisite.tidy_defaults.assert_called_once_with()

    def test_tidydefaults_unconfigured(self):
        self.test_config.set('zone', None)
        actions.tidydefaults([])
        self.action_fail.assert_called_once()
