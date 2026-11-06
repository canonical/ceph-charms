# CephX SRU tests

Run all seven cases in order on disposable labs for https://bugs.launchpad.net/ubuntu/+source/linux/+bug/2171558. Tests run on the host through Zaza/libjuju; guests do not need pytest or a copied test suite. Follow the commands below in one Bash shell; the linked case READMEs explain the mechanisms and required evidence.

**Never use production or shared labs, and never run cases concurrently.** Cases 01 and 04 reboot clients or restart daemons. Case 05 permanently rotates credentials and enforces aes256k-only authentication. Cases 06–07 use a separate, non-shipping kernel.

## Prepare the host and inputs

Use an Ubuntu 24.04 amd64 host with Python 3.12, Juju 3.6, and initialized local LXD with VM support in its `default` project. Allow 16 GiB RAM, 88 GiB root disks, and two 8 GiB OSD volumes for one cluster, plus controller/build resources. Guests need access to Ubuntu archives, Charmhub, and the Ceph PPA. The sequence destroys each completed cluster before creating the next to limit memory use.

Export the inputs below as absolute paths where applicable. Expected digests must come from the chosen build's record, not arbitrary files on disk. Keep the candidate source revision fixed: changing it requires rebuilt kernel/diagnostic artifacts, new digests, and a new `RUN`. Old results apply only to the old artifacts.

| Variable | Required value |
| --- | --- |
| `IMAGE` | Candidate `linux-image[-unsigned]-RELEASE` package |
| `MODULES` | Matching `linux-modules-RELEASE` package |
| `IMAGE_SHA256`, `MODULES_SHA256` | Expected SHA-256 digests of those packages |
| `STOCK_RELEASE`, `STOCK_VERSION` | Exact installed, signed Ubuntu kernel release and package version without aes256k support; see [case 01](01_stock_limitation/README.md) |

For the existing local build in our environment, copy this block before continuing. These are the candidate image/modules for cases 01–05, not the separate diagnostic kernel built in case 06; the stock values identify the pre-backport kernel already present in our Noble client VMs.

```bash
CANDIDATE_DIR="$HOME/cephx-sru-builds/review-fixes-20261104/candidate"
export IMAGE="$CANDIDATE_DIR/linux-image-unsigned-6.8.0-149-generic_6.8.0-149.149+aes256k4_amd64.deb"
export MODULES="$CANDIDATE_DIR/linux-modules-6.8.0-149-generic_6.8.0-149.149+aes256k4_amd64.deb"
export IMAGE_SHA256=a21a38cc7957ebaa05f9a5a072a95e899367256d013cf84f8b0b37518c49a965
export MODULES_SHA256=b2399c50bf1a72e8102856b5fc10933fb31f7207db8d38d7ded9f004555575cc
export STOCK_RELEASE=6.8.0-147-generic
export STOCK_VERSION=6.8.0-147.147
```

The paths and digests above come from that build's `artifacts.json`; they are a concrete local example, not permanent defaults. If using another build or host, replace them together and check the installed stock version. The next block sets the controller, kernel repository, model names, and diagnostic build directory; change `RUN=review1` to an unused name before each new run.

Start from the repository root. Choose an unused `RUN` and set `KERNEL_REPO` to the repository containing the committed candidate source. Setup refuses existing model names and output directories. Stop on any failure.

```bash
set -euo pipefail
: "${IMAGE:?Export the candidate image package path}"
: "${MODULES:?Export the matching modules package path}"
: "${IMAGE_SHA256:?Export the expected image digest}"
: "${MODULES_SHA256:?Export the expected modules digest}"
: "${STOCK_RELEASE:?Export the installed pre-backport stock release}"
: "${STOCK_VERSION:?Export its exact Ubuntu package version}"
export CONTROLLER=cephx-rbd-controller
export RUN=review1
export KERNEL_REPO="$HOME/work/linux--noble"
export FRESH_MODEL="cephx-sru-${RUN}-fresh"
export UPGRADE_MODEL="cephx-sru-${RUN}-upgraded"
export KERNEL_MODEL="cephx-sru-${RUN}-kernel"
export BUILD_ROOT="$HOME/cephx-sru-builds/$RUN"

sudo apt-get install -y python3-venv build-essential bc bison flex libssl-dev \
  libelf-dev dpkg-dev debhelper rsync cpio kmod zstd git tar jq
cd ceph-mon/tests/cephx_sru
python3 -m venv "$HOME/cephx-sru-venv"
. "$HOME/cephx-sru-venv/bin/activate"
python -m pip install -r requirements.txt
export PYTHONPATH="$PWD${PYTHONPATH:+:$PYTHONPATH}"

# Create the controller only if it is not already registered locally.
controller_exists=$(juju controllers --format=json | python -c \
  'import json, os, sys; print(os.environ["CONTROLLER"] in (json.load(sys.stdin)["controllers"] or {}))')
if [ "$controller_exists" = False ]; then
  juju bootstrap localhost "$CONTROLLER" \
    --bootstrap-base ubuntu@24.04 \
    --bootstrap-constraints 'cores=2 mem=2G root-disk=12G'
fi
```

