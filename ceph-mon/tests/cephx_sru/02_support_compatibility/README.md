# 02: Support and compatibility

Follow the [execution guide](../README.md#02-support-and-compatibility). Use the candidate kernel and mixed-key Ceph 19.2.6 cluster; both `aes` and `aes256k` must remain allowed.

## Run order

Use the execution guide's shell and variables, from `ceph-mon/tests/cephx_sru` on the host:

1. Finish case 01's candidate restoration; keep the same mixed-key cluster and set `SRU_ENVIRONMENT` to its candidate record.
2. Run `python -m unittest -v 02_support_compatibility/compatibility.py` to check RBD and CephFS I/O with both key types; require four tests ending in `OK`.
3. Keep the lab and candidate kernel for [case 03](../03_messenger_compatibility/README.md); no new setup or reboot is needed.

## Evidence

After restoring the candidate from case 01, all four comparisons must pass: RBD and CephFS, each with a newly created `aes` or `aes256k` identity. Each maps or mounts through the kernel, directly writes and reads a deterministic nonzero 1 MiB payload, and compares its bytes and SHA-256 digest. Fresh identities and storage keep one successful comparison from supplying another's evidence.

The cluster must allow both key types and the dedicated RBD pool must be active+clean before I/O. The shared test base also checks the expected kernel, modules, packages, cluster identity, and authentication requirements.

Like case 01, this case uses the shared storage helpers' explicit `ms_mode=crc` default. The kernel changes, not the messenger mode: the candidate must support `aes256k` without regressing legacy-key I/O. Case 03 varies the messenger mode, and case 04 tests session recovery.

## Cleanup

Each comparison owns its restricted identity, storage, mount or mapping, and private credential files. Context managers remove them after success or failure. A hung kernel or interrupted host process can prevent cleanup; inspect or discard that disposable model before continuing.
