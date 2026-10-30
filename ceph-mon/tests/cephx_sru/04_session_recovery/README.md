# 04: Session recovery

Follow the [execution guide](../README.md#04-session-recovery). This case temporarily changes ticket lifetimes, restarts both OSDs separately, and restarts the filesystem's active MDS. Use only the dedicated lab, without concurrent tests or other kernel Ceph clients.

## Prerequisites

The cluster must have exactly two OSDs, one active MDS for the recorded filesystem, and all placement groups active+clean. The candidate needs mounted debugfs, `CONFIG_DYNAMIC_DEBUG`, and readable kernel journal messages from `libceph`'s `process_one_ticket` sites: `expires=%llu renew_after=` and `got ticket service %d`. Missing instrumentation or readiness is a failure, not a skip.

## Passing result

All four tests must pass:

- RBD remap and CephFS remount each write once, disconnect, reconnect, and compare uncached direct readback without rewriting the stored payload.
- The two live-recovery tests keep the original kernel client connected while observing ticket renewal and restarting daemons. They compare direct readback after every recovery.

Renewal requires a successful ticket installation with a later expiration, observed no earlier than the original renewal threshold. This is checked for AUTH and OSD tickets, plus MDS tickets for CephFS, within a bounded wait. The same sole kernel client must remain present; elapsed time or successful I/O alone is insufficient. Only the two nonsecret debug sites are enabled, and guest journal output is filtered before transfer.

Daemon metadata identifies the actual units to restart. Changed systemd invocation IDs and OSD `up_from` epochs prove restarts; both OSDs must return up+in and placement groups active+clean. CephFS also requires a new active MDS global ID and a fresh directory create/remove through the unchanged mount. Cached file reads alone cannot prove MDS recovery.

## Restoration

Cleanup restores the original monitor ticket-lifetime overrides and dynamic-debug flags, then removes scoped storage and credentials. A killed process, hung kernel, or unreachable unit can prevent cleanup. After interruption, inspect daemon state, mounts/mappings, ticket lifetimes, and debug flags, or discard the disposable lab.
