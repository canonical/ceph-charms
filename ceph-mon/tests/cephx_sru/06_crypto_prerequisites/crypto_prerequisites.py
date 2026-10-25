"""Case 06: actual Kerberos and cryptomgr vector execution, in a client-only VM."""

from collections import Counter
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


class CryptoPrerequisitesTest(unittest.TestCase):
    @classmethod
    def script(cls, command, timeout=90):
        return remote(cls.expected["model"], "ubuntu/0", command, timeout=timeout)

    @classmethod
    def command(cls, *args, timeout=90):
        return cls.script(shlex.join(map(str, args)), timeout=timeout)

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.addClassCleanup(zaza.clean_up_libjuju_thread)
        cls.expected = json.loads(Path(os.environ["SRU_ENVIRONMENT"]).read_text())
        cls.artifacts = json.loads(Path(cls.expected["SRU_KERNEL_ARTIFACTS"]).read_text())
        assert cls.artifacts["case"] == "06", "Require case-06 artifacts"
        kernel = cls.command("uname", "-r").strip()
        assert kernel == cls.expected["SRU_EXPECTED_KERNEL"] == cls.artifacts["SRU_EXPECTED_KERNEL"]
        assert "cephx-sru06" in kernel, "Never run this on a shipping kernel"
        config = cls.command("cat", f"/boot/config-{kernel}")
        assert hashlib.sha256(config.encode()).hexdigest() == cls.artifacts["config"]["sha256"]
        for option in ("CRYPTO_KRB5=m", "CRYPTO_KRB5_SELFTESTS=y", "CRYPTO_MANAGER_EXTRA_TESTS=y",
                       "MODVERSIONS=y", "MODULE_UNLOAD=y"):
            assert f"CONFIG_{option}\n" in config, f"Missing CONFIG_{option}"
        assert "CONFIG_CRYPTO_MANAGER_DISABLE_TESTS=y" not in config
        for name in ("krb5", "libceph"):
            magic = cls.command("modinfo", "-F", "vermagic", name).split()
            assert magic[0] == kernel and "modversions" in magic, f"Mismatched {name} module"

    def test_kerberos_and_cryptomgr_vectors(self):
        artifact = self.artifacts["module"]
        path = Path(artifact["path"])
        with path.open("rb") as stream:
            self.assertEqual(hashlib.file_digest(stream, "sha256").hexdigest(), artifact["sha256"])
        stage = f"/home/ubuntu/cephx-crypto-{uuid4().hex}.ko"
        self.addCleanup(self.command, "rm", "-f", "--", stage)
        model.scp_to_unit("ubuntu/0", str(path), stage, model_name=self.expected["model"])
        self.assertEqual(self.command("sha256sum", stage).split()[0], artifact["sha256"])
        magic = self.command("modinfo", "-F", "vermagic", stage).split()
        self.assertEqual(magic[0], self.expected["SRU_EXPECTED_KERNEL"])
        self.assertIn("modversions", magic)
        # A busy module is a failed prerequisite, not permission to reuse boot logs.
        self.command("modprobe", "-r", "krb5")
        marker = f"CEPHX_SRU06_BEGIN_{uuid4().hex}"
        self.script(f"printf '%s\\n' {shlex.quote(marker)} > /dev/kmsg")
        self.command("modprobe", "krb5", timeout=600)
        self.addCleanup(self.command, "modprobe", "-r", "krb5")
        for parameter in ("notests", "noextratests"):
            self.assertEqual(self.command("cat", f"/sys/module/cryptomgr/parameters/{parameter}").strip(), "N",
                             f"cryptomgr {parameter} must be disabled")
        self.assertGreater(int(self.command("cat", "/sys/module/cryptomgr/parameters/fuzz_iterations")), 0)
        self.command("insmod", stage, timeout=900)
        self.addCleanup(self.command, "rmmod", "cephx_sru_crypto")
        logs = self.command("dmesg", "--raw").split(marker, 1)
        self.assertEqual(len(logs), 2, "Fresh kernel-log marker was lost")
        evidence = logs[1]
        self.assertIn("krb5: Selftests succeeded", evidence)
        expected = []
        for bits in (128, 256):
            enctype = f"aes{bits}-cts-hmac-sha{'256-128' if bits == 128 else '384-192'}"
            for vector in ("prf", "key", "enc no plain", "enc plain<block", "enc plain==block", "enc plain>block", "mic"):
                expected.append(f"{enctype} {vector}")
            enctype = f"camellia{bits}-cts-cmac"
            vectors = ["key", "enc no plain", "enc 1 plain", "enc 9 plain", "enc 13 plain", "enc 30 plain"]
            vectors += ["mic abc", "mic ABC"] if bits == 128 else ["mic 123", "mic !@#"]
            expected.extend(f"{enctype} {vector}" for vector in vectors)
        # Vector events contain an enctype and test name, unlike suite banners.
        observed = re.findall(r"krb5: Running (\S+-cts-\S+ [^\n]+)", evidence)
        self.assertEqual(Counter(observed), Counter(expected), "All 30 Kerberos vectors must actually run")
        self._assert_cryptomgr_vectors(evidence)
        self.assertNotRegex(evidence, r"Skipping|TESTFAIL|TESTINVAL|Selftests failed|self-tests[^\n]*failed|BUG:|WARNING:|Oops:")

    def _assert_cryptomgr_vectors(self, evidence):
        for hash_name in ("sha256", "sha384"):
            algorithm = f"authenc(hmac({hash_name}),cts(cbc(aes)))"
            # alg_test() also returns zero when its test descriptor is absent.
            self.assertNotIn(f"alg: No test for {algorithm} (", evidence,
                             f"Missing cryptomgr vectors for {algorithm}")
            self.assertRegex(evidence, rf"CEPHX_SRU_CRYPTO vectors={re.escape(algorithm)} driver=\S+ result=0 PASS")
