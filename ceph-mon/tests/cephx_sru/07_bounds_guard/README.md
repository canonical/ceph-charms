# 07: Decrypt bounds guard

This case proves that the real `__ceph_x_decrypt()` rejects successfully decrypted data **before reading a header that is too short**. Follow the [execution guide](../README.md#07-decrypt-bounds-guard), reusing case 06's build and VM; no Ceph cluster is involved.

## Run order

Use the full commands and variables in the execution guide, from `ceph-mon/tests/cephx_sru` on the host:

1. After case 06 passes, run [`build_instrumentation.py`](build_instrumentation.py) against its retained build, with a new `$BUILD_ROOT/07` destination. This builds an instrumented `libceph.ko` and the `cephx_sru_bounds.ko` runner, recording both in a new manifest.
2. Keep `SRU_ENVIRONMENT` pointing to the case-06 VM and set `SRU_KERNEL_ARTIFACTS` to the case-07 manifest for `python -m unittest -v 07_bounds_guard/bounds_guard.py`; require one test ending in `OK`.
3. Save the output and both manifests, then run the guide's `juju destroy-model` command to delete the dedicated kernel VM. This completes the sequence.

The builder patches only case 06's archived `net/ceph/auth_x.c`: it logs entry into the existing length guard and includes [`cephx_sru_bounds_test.inc`](cephx_sru_bounds_test.inc) beside the private decrypt function. This lets the test call the real function without replacing it or changing its condition/return value.

[`bounds_guard.py`](bounds_guard.py) copies both modules to the VM, loads their dependencies, then loads libceph followed by the runner, [`cephx_sru_bounds.c`](cephx_sru_bounds.c). The runner's `module_init()` calls the test-only `cephx_sru_bounds_run()` export. Python checks the new kernel-log evidence, then unloads both modules.

The `.c` file is the entry point; the `.inc` file contains the comparisons. Neither runs as a standalone program. Fixed keys are public synthetic test data, not cluster credentials.

The builder rejects a checkout/worktree, already-modified `auth_x.c`, or changed base artifacts. Use new case-06/07 directories for another instrumentation build. The runtime test requires the original case-06 image/source identity, matching configuration and module hashes, and matching symbol versions; it never forces a module load.

## What proves the guard works

First, a normal 32-byte payload must survive a complete CephX encrypt/decrypt round trip. This positive control shows the test key, cipher, and real decrypt path work.

Next, the helper encrypts plaintexts of lengths 0–8 with the real legacy AES cipher, without adding a CephX header. These are valid padded ciphertexts, but too short to contain the packed header: one version byte plus eight magic bytes. A separate raw decrypt verifies padding, length, and bytes before each ciphertext is passed to the real CephX decrypt function.

The full unittest must finish with `OK`, requiring the positive control, nine branch markers **inside the actual guard**, nine matching `-EINVAL` results, and the runner's completion record. Just seeing `-EINVAL` would be insufficient: invalid padding or bad magic could fail elsewhere. Stale/missing logs and kernel warnings also fail.

## Cleanup

Only the VM's loaded libceph changes; the installed on-disk module is never overwritten. Both instrumented modules and staged files are removed after success or assertion failure. Busy clients prevent unloading and fail the prerequisite; dependencies may remain loaded.

Never load these non-shipping modules on the host, a cluster client, or a production machine.
