import importlib
import unittest


crypto = importlib.import_module("06_crypto_prerequisites.crypto_prerequisites")


class CryptoEvidenceTest(unittest.TestCase):
    def test_missing_descriptor_cannot_pass_with_successful_alg_test_return(self):
        algorithms = [f"authenc(hmac({name}),cts(cbc(aes)))" for name in ("sha256", "sha384")]
        passed = "\n".join(
            f"CEPHX_SRU_CRYPTO vectors={name} driver=fixture result=0 PASS"
            for name in algorithms)
        for missing in algorithms:
            with self.subTest(missing=missing):
                # The kernel's notest2 path returns zero and emits this diagnostic.
                evidence = f"alg: No test for {missing} (fixture)\n{passed}"
                with self.assertRaises(AssertionError):
                    crypto.CryptoPrerequisitesTest()._assert_cryptomgr_vectors(evidence)
