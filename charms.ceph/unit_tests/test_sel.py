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

import charms_ceph.selog as selog

from datetime import datetime, timezone
import json
import unittest


class SecurityEventLoggingTest(unittest.TestCase):

    def setUp(self):
        self.prev_cb = selog.register_log_callback(lambda x: x)
        self.prev_defaults = selog.register_defaults({'appid': 'app'})

    def tearDown(self):
        selog.register_log_callback(self.prev_cb)
        selog.register_defaults(self.prev_defaults)

    def test_logging(self):
        obj = json.loads(selog.log('Logging message', msg='short message',
                                   event='sys_startup', detail='app_started'))
        self.assertEqual(obj['type'], 'security')
        self.assertEqual(obj['level'], 'WARN')
        self.assertEqual(obj['appid'], 'app')

        date = datetime.fromisoformat(obj['datetime'])
        self.assertIs(date.tzinfo, timezone.utc)

    def test_invalid_log(self):
        with self.assertRaises(Exception):
            selog.log('???', invalid=None)

        with self.assertRaises(ValueError):
            selog.log('', event='invalid')

        with self.assertRaises(ValueError):
            selog.log('', event='sys_startup', level='invalid')
