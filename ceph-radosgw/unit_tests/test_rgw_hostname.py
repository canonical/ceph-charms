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

import unittest

import rgw_hostname


class TestRgwHostnameHelpers(unittest.TestCase):

    def test_parse_rgw_public_hostnames(self):
        self.assertEqual(
            ['rgw.example.com', 's3.example.com'],
            rgw_hostname.parse_rgw_public_hostnames(
                ' rgw.example.com , s3.example.com,rgw.example.com ,, '),
        )

    def test_normalise_rgw_public_hostnames(self):
        self.assertEqual(
            'rgw.example.com,s3.example.com',
            rgw_hostname.normalise_rgw_public_hostnames(
                ' rgw.example.com , s3.example.com '),
        )

    def test_apache_ssl_vhosts(self):
        endpoints = [
            ('10.0.0.10', '10.0.0.10', 443, 80),
            ('10.0.0.10', 'rgw.example.com,s3.example.com', 443, 80),
        ]
        self.assertEqual(
            [
                {
                    'address': '10.0.0.10',
                    'endpoint': '10.0.0.10',
                    'cert_cn': '10.0.0.10',
                    'aliases': [],
                    'ext': 443,
                    'int': 80,
                },
                {
                    'address': '10.0.0.10',
                    'endpoint': 'rgw.example.com',
                    'cert_cn': 'rgw.example.com',
                    'aliases': ['*.rgw.example.com'],
                    'ext': 443,
                    'int': 80,
                },
                {
                    'address': '10.0.0.10',
                    'endpoint': 's3.example.com',
                    'cert_cn': 's3.example.com',
                    'aliases': ['*.s3.example.com'],
                    'ext': 443,
                    'int': 80,
                },
            ],
            rgw_hostname.apache_ssl_vhosts(
                endpoints,
                'rgw.example.com,s3.example.com',
                True,
            ),
        )

    def test_certificate_requests(self):
        cert_requests = {
            'juju-123.lxd': {'sans': ['10.0.0.10']},
            'rgw.example.com,s3.example.com': {
                'sans': ['10.0.0.10', 'rgw.example.com,s3.example.com'],
            },
        }
        self.assertEqual(
            {
                'juju-123.lxd': {'sans': ['10.0.0.10']},
                'rgw.example.com': {
                    'sans': [
                        '*.rgw.example.com',
                        '10.0.0.10',
                        'rgw.example.com',
                    ],
                },
                's3.example.com': {
                    'sans': [
                        '*.s3.example.com',
                        '10.0.0.10',
                        's3.example.com',
                    ],
                },
            },
            rgw_hostname.certificate_requests(
                cert_requests,
                'rgw.example.com,s3.example.com',
                virtual_hosted_bucket_enabled=True,
            ),
        )