## Deploy the mixed-key cluster

Setup pins Ceph `19.2.6-0ubuntu0.24.04.1` from `ppa:johnramsden/noble-caracal-ceph-squid-sru`, prepares storage, and boots the client into the candidate kernel. Both legacy `aes` and `aes256k` keys are allowed. Only the client VM has Secure Boot disabled; the host kernel is unchanged.

```bash
python setup_lab.py --controller "$CONTROLLER" --model "$FRESH_MODEL" \
  --image "$IMAGE" --modules "$MODULES" \
  --image-sha256 "$IMAGE_SHA256" --modules-sha256 "$MODULES_SHA256"
export CANDIDATE_ENV="$HOME/cephx-sru-labs/${CONTROLLER}-${FRESH_MODEL}/environment.json"
export STOCK_ENV="${CANDIDATE_ENV%.json}-stock.json"
export SRU_ENVIRONMENT="$CANDIDATE_ENV"
```

## 01: Stock-kernel control

[Case 01 details](01_stock_limitation/README.md). Temporarily boot the pre-backport stock kernel: legacy `aes` RBD/CephFS I/O must work and `aes256k` must fail. The exact stock image/modules must already be installed with trusted Ubuntu archive metadata; the switch helper does not install them.

Setup first verifies the candidate boot. The EXIT trap below restores that same candidate even when the stock test fails, preserving the failure status. Restoration checks candidate packages and reboots only the client; it does not recreate the VM, roll back disks, remove kernels, or change the cluster.

```bash
(
  # Register restoration; it runs when this subshell exits, not here.
  trap '
    result=$?
    python 01_stock_limitation/switch_kernel.py restore \
      --environment "$CANDIDATE_ENV" || exit 1
    exit "$result"
  ' EXIT
  python 01_stock_limitation/switch_kernel.py stock \
    --environment "$CANDIDATE_ENV" --release "$STOCK_RELEASE" \
    --version "$STOCK_VERSION" --output "$STOCK_ENV"
  SRU_ENVIRONMENT="$STOCK_ENV" python -m unittest -v \
    01_stock_limitation/stock_limitation.py
)
```

If restoration fails or the shell is killed, run `python 01_stock_limitation/switch_kernel.py restore --environment "$CANDIDATE_ENV"` before continuing.

## 02: Support and compatibility

[Case 02 details](02_support_compatibility/README.md). On the restored candidate and unchanged mixed-key cluster, compare RBD and CephFS write/read data with both `aes` and `aes256k` credentials.

```bash
python -m unittest -v 02_support_compatibility/compatibility.py
```

## 03: Messenger compatibility

[Case 03 details](03_messenger_compatibility/README.md). Exercise aes256k RBD/CephFS I/O in legacy, crc, and secure modes, verifying the negotiated mode and v1 signing. Prepare debugfs for this case and case 04:

```bash
juju ssh -m "$CONTROLLER:$FRESH_MODEL" ubuntu/0 -- \
  'sudo mountpoint -q /sys/kernel/debug || sudo mount -t debugfs debugfs /sys/kernel/debug'
python -m unittest -v 03_messenger_compatibility/messenger.py
```

## 04: Session recovery

[Case 04 details](04_session_recovery/README.md). Verify uncached reads after remap/remount, OSD/MDS restarts, AUTH-driven renewal, and independent service-ticket renewal before the original AUTH ticket needs renewal. This temporarily changes ticket lifetimes and restarts storage daemons; keep the client free of unrelated RBD mappings and CephFS mounts.

```bash
python -m unittest -v 04_session_recovery/recovery.py
```

## 05: Enforced policy, fresh and upgraded

[Case 05 details](05_enforced_policy/README.md). Rotate admin/daemon keys, enforce aes256k-only authentication, then require legacy rejection and aes256k RBD/CephFS I/O. Run on **both** a fresh cluster and a genuinely upgraded older cluster. First consume the fresh cluster from cases 01–04:

