# 06: Kernel crypto prerequisites

Follow the [execution guide](../README.md#06-kernel-crypto-prerequisites). This case needs one disposable Noble amd64 VM, not a Ceph cluster. A normal shipping kernel is insufficient: Kerberos selftests and crypto-manager extra tests must be enabled and observed running.

## Build contract

`build_kernel.py` archives the kernel repository's committed `HEAD` outside the checkout. The default configuration keeps the VM's boot, disk, network, and Juju facilities while omitting unrelated drivers and debug packages. It enables module unloading, modversions, Kerberos selftests, and crypto-manager extra tests. `--config /absolute/base.config` supplies a custom base instead of the trimmed default; mandatory test options still apply.

The Noble source needs the case-local `build-prerequisite.patch`: it makes the otherwise unused global `lsm_key_alloc()` definition static to fix a compilation error with warnings treated as errors. This patch applies only to the source archive. The manifest records its digest, source revision, final configuration, kernel image, and evidence module. The result is a non-shipping test build, not an unmodified source revision.

If compilation is interrupted, resume the incomplete build without reclassifying or replacing its retained configuration:

```bash
python 06_crypto_prerequisites/build_kernel.py \
  --repo "$KERNEL_REPO" --destination "$BUILD_ROOT/06" --jobs 2 --resume-build
```

Run from the same directory and shell as the master guide. Resume requires the same source `HEAD` and rejects completed builds. Do not resume after case 07 has instrumented the source. Full compilation is required; `modules_prepare` alone cannot provide matching symbol-version data.

`setup_client.py` installs the upstream-style image package, which contains its modules, on a separate VM. It disables that VM's Secure Boot, boots the recorded release, and writes `SRU_ENVIRONMENT`. The cluster launcher instead requires separate Ubuntu image/modules packages and is not interchangeable.

## Passing result

The full unittest must pass. It checks the running release, complete configuration digest, mandatory options, enabled runtime extra tests, and matching module versions. It unloads/reloads Kerberos after a fresh log marker and requires all 30 vector names plus `Selftests succeeded`.

The evidence module explicitly calls the kernel's `alg_test()` for `authenc(hmac(sha256),cts(cbc(aes)))` and `authenc(hmac(sha384),cts(cbc(aes)))`. Both must produce success records. Because `alg_test()` also returns zero when a descriptor is missing, the test rejects either algorithm's `alg: No test for ...` diagnostic. Skipped vectors, test failures, warnings, and missing evidence fail the case.

## Cleanup

The evidence module and Kerberos module are unloaded, and staged files removed, including after assertion failures. Busy modules fail rather than being forcibly removed. Retain the model and complete build for case 07; never publish this test kernel or its unsigned modules as shipping artifacts.
