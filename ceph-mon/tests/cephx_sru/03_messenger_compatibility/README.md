# 03: Messenger compatibility

Follow the [execution guide](../README.md#03-messenger-compatibility). This case compares kernel RBD and CephFS using `aes256k` in `legacy`, `crc`, and `secure` mode.

## Prerequisites

Use the candidate-kernel cluster with mounted debugfs, working storage, and all three messenger modes enabled. Ceph 19.2.6 must expose live `messenger dump` records for monitor, OSD, and MDS connections. Keep CephX message signing enabled. Missing evidence or unsupported schemas fail the test; it does not reconfigure daemons to make a comparison pass.

## Passing result

All six comparisons must pass. Each performs a direct 1 MiB write/read comparison, then correlates the kernel client's authenticated global ID with live daemon connections. OSD/MDS connections must also match its unique identity. Every comparison needs monitor and OSD evidence; CephFS additionally needs MDS evidence.

- `legacy`: live v1 connections must be `OPENED`. Kernel options must not disable CephX signing, and connected daemons must report signing enabled.
- `crc`: live v2 connections must be `READY`, negotiate `crc`, and report `PLAIN` receive/transmit ciphers.
- `secure`: live v2 connections must be `READY`, negotiate `secure`, and report nonempty, non-`PLAIN` ciphers in both directions.

Requested options alone do not prove negotiation. Legacy signing evidence combines effective kernel and daemon settings, established v1 sessions, and completed I/O; it does not capture individual signatures. In the kernel, `ceph_create_client()` requires `CEPH_FEATURE_MSG_AUTH` when signing is enabled, and messenger-v1 uses the negotiated feature for signing callbacks.

## Cleanup and limits

Shared resource cleanup removes scoped storage, identities, and private files. Run serially without concurrent daemon reconfiguration. The test reads masked debugfs options and live connection state; it does not enable broad authentication logging or capture secret/signature material.