```bash
python 05_enforced_policy/setup_policy.py \
  --environment "$SRU_ENVIRONMENT" --origin fresh --disposable
python -m unittest -v 05_enforced_policy/enforced_policy.py
```

Save the output and environment record. Destroy the completed fresh lab **and all its storage**, not the controller; wait for destruction to finish before deploying the older cluster.

```bash
juju destroy-model "$CONTROLLER:$FRESH_MODEL" --destroy-storage
python setup_lab.py --controller "$CONTROLLER" --model "$UPGRADE_MODEL" \
  --image "$IMAGE" --modules "$MODULES" \
  --image-sha256 "$IMAGE_SHA256" --modules-sha256 "$MODULES_SHA256" \
  --ceph-version 19.2.3-0ubuntu0.24.04.3 --ceph-source distro
export SRU_ENVIRONMENT="$HOME/cephx-sru-labs/${CONTROLLER}-${UPGRADE_MODEL}/environment.json"
python 05_enforced_policy/setup_policy.py \
  --environment "$SRU_ENVIRONMENT" --origin upgraded --disposable
python -m unittest -v 05_enforced_policy/enforced_policy.py
```

After saving the upgraded run's output, destroy that completed lab too:

```bash
juju destroy-model "$CONTROLLER:$UPGRADE_MODEL" --destroy-storage
```

## 06: Kernel crypto prerequisites

[Case 06 details](06_crypto_prerequisites/README.md). Run Kerberos and crypto-manager selftests on a separate test-enabled kernel built from committed `HEAD`, using `x86_64_defconfig` plus the case's `kernel.config` fragment. The builder archives source outside the checkout without modifying it; uncommitted changes are not included. `JOBS` defaults to 2. Do not ship this kernel.

```bash
bash 06_crypto_prerequisites/build_kernel.sh "$KERNEL_REPO" "$BUILD_ROOT/06"
python 06_crypto_prerequisites/setup_client.py \
  --controller "$CONTROLLER" --model "$KERNEL_MODEL" \
  --artifacts "$BUILD_ROOT/06/artifacts.json"
export SRU_ENVIRONMENT="$HOME/cephx-sru-labs/${CONTROLLER}-${KERNEL_MODEL}/environment.json"
python -m unittest -v 06_crypto_prerequisites/crypto_prerequisites.py
```

## 07: Decrypt bounds guard

[Case 07 details](07_bounds_guard/README.md). Reuse the case-06 build and VM. Test-only libceph instrumentation must show `-EINVAL` when valid ciphertext decrypts to less than an encryption header; it does not replace the installed module on disk. This checks the companion bounds fix, not aes256k support itself.

```bash
python 07_bounds_guard/build_instrumentation.py \
  --kernel-build "$BUILD_ROOT/06" --destination "$BUILD_ROOT/07" --jobs 2
SRU_KERNEL_ARTIFACTS="$BUILD_ROOT/07/artifacts.json" python -m unittest -v \
  07_bounds_guard/bounds_guard.py
```

## How to know the run passed

Every setup/build command must exit zero. Every full unittest invocation must exit zero and finish with `OK`, with no skips, failures, or errors. Expected rejection diagnostics in cases 01 and 05 are normal; the final result must still be `OK`. Deployment, module load, or a single selected test is not full coverage.

| Case | Required full-suite result |
| --- | --- |
| 01 | 4 tests, then successful candidate restoration |
| 02 | 4 tests |
| 03 | 6 tests |
| 04 | 6 tests |
| 05 | 1 test on fresh deployment **and** 1 on genuine upgrade |
| 06 | 1 test |
| 07 | 1 test |

Retain terminal output, all environment records under `~/cephx-sru-labs/`, and both kernel artifact manifests. Each case README describes its required evidence. Version/module checks are not exact loaded-binary attestation: use the intended final candidate artifacts for submission. Package pins do not freeze all archive dependencies.

## Finish or diagnose a failure

Tests clean up their own mappings, mounts, identities, and temporary files. Interrupted processes, unavailable machines, or hung kernels may leave resources behind. Stop and inspect a failure, or destroy its explicitly named model and start with a new `RUN`; there is no automatic repair or resume. Case 05 leaves its clusters aes256k-only, unsuitable for cases 01–04.

After case 07, save its output and destroy the dedicated kernel VM:

```bash
juju destroy-model "$CONTROLLER:$KERNEL_MODEL" --destroy-storage
```

Keep or remove the external build directories only after saving the required evidence. Never install the test kernel or instrumented module on the host or a production client.
