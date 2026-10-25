# 06: Kernel crypto prerequisites

This case proves the backported crypto routines execute their known-answer tests; successful module loading alone is not enough. Follow the [execution guide](../README.md#06-kernel-crypto-prerequisites) for commands. It uses a separate disposable Noble amd64 VM, **without a Ceph cluster**, because the shipping kernel does not enable all diagnostic tests.

## Run order

Use the full commands and variables in the execution guide, from `ceph-mon/tests/cephx_sru` on the host:

1. Run [`build_kernel.sh`](build_kernel.sh) with `KERNEL_REPO` and a new `$BUILD_ROOT/06` directory to build the diagnostic kernel and crypto helper, then write `artifacts.json`.
2. Run [`setup_client.py`](setup_client.py) with that manifest to create the separate VM, install the image and its included modules, disable Secure Boot only in this VM, and boot the diagnostic kernel. Point `SRU_ENVIRONMENT` at the new environment record.
3. Run `python -m unittest -v 06_crypto_prerequisites/crypto_prerequisites.py`; require one test ending in `OK`. It reloads `krb5` for its selftests, then loads the C helper with `insmod` to invoke the crypto-manager tests and check their fresh kernel-log results.
4. Keep this VM, its environment record, and the complete build for [case 07](../07_bounds_guard/README.md); do not destroy or rebuild them between cases.

The builder archives committed `HEAD` outside the checkout and merges the commented [`kernel.config`](kernel.config) fragment onto `x86_64_defconfig`. It does not include uncommitted changes, create a worktree, or patch the original checkout. The fragment keeps VM boot/network support, enables selftests, and drops unrelated drivers/debug packages; `JOBS` controls parallelism (default `2`). The manifest records the source revision, kernel release, and paths/digests for the image, resolved configuration, and compiled [`cephx_sru_crypto.c`](cephx_sru_crypto.c) helper.

The C file is neither a standalone executable nor a Python import: its `module_init()` entry point calls kernel APIs unavailable to the host-side Python process. The unittest loads it, checks its results, and unloads it; there is no separate C command to run.

Use a new build directory each time; the script deliberately has no resume/custom-configuration modes. Keep the complete output for case 07: `modules_prepare` alone does not generate the symbol-version data needed to build matching modules.

## Why the configuration checks matter

`CRYPTO_MANAGER_DISABLE_TESTS=n` keeps `alg_test()` from becoming a success-returning stub. `CRYPTO_MANAGER_EXTRA_TESTS=y` includes the extended tests; runtime `notests`/`noextratests` switches must also be off and fuzz iterations positive. `CRYPTO_KRB5_SELFTESTS=y` builds the separate Kerberos vectors, run when `krb5` loads.

The C helper's `BUILD_BUG_ON` checks reject a build against headers that lack these prerequisites. Requesting options in a fragment is not proof that Kconfig enabled them. Python separately checks the **booted** kernel's resolved configuration and runtime settings, so an old or mismatched guest cannot pass using the new helper.

Compiler warnings remain visible but nonfatal (`CONFIG_WERROR=n`); this does not relax checks for kernel runtime warnings. The test-enabled configuration makes this a **non-shipping kernel**, even though its source is unpatched.

## Passing result and cleanup

The full unittest must finish with `OK`. It requires all 30 named Kerberos vector events and `Selftests succeeded`, plus explicit crypto-manager success records for both RFC8009 AES profiles. `alg_test()` can also return zero when no test descriptor exists, so Python rejects `alg: No test for ...` as well as skips, failures, missing evidence, and kernel runtime warnings.

Modules and staged files are removed even after assertion failures; busy modules fail rather than being forcibly unloaded. Retain the VM, environment record, and build for [case 07](../07_bounds_guard/README.md). Never publish this kernel or its unsigned modules as shipping artifacts.
