# 01: Stock-kernel control

Follow the [execution guide](../README.md#01-stock-kernel-control) for switching, testing, and restoration. This case checks that the pre-backport kernel rejects `aes256k`, while legacy `aes` still works for kernel RBD and CephFS.

## Prerequisites

Use the mixed-key Ceph 19.2.6 lab. The client must already have matching, unmodified amd64 `linux-image-RELEASE` and `linux-modules-RELEASE` packages from a trusted Ubuntu Noble main archive. The image must be signed, from source `linux-signed`; modules must come from source `linux`. `python3-apt` and authenticated archive metadata must remain available. The helper validates package versions, origin, file integrity, and ownership of the Ceph modules; it does not install kernels.

Set `STOCK_RELEASE` and `STOCK_VERSION` to that installed pre-backport kernel. For example, the validated control used `6.8.0-147-generic` and `6.8.0-147.147`. A later archive kernel that already supports `aes256k` is not a negative control. The candidate image/modules must remain installed for restoration.

## Passing result

All four tests must pass. Legacy-key RBD and CephFS each complete a deterministic nonzero 1 MiB direct write/read comparison. The stronger-key tests require the specific unsupported-key diagnostics: RBD fails to add the secret to the kernel with error 22, and CephFS reports `adding ceph secret key to kernel failed: Invalid argument`. Generic failures, timeouts, and server authentication failures do not count.

## Restoration

Both switch operations reboot only the dedicated client. `restore` verifies the installed candidate packages, sets the bootloader default to that kernel, reboots the client, and checks the running release. It does not recreate the VM, roll back its disk, remove either kernel, or change the Ceph cluster.

For both `stock` and `restore`, pass the `environment.json` created by `setup_lab.py` to `--environment`; it identifies the candidate kernel to return to and remains unchanged. Stock setup writes a separate record without candidate artifact hashes, used only to run the stock tests. Do not pass that stock-test record to the switch helper, even after an interrupted setup or failed test.

The execution guide registers an exit trap before switching to stock. That trap runs after the stock commands finish or fail, restoring the candidate before cases 02–05 while preserving failure status. The guide also gives an independent recovery command. Scoped test storage and credentials are removed through the shared storage cleanup.
