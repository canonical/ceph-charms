# 07: Decrypt bounds guard

Follow the [execution guide](../README.md#07-decrypt-bounds-guard), reusing the completed case-06 build and VM. This case exercises the real `__ceph_x_decrypt()` plaintext-length guard with non-shipping instrumentation, not a userspace reimplementation or a malformed-padding error.

## Build and isolation

`build_instrumentation.py` patches the archived `net/ceph/auth_x.c`, adds the C test body in the same translation unit, and incrementally builds libceph and its test runner. It rejects a checkout/worktree as source, already-instrumented source, or changed base artifacts. A second instrumentation build needs fresh case-06 and case-07 output directories.

The test requires the overriding case-07 manifest to match the original case-06 image digest and source revision associated with `SRU_ENVIRONMENT`. It also checks the booted release, full configuration digest, module hashes, and modversions. The base manifest retains the case-06 archive-only build prerequisite. Neither instrumented module loads with force flags.

The patch adds a branch-evidence log inside the existing guard and a test-only wrapper. It does not change the guard condition, production decryption logic, or return value. Fixed synthetic keys are public test data; no cluster credentials are involved.

## Passing result

The full unittest must pass, with all of this fresh kernel evidence:

- A normal 32-byte payload encrypts deterministically to 48 bytes, decrypts through the real function, and returns the original 32 bytes.
- Each plaintext length 0–8 is encrypted with the real cipher, independently decrypted to verify valid padding and exact bytes, then supplied to `__ceph_x_decrypt()`.
- Each short plaintext reaches the actual 9-byte-header guard and returns `-EINVAL`, with nine matching branch markers and short-ciphertext records.

Loading a module, returning a padding error, or rejecting bad magic cannot satisfy these assertions. Stale/missing logs and kernel warnings fail the case.

## Cleanup

The instrumented libceph is loaded from a private staged path; the installed on-disk module is never overwritten. Both instrumented modules and staged files are removed after success or assertion failure. Busy clients prevent unloading and fail the prerequisite; the test never removes clients forcibly. Dependencies may remain loaded in the disposable VM.

Keep the test output and both manifests, then follow the master guide to destroy the dedicated VM. Never load the instrumented module on the host, a cluster client, or a production machine.
