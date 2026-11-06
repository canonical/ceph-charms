# 04: Session recovery

Follow the [execution guide](../README.md#04-session-recovery). This case temporarily changes ticket lifetimes, restarts both OSDs separately, and restarts the filesystem's active MDS. Use only the dedicated lab, without concurrent tests or other kernel Ceph clients.

## Run order

Use the execution guide's shell and variables, from `ceph-mon/tests/cephx_sru` on the host:

1. Keep the candidate lab and mounted debugfs from case 03; do not start other kernel Ceph clients.
2. Run `python -m unittest -v 04_session_recovery/recovery.py` to exercise remap/remount, both ticket-renewal scenarios, and daemon restarts; require six tests ending in `OK`. The test manages the temporary settings and restarts—do not perform them separately.
3. After successful cleanup, keep the same mixed-key lab for the fresh-deployment run of [case 05](../05_enforced_policy/README.md).

## Prerequisites

The cluster must have exactly two OSDs, one active MDS for the recorded filesystem, and all placement groups active+clean. The candidate needs mounted debugfs, `CONFIG_DYNAMIC_DEBUG`, and readable kernel journal messages from `libceph`'s `process_one_ticket` sites: `expires=%llu renew_after=` and `got ticket service %d`. Missing instrumentation or readiness is a failure, not a skip.

## Evidence

All six tests must pass:

- RBD remap and CephFS remount each write once, disconnect, reconnect, and compare uncached direct readback without rewriting the stored payload.
- The two live-recovery tests first shorten AUTH and service tickets to 60 seconds, then keep the original kernel client connected through observed renewal and separate OSD restarts. CephFS additionally restarts its MDS. They compare the original payload by direct readback after renewal and each restart, without rewriting it.
- The two independent service-ticket renewal tests use a 300-second AUTH lifetime and 60-second service lifetimes, without restarting any daemon. RBD must install a later-expiring OSD ticket; CephFS must install later-expiring MDS and OSD tickets. Every required renewal must precede the original AUTH ticket's renewal threshold, and no replacement AUTH ticket may be observed. They compare uncached direct readback of the original payload afterward, preserving the same sole kernel client and mapping/mount path.

Renewal requires a successful ticket installation with a later expiration, observed no earlier than the original service ticket's renewal threshold. The live-recovery scenario checks AUTH and OSD tickets, plus MDS tickets for CephFS, within 180 seconds; the independent scenario checks service renewals within the same deadline while the original AUTH ticket remains before its renewal threshold. Initial ticket lifetimes are checked per service against the requested settings. Renewal normally occurs before expiry: merely waiting for a ticket lifetime, or observing a renewal request, is not evidence of an installed replacement. Missing, empty, or stale ticket evidence fails the test. The same sole kernel client and mapping/mount path must remain present; a replacement client cannot pass as recovery.

Only the two nonsecret debug sites are enabled. Their expiry and installation messages are paired across journal batches, with guest output filtered before transfer. Requiring a single kernel Ceph client makes those messages attributable to this workload.

Daemon metadata identifies the actual units to restart. Changed systemd invocation IDs and OSD `up_from` epochs prove restarts; both OSDs must return up+in and placement groups active+clean. CephFS also requires a new active MDS global ID and a fresh directory create/remove through the unchanged mount. Cached file reads alone cannot prove MDS recovery.

## Restoration

Cleanup restores the original monitor ticket-lifetime overrides and dynamic-debug flags, then removes scoped storage and credentials. A killed process, hung kernel, or unreachable unit can prevent cleanup. After interruption, inspect daemon state, mounts/mappings, ticket lifetimes, and debug flags, or discard the disposable lab.
