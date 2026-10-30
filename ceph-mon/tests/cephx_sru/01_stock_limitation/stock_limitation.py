"""Bullet 01: stock kernel legacy I/O works, but aes256k keys are rejected."""

import json
import re

from helpers import ADMIN, RemoteCommandError
from lab import LabTestCase
from storage import cephfs_volume, rbd_volume
from .switch_kernel import stock_provenance


class StockLimitationTest(LabTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        assert cls.expected.get("SRU_KERNEL_KIND") == "stock", \
            "Use a separate stock environment produced by switch_kernel.py"
        assert cls.expected.get("SRU_CANDIDATE_KERNEL"), "Missing candidate release record"
        assert cls.expected["SRU_EXPECTED_KERNEL"] != cls.expected["SRU_CANDIDATE_KERNEL"], \
            "This control must not run the candidate kernel"
        version = cls.expected.get("SRU_STOCK_KERNEL_VERSION")
        assert version, "Missing exact Ubuntu stock package version"
        stock_provenance(cls.run_command, cls.expected["SRU_EXPECTED_KERNEL"], version)
        monmap = json.loads(cls.run_command("ceph", *ADMIN, "mon", "dump",
                                             "--format", "json"))
        assert {cipher["name"] for cipher in monmap["auth_allowed_ciphers"]} == {
            "aes", "aes256k"
        }, "This control requires the mixed-key cluster, not an aes256k-only cluster"

    def test_rbd_aes_direct_io(self):
        with rbd_volume(self, "aes") as volume:
            volume.connect()
            volume.write()
            volume.read_compare()

    def test_cephfs_aes_direct_io(self):
        with cephfs_volume(self, "aes") as volume:
            volume.connect()
            volume.write()
            volume.read_compare()

    def test_rbd_aes256k_rejected_by_kernel(self):
        with rbd_volume(self, "aes256k") as volume:
            with self.assertRaises(RemoteCommandError) as rejected:
                volume.connect()
            error = rejected.exception
            self.assertNotIn(str(error.code), ("0", "124", "137"),
                             "A success or timeout is not a crypto-key rejection")
            diagnostics = error.stdout + "\n" + error.stderr
            self.assertRegex(diagnostics,
                             rf"failed to add secret '{re.escape(volume.entity)}' to kernel")
            self.assertRegex(diagnostics, r"map failed: \(22\) Invalid argument")

    def test_cephfs_aes256k_rejected_by_kernel(self):
        with cephfs_volume(self, "aes256k") as volume:
            with self.assertRaises(RemoteCommandError) as rejected:
                volume.connect()
            error = rejected.exception
            self.assertNotIn(str(error.code), ("0", "124", "137"),
                             "A success or timeout is not a crypto-key rejection")
            self.assertRegex(error.stdout + "\n" + error.stderr,
                             r"adding ceph secret key to kernel failed: Invalid argument")
