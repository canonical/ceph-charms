"""Case 07: actual __ceph_x_decrypt rejects valid sub-header ciphertext."""

import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import unittest
from uuid import uuid4

import zaza
from zaza import model

from helpers import remote


class BoundsGuardTest(unittest.TestCase):
    @classmethod
    def command(cls, *args, timeout=90):
        return remote(cls.expected["model"], "ubuntu/0", shlex.join(map(str, args)), timeout=timeout)

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.addClassCleanup(zaza.clean_up_libjuju_thread)
        cls.expected = json.loads(Path(os.environ["SRU_ENVIRONMENT"]).read_text())
        cls.artifacts = json.loads(Path(os.environ.get(
            "SRU_KERNEL_ARTIFACTS", cls.expected["SRU_KERNEL_ARTIFACTS"])).read_text())
        assert cls.artifacts["case"] == "07", "Require explicit non-shipping instrumented artifacts"
        base = json.loads(Path(cls.expected["SRU_KERNEL_ARTIFACTS"]).read_text())
        assert base["case"] == "06", "Require the client's original case-06 manifest"
        assert cls.artifacts["revision"] == base["revision"], "Different kernel source revision"
        assert cls.artifacts["image"]["sha256"] == base["image"]["sha256"], "Different base kernel image"
        kernel = cls.command("uname", "-r").strip()
        assert kernel == cls.expected["SRU_EXPECTED_KERNEL"] == cls.artifacts["SRU_EXPECTED_KERNEL"]
        assert "cephx-sru06" in kernel, "Never load instrumentation into a shipping kernel"
        config = cls.command("cat", f"/boot/config-{kernel}")
        assert hashlib.sha256(config.encode()).hexdigest() == cls.artifacts["config"]["sha256"]
        for option in ("CEPH_LIB=m", "MODVERSIONS=y", "MODULE_UNLOAD=y"):
            assert f"CONFIG_{option}\n" in config, f"Missing CONFIG_{option}"

    def test_real_short_plaintext_guard(self):
        stage = f"/home/ubuntu/cephx-bounds-{uuid4().hex}"
        self.command("install", "-d", "-o", "ubuntu", "-g", "ubuntu", "-m", "700", stage)
        self.addCleanup(self.command, "rm", "-rf", "--", stage)
        paths = {}
        for kind in ("libceph", "module"):
            artifact = self.artifacts[kind]
            path = Path(artifact["path"])
            with path.open("rb") as stream:
                self.assertEqual(hashlib.file_digest(stream, "sha256").hexdigest(), artifact["sha256"])
            guest = f"{stage}/{path.name}"
            model.scp_to_unit("ubuntu/0", str(path), guest, model_name=self.expected["model"])
            self.assertEqual(self.command("sha256sum", guest).split()[0], artifact["sha256"])
            magic = self.command("modinfo", "-F", "vermagic", guest).split()
            self.assertEqual(magic[0], self.expected["SRU_EXPECTED_KERNEL"])
            self.assertIn("modversions", magic)
            paths[kind] = guest
        self.assertEqual(self.command("modinfo", "-F", "name", paths["libceph"]).strip(), "libceph")
        self.assertEqual(self.command("modinfo", "-F", "name", paths["module"]).strip(), "cephx_sru_bounds")
        # Fail if kernel clients are busy. Never unload RBD/CephFS or force modules.
        self.command("modprobe", "-r", "libceph")
        dependencies = self.command("modinfo", "-F", "depends", paths["libceph"]).strip()
        if dependencies:
            self.command("modprobe", "--all", *dependencies.split(","), timeout=600)
        marker = f"CEPHX_SRU07_BEGIN_{uuid4().hex}"
        remote(self.expected["model"], "ubuntu/0", f"printf '%s\\n' {shlex.quote(marker)} > /dev/kmsg")
        # Disk-installed libceph stays untouched; only this VM's loaded module changes.
        self.command("insmod", paths["libceph"])
        self.addCleanup(self.command, "rmmod", "libceph")
        self.command("insmod", paths["module"], timeout=300)
        self.addCleanup(self.command, "rmmod", "cephx_sru_bounds")
        logs = self.command("dmesg", "--raw").split(marker, 1)
        self.assertEqual(len(logs), 2, "Fresh kernel-log marker was lost")
        evidence = logs[1]
        self.assertEqual(evidence.count("CEPHX_SRU07_COMPLETE PASS"), 1)
        self.assertEqual(evidence.count("CEPHX_SRU07_CONTROL ciphertext=48 payload=32 deterministic=1 result=32 PASS"), 1)
        # The branch marker is inside the original production guard; -EINVAL from
        # padding or bad magic cannot satisfy these assertions.
        guards = re.findall(r"CEPHX_SRU07_GUARD plaintext=(\d+) header=(\d+) result=(-?\d+)", evidence)
        self.assertEqual(guards, [(str(length), "9", "-22") for length in range(9)])
        shorts = re.findall(r"CEPHX_SRU07_SHORT ciphertext=(\d+) plaintext=(\d+) header=(\d+) result=(-?\d+) PASS", evidence)
        self.assertEqual(shorts, [("16", str(length), "9", "-22") for length in range(9)])
        self.assertNotRegex(evidence, r"bad magic|unexpected decrypt|BUG:|WARNING:|Oops:|Selftests failed|TESTFAIL|TESTINVAL")
