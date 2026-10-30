# 02: Support and compatibility

Follow the [execution guide](../README.md#02-support-and-compatibility). Use the candidate kernel and mixed-key Ceph 19.2.6 cluster; both `aes` and `aes256k` must remain allowed.

## Passing result

All four tests must pass: RBD and CephFS, each with a newly created legacy or stronger-key identity. Each comparison maps or mounts through the kernel, directly writes and reads a deterministic nonzero 1 MiB payload, and compares its bytes and SHA-256 digest.

This case uses `ms_mode=crc`. It proves storage compatibility, not all messenger modes or session recovery; those are covered by cases 03 and 04. The shared test base checks the expected kernel, modules, packages, cluster identity, and authentication requirements before comparisons run.

## Cleanup

Each comparison owns its restricted identity, storage, mount or mapping, and private credential files. Context managers remove them after success or failure. A hung kernel or interrupted host process can prevent cleanup; inspect or discard that disposable model before continuing.
