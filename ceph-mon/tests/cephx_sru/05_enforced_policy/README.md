# 05: Enforced authentication policy

Follow the [execution guide](../README.md#05-enforced-policy-fresh-and-upgraded). Both the fresh deployment and genuine upgrade runs are required. This case permanently replaces credentials and enforces aes256k-only authentication; never use a production or shared model.

## Preparation

`setup_policy.py` requires a `cephx-sru-*` model and `--disposable`. Fresh preparation verifies the original model package pin and the installed/running Ceph versions. Upgrade preparation requires a real `19.2.3-0ubuntu0.24.04.3` deployment from Ubuntu's archive, upgrades it to `19.2.6-0ubuntu0.24.04.1` from the SRU PPA, and checks the unchanged cluster identity. A cipher toggle on an already-new deployment is not an upgrade. Missing archived packages are setup failures, not substitutions or skips.

Preparation creates a tested recovery administrator, rotates the monitor, daemon, administrator, and remaining client keys, distributes private keyrings, and updates OSD BlueStore labels. It verifies that keys changed, installed copies match, and all keys use the stronger type before enforcement. It also replaces the rotating service keys and refreshes the client administrator copy. Secret values remain in private files; transfers use Zaza SCP, not action arguments/results.

Daemon handover uses `restart`, not `start`: a charm status hook can start a stopped daemon before its new key arrives. Preparation checks new systemd invocation IDs and a newly authenticated, active MDS. Planned restarts clear systemd's start-rate counter. OSD stop and offline label update share one unit execution; tool failures abort preparation.

## Passing result

The single full test must pass on **each** prepared cluster. Both legacy and stronger-key identities first perform RBD/CephFS I/O. After aes256k-only enforcement, both legacy attempts must fail with fresh kernel authentication-rejection diagnostics. RBD uses the kernel's sysfs mapping interface for baseline, rejection, and reconnect, bypassing the `rbd` CLI's userspace authentication preflight. Generic userspace errors or timeouts cannot pass.

Stronger-key clients must reconnect, read the pre-enforcement payload unchanged, then complete another direct write/read comparison. Scoped resources are cleaned up, but the cluster remains aes256k-only. Run once per prepared cluster; it cannot then serve cases 01–04.

## Failure recovery and limits

If rotation fails before administrator handover, a root-owned recovery keyring under `/etc/ceph/ceph.client.sru-policy-admin-*.keyring` and its auth entry remain deliberately available. Keep them until access is recovered or the model is destroyed. Other temporary exports are removed.

This is a disposable package-based procedure, not a production rolling upgrade or migration of every charm-managed leader/relation secret. Do not trigger charm reconfiguration during it. The procedure follows [Ceph's key-rotation guidance](https://docs.ceph.com/en/umbrella/rados/configuration/auth-config-ref/#upgrading-and-rotating-cephx-keys); the charm's rotation action does not cover the complete administrator/monitor migration.
