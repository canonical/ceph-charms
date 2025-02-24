#!/usr/bin/env python3
# Copyright 2024 Canonical
# See LICENSE file for licensing details.
#
# Learn more at: https://juju.is/docs/sdk
import unittest
import subprocess
import tempfile
import os

from unittest.mock import patch, MagicMock

from ceph_dashboard_commands import (
    validate_ssl_keypair,
    _run_cmd,
    ceph_mgr_instances,
)


TEST_MGR_DUMP = """
{
    "active_name": "foomgr",
    "active_addrs": {},
    "available": true,
    "standbys": [
        {
            "name": "barmgr",
            "mgr_features": 4540701547738038271,
            "available_modules": []
        }]
}
"""


class TestCephDashboardCommand(unittest.TestCase):
    @patch('ceph_dashboard_commands.subprocess.run')
    def test_run_cmd(self, mock_run):
        # Mock the Popen object and its methods
        process_mock = MagicMock()
        process_mock.stdout = 'output line 1\noutput line 2\n'
        process_mock.stderr = ''
        process_mock.returncode = 0
        mock_run.return_value = process_mock

        # Execute the function
        result = _run_cmd(['echo', 'test'])

        # Verify the result
        self.assertEqual(result, 'output line 1\noutput line 2\n')


class TestSSLValidation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        """Generate test certificates and keys for all test cases"""
        cls.valid_cert, cls.valid_key = cls._generate_cert_key_pair()
        cls.another_cert, cls.another_key = cls._generate_cert_key_pair()
        cls.malformed_cert = (
            b"-----BEGIN CERTIFICATE-----\nMalform\n-----END CERTIFICATE-----"
        )
        cls.malformed_key = (
            b"-----BEGIN PRIVATE KEY-----\nMalform\n-----END PRIVATE KEY-----"
        )

    @staticmethod
    def _generate_cert_key_pair(days=1):
        """Generate a test certificate and private key pair"""
        # create a key tmpfile
        with tempfile.NamedTemporaryFile(mode="wb", delete=False) as key_file:
            subprocess.run(
                [
                    "openssl",
                    "genpkey",
                    "-algorithm",
                    "RSA",
                    "-out",
                    key_file.name,
                ],
                check=True,
                capture_output=True,
            )
            # openssl config file
            with tempfile.NamedTemporaryFile(
                mode="w", delete=False
            ) as config_file:
                config_content = """
                [req]
                default_bits = 2048
                prompt = no
                default_md = sha256
                distinguished_name = dn
                x509_extensions = v3_req

                [dn]
                CN = test.local

                [v3_req]
                basicConstraints = CA:FALSE
                keyUsage = nonRepudiation, digitalSignature, keyEncipherment
                subjectAltName = @alt_names

                [alt_names]
                DNS.1 = test.local
                """
                config_file.write(config_content)
                config_file.flush()

                # create certificate with config file
                with tempfile.NamedTemporaryFile(
                    delete=False, mode="wb"
                ) as cert_file:
                    subprocess.run(
                        [
                            "openssl",
                            "req",
                            "-new",
                            "-x509",
                            "-key",
                            key_file.name,
                            "-out",
                            cert_file.name,
                            "-config",
                            config_file.name,
                        ],
                        check=True,
                        capture_output=True,
                    )
                    with open(cert_file.name, "rb") as cert_f:
                        cert_content = cert_f.read()
                    with open(key_file.name, "rb") as key_f:
                        key_content = key_f.read()

                os.unlink(cert_file.name)
                os.unlink(config_file.name)
            os.unlink(key_file.name)

            return cert_content, key_content

    def test_valid_cert_key_pair(self):
        """Test validation of a valid certificate and key pair"""
        is_valid, message = validate_ssl_keypair(
            self.valid_cert, self.valid_key
        )
        self.assertTrue(is_valid)

    def test_mismatched_pair(self):
        """Test validation with mismatched certificate and key"""
        is_valid, message = validate_ssl_keypair(
            self.valid_cert, self.another_key
        )
        self.assertFalse(is_valid)

    def test_malformed_cert(self):
        """Test validation with malformed certificate"""
        is_valid, message = validate_ssl_keypair(
            self.malformed_cert, self.valid_key
        )
        self.assertFalse(is_valid)

    def test_malformed_key(self):
        """Test validation with malformed key"""
        is_valid, message = validate_ssl_keypair(
            self.valid_cert, self.malformed_key
        )
        self.assertFalse(is_valid)

    def test_empty_inputs(self):
        """Test validation with empty inputs"""
        is_valid, message = validate_ssl_keypair(b"", b"")
        self.assertFalse(is_valid)

    @patch("ceph_dashboard_commands._run_cmd")
    def test_ceph_mgr_instances(self, run_cmd):
        """Test retrieving ceph mgr instances"""
        run_cmd.return_value = TEST_MGR_DUMP
        mgrs = ceph_mgr_instances()
        self.assertEqual(mgrs, ["foomgr", "barmgr"])


if __name__ == "__main__":
    unittest.main()
