# 03: Messenger compatibility

Follow the [execution guide](../README.md#03-messenger-compatibility). This case compares kernel RBD and CephFS using `aes256k` in `legacy`, `crc`, and `secure` mode.

## Run order

Use the execution guide's shell and variables, from `ceph-mon/tests/cephx_sru` on the host:

1. Keep the candidate lab from case 02. Run the guide's `juju ssh` command to mount debugfs on the client, making its live kernel-client state readable.
2. Run `python -m unittest -v 03_messenger_compatibility/messenger.py` to compare both storage clients in all three messenger modes; require six tests ending in `OK`.
3. Leave debugfs mounted and keep the lab for [case 04](../04_session_recovery/README.md).

## Prerequisites

Use the candidate-kernel cluster with mounted debugfs, working storage, and all three messenger modes enabled. Both the client credentials and the monitor's `auth_service_cipher` must be `aes256k`: v1 signing uses session keys, not the client's long-term credential. Ceph 19.2.6 must expose live `messenger dump` records for monitor, OSD, and MDS connections. Keep CephX message signing enabled. Missing evidence or unsupported schemas fail the test; it does not reconfigure daemons to make a comparison pass.

## Evidence

All six comparisons must pass: RBD and CephFS in each mode, always using `aes256k` credentials. Key type selects authentication; messenger mode independently selects transport. Each comparison requests its mode, completes a direct 1 MiB write/read comparison, and inspects the still-connected kernel client and daemon sessions.

The monitor-issued `global_id` joins this scoped kernel client to its live daemon connections; it is not a PID or device number. OSD/MDS connections must also match the unique credential name. Every comparison needs monitor and OSD evidence; CephFS additionally needs MDS evidence. Empty dumps or unrelated sessions cannot satisfy those role checks.

- `legacy`: live v1 connections must be `OPENED`. Kernel options must not disable CephX signing, and connected daemons must report signing enabled.
- `crc`: live v2 connections must be `READY`, negotiate `crc`, and report `PLAIN` receive/transmit ciphers.
- `secure`: live v2 connections must be `READY`, negotiate `secure`, and report nonempty, non-`PLAIN` ciphers in both directions.

Requested options alone do not prove negotiation. Legacy signing evidence combines effective kernel and daemon settings, established v1 sessions, and completed I/O; it does not capture individual signatures. Enabled kernel signing requires the messenger-v1 `MSG_AUTH` feature during the handshake.

## Cleanup and limits

Shared resource cleanup removes scoped storage, identities, and private files. Run serially without concurrent daemon reconfiguration. The test reads masked debugfs options and live connection state; it does not enable broad authentication logging or capture secret/signature material.
