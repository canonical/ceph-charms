# 05: Enforced authentication policy

Follow the [execution guide](../README.md#05-enforced-policy-fresh-and-upgraded). Both the fresh deployment and genuine upgrade runs are required. This case permanently replaces credentials and enforces aes256k-only authentication; never use a production or shared model.

## Run order

Use the full commands and variables in the execution guide, from `ceph-mon/tests/cephx_sru` on the host; this case needs two separate lab runs:

1. On the fresh lab retained from cases 01–04, run [`setup_policy.py`](setup_policy.py) with `--origin fresh --disposable` to rotate and verify credentials while both key types remain allowed.
2. Run `python -m unittest -v 05_enforced_policy/enforced_policy.py` to establish working I/O, enforce aes256k-only policy, and check legacy rejection plus stronger-key I/O; require one test ending in `OK`.
3. Save the output and environment record, then run the guide's `juju destroy-model` command to delete that lab and its storage. Run [`setup_lab.py`](../setup_lab.py) with the older `19.2.3-0ubuntu0.24.04.3` distro package pin to create the upgrade lab, and point `SRU_ENVIRONMENT` at its new record.
4. Run `setup_policy.py` with `--origin upgraded --disposable` to upgrade that existing cluster to 19.2.6, then rotate and verify its credentials.
5. Run the same `enforced_policy.py` unittest command again; require one test ending in `OK` on this upgraded cluster too.
6. Save the second run's evidence and destroy the upgraded lab and its storage. Continue to [case 06](../06_crypto_prerequisites/README.md), which creates a separate client-only VM.

## Preparation

`setup_policy.py` requires a `cephx-sru-*` model and `--disposable`. Fresh preparation verifies the original model package pin and the installed/running Ceph versions. Upgrade preparation requires a real `19.2.3-0ubuntu0.24.04.3` deployment from Ubuntu's archive, upgrades it to `19.2.6-0ubuntu0.24.04.1` from the SRU PPA, and checks the unchanged cluster identity. A cipher toggle on an already-new deployment is not an upgrade. Missing archived packages are setup failures, not substitutions or skips.

Preparation has four local operational boundaries: `prepare_origin()` verifies provenance and performs any package upgrade; `inventory_daemons()` locates the real installed daemon keyrings and records MDS IDs; `rotate_credentials()` owns private staging, recovery access, ordered key handover and daemon restarts; `verify_rotation()` checks the resulting keys and service-key health before staging is removed. The main flow then refreshes the client administrator copy, checks running versions, and records successful preparation.

Preparation leaves both key types allowed while it creates and tests a stronger-key recovery administrator. It rotates `mon.` first, then daemon and remaining client keys, and `client.admin` last so administrator handover cannot strand the procedure. Each rotated key is distributed to its installed copies before daemon restart; OSD BlueStore labels are updated too. Checks require changed stronger-type keys, matching local copies, and restarted daemons using them.

Daemon handover uses `restart`, not `start`: a charm status hook can start a stopped daemon before its new key arrives. Preparation checks new systemd invocation IDs and a newly authenticated, active MDS. Planned restarts clear systemd's start-rate counter. OSD stop and offline label update share one unit execution; tool failures abort preparation.

After successful administrator handover, preparation removes the recovery administrator. Only after identity rotation does it switch the service cipher and wipe rotating service keys, avoiding a wait for old service credentials to expire in this disposable lab. It then checks authentication health and refreshes the client administrator copy. Secret values remain in private files; transfers use Zaza SCP, not action arguments/results.

## Enforcement and evidence

The single full test must pass on **each** prepared cluster. Four fresh identities (RBD/CephFS with each key type) first complete I/O under mixed-key policy, then disconnect. This baseline rules out bad capabilities or storage as the reason for a later rejection. The test then enforces aes256k-only authentication and checks the monitor's advertised policy.

Both legacy reconnects must fail with kernel authentication rejection after a unique journal marker, and leave no mapping or mount. Old diagnostics, generic userspace errors, and timeouts cannot pass. RBD uses the kernel's sysfs mapping interface for baseline, rejection, and reconnect, bypassing the `rbd` CLI's userspace authentication preflight.

Stronger-key clients must reconnect, read the pre-enforcement payload unchanged, then complete another direct write/read comparison. After the `ExitStack` removes all scoped volumes and temporary identities, the test requires the original cluster FSID, every OSD up and in, active+clean PGs (including healthy scrubbing states), an available manager, an active non-laggy MDS for the test filesystem, and no health check whose name starts with `AUTH_`. It polls for up to 300 seconds, with bounded commands, to allow asynchronous recovery and authentication-health clearing; a readiness deadline reports the latest status, PG, MDS and detailed health snapshots. Unrelated warnings such as `MON_DISK_LOW` do not fail this check. The cluster remains aes256k-only; run once per prepared cluster, after which it cannot serve cases 01–04.

## Failure recovery and limits

If rotation fails before administrator handover, a root-owned recovery keyring under `/etc/ceph/ceph.client.sru-policy-admin-*.keyring` and its auth entry remain deliberately available. Keep them until access is recovered or the model is destroyed. Other temporary exports are removed.

This is a disposable package-based procedure, not a production rolling upgrade or migration of every charm-managed leader/relation secret. Do not trigger charm reconfiguration during it. The procedure follows [Ceph's key-rotation guidance](https://docs.ceph.com/en/umbrella/rados/configuration/auth-config-ref/#upgrading-and-rotating-cephx-keys); the charm's rotation action does not cover the complete administrator/monitor migration.
