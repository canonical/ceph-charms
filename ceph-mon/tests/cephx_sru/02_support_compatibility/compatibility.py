"""Bullet 02: candidate-kernel RBD and CephFS I/O with both CephX key types."""

import json

from helpers import ADMIN, pgs_active_clean
from lab import LabTestCase
from storage import cephfs_volume, rbd_volume


class CompatibilityTest(LabTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        monmap = json.loads(cls.run_command("ceph", *ADMIN, "mon", "dump",
                                             "--format", "json"))
        assert {cipher["name"] for cipher in monmap["auth_allowed_ciphers"]} == {
            "aes", "aes256k"
        }, "This comparison requires a mixed-key cluster"
        pgs = json.loads(cls.run_command("ceph", *ADMIN, "pg", "ls-by-pool",
                                        cls.pool, "--format", "json"))
        assert pgs_active_clean(pgs["pg_stats"]), \
            "The dedicated RBD pool must have active+clean PGs"

    def test_rbd_aes(self):
        self._compare(rbd_volume, "aes")

    def test_rbd_aes256k(self):
        self._compare(rbd_volume, "aes256k")

    def test_cephfs_aes(self):
        self._compare(cephfs_volume, "aes")

    def test_cephfs_aes256k(self):
        self._compare(cephfs_volume, "aes256k")

    def _compare(self, resource, key_type):
        with resource(self, key_type) as volume:
            volume.connect()
            volume.write()
            volume.read_compare()
