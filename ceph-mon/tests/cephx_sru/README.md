# CephX SRU tests

Run these seven cases in order on disposable labs. Python tests run on the host through Zaza/libjuju; guests do not need pytest or a copied Python test suite. The numbered READMEs explain each case's prerequisites, evidence, and limits. The numbering follows https://bugs.launchpad.net/ubuntu/+source/linux/+bug/2171558.

**Never use production or shared labs.** Cases 01 and 04 reboot clients or restart daemons. Case 05 permanently rotates credentials and enforces aes256k-only authentication. Cases 06–07 use a separate, non-shipping kernel. Do not run cases concurrently.

## Prepare the host and inputs

Use an Ubuntu 24.04 amd64 host with Python 3.12, Juju 3.6, and initialized local LXD with VM support in its `default` project. Allow 16 GiB RAM, 88 GiB root disks, and two 8 GiB OSD volumes for one cluster, plus controller/build resources. Guests need access to Ubuntu archives, Charmhub, and the Ceph PPA. The sequence destroys each completed cluster before creating the next to limit memory use.

Export these inputs before starting; paths must be absolute. For example, `export IMAGE=/path/to/candidate-image.deb`. Expected digests must come from the chosen build's record, not from arbitrary files found on disk.

| Variable | Required value |
| --- | --- |
| `IMAGE` | Candidate `linux-image[-unsigned]-RELEASE` package |
| `MODULES` | Matching `linux-modules-RELEASE` package |
| `IMAGE_SHA256`, `MODULES_SHA256` | Expected SHA-256 digests of those packages |
| `STOCK_RELEASE`, `STOCK_VERSION` | Exact installed, signed Ubuntu kernel release and package version without aes256k support; see [case 01](01_stock_limitation/README.md) |

Run the following blocks in the **same Bash shell**, initially from the repository root. Choose an unused `RUN` value and set `KERNEL_REPO` to the kernel repository containing the committed candidate source. Setup refuses existing model names and output directories. Stop on any failure; do not continue to later cases.

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
  libelf-dev dpkg-dev debhelper rsync cpio kmod zstd git tar
cd ceph-mon/tests/cephx_sru
python3 -m venv "$HOME/cephx-sru-venv"
. "$HOME/cephx-sru-venv/bin/activate"
python -m pip install -r requirements.txt
export PYTHONPATH="$PWD${PYTHONPATH:+:$PYTHONPATH}"
python -m unittest discover -v -s unit_tests

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

Setup pins Ceph `19.2.6-0ubuntu0.24.04.1` from `ppa:johnramsden/noble-caracal-ceph-squid-sru`, prepares storage, and boots the client into the candidate kernel. Only that VM has Secure Boot disabled; the host kernel is unchanged.

```bash
python setup_lab.py --controller "$CONTROLLER" --model "$FRESH_MODEL" \
  --image "$IMAGE" --modules "$MODULES" \
  --image-sha256 "$IMAGE_SHA256" --modules-sha256 "$MODULES_SHA256"
export CANDIDATE_ENV="$HOME/cephx-sru-labs/${CONTROLLER}-${FRESH_MODEL}/environment.json"
export STOCK_ENV="${CANDIDATE_ENV%.json}-stock.json"
export SRU_ENVIRONMENT="$CANDIDATE_ENV"
```

## 01: Stock-kernel control

[Case 01 details](01_stock_limitation/README.md). **The stock boot is a temporary negative control, not a rollback.** The shared launcher first boots the candidate to verify its installation and record the candidate environment. Case 01 reuses that lab rather than needing a separate setup workflow.

Case 01 then boots the pre-backport stock kernel: legacy `aes` must still work, while `aes256k` must fail because that kernel lacks support. Afterward, `restore` checks the installed candidate packages, selects the candidate in the bootloader, and reboots only the client for cases 02–05. It does not delete/recreate the VM, roll back its disk, remove either kernel, or change the Ceph cluster. The later `juju destroy-model ... --destroy-storage` commands explicitly delete the completed labs and their storage.

The stock image/modules must already be installed and have trusted Ubuntu archive metadata; the switch helper does not install them. The `trap ... EXIT` below only **registers** restoration when encountered; it does not run `restore` immediately. Restoration runs when the subshell exits, after the stock commands, even if they fail; failures still propagate.

```bash
(
  # Register cleanup now; restore runs after the stock commands, on subshell exit.
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

[Case 02 details](02_support_compatibility/README.md). Run on the restored candidate and the unchanged mixed-key cluster.

```bash
python -m unittest -v 02_support_compatibility/compatibility.py
```

## 03: Messenger compatibility

[Case 03 details](03_messenger_compatibility/README.md). Prepare debugfs on the dedicated client; both this case and case 04 use it for kernel evidence.

```bash
juju ssh -m "$CONTROLLER:$FRESH_MODEL" ubuntu/0 -- \
  'sudo mountpoint -q /sys/kernel/debug || sudo mount -t debugfs debugfs /sys/kernel/debug'
python -m unittest -v 03_messenger_compatibility/messenger.py
```

## 04: Session recovery

[Case 04 details](04_session_recovery/README.md). Keep the client free of other RBD mappings and CephFS mounts. This case temporarily changes ticket lifetimes and restarts the storage daemons.

```bash
python -m unittest -v 04_session_recovery/recovery.py
```

## 05: Enforced policy, fresh and upgraded

[Case 05 details](05_enforced_policy/README.md). Both runs are required. First consume the fresh cluster used above:

```bash
python 05_enforced_policy/setup_policy.py \
  --environment "$SRU_ENVIRONMENT" --origin fresh --disposable
python -m unittest -v 05_enforced_policy/enforced_policy.py
```

Save the output and environment record. The next command **permanently destroys the completed fresh lab and its storage**, not the controller. Wait for it to finish before deploying the older cluster.

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

[Case 06 details](06_crypto_prerequisites/README.md). Build a separate test-enabled kernel from the kernel repository's committed `HEAD`; uncommitted changes are not included. The build uses an external source archive, not a worktree, and never modifies the original checkout. Do not ship this kernel.

```bash
python 06_crypto_prerequisites/build_kernel.py \
  --repo "$KERNEL_REPO" --destination "$BUILD_ROOT/06" --jobs 2
python 06_crypto_prerequisites/setup_client.py \
  --controller "$CONTROLLER" --model "$KERNEL_MODEL" \
  --artifacts "$BUILD_ROOT/06/artifacts.json"
export SRU_ENVIRONMENT="$HOME/cephx-sru-labs/${CONTROLLER}-${KERNEL_MODEL}/environment.json"
python -m unittest -v 06_crypto_prerequisites/crypto_prerequisites.py
```

## 07: Decrypt bounds guard

[Case 07 details](07_bounds_guard/README.md). Reuse the case-06 build and VM. This adds test-only instrumentation to the archived libceph source; it does not replace the installed module on disk.

```bash
python 07_bounds_guard/build_instrumentation.py \
  --kernel-build "$BUILD_ROOT/06" --destination "$BUILD_ROOT/07" --jobs 2
SRU_KERNEL_ARTIFACTS="$BUILD_ROOT/07/artifacts.json" python -m unittest -v \
  07_bounds_guard/bounds_guard.py
```

## How to know the run passed

Every setup/build command must exit zero. Every full unittest invocation must exit zero and finish with `OK`, without skips, failures, or errors. Expected rejection diagnostics inside cases 01 and 05 are normal; their final unittest result must still be `OK`. A single selected test, successful deployment, or successful module load is not complete coverage.

| Case | Required full-suite result |
| --- | --- |
| 01 | 4 tests, then successful candidate restoration |
| 02 | 4 tests |
| 03 | 6 tests |
| 04 | 4 tests |
| 05 | 1 test on fresh deployment **and** 1 on genuine upgrade |
| 06 | 1 test |
| 07 | 1 test |

Retain terminal output, the environment records under `~/cephx-sru-labs/`, and both kernel artifact manifests. The numbered READMEs explain the evidence each test requires. Version/module checks are not exact loaded-binary attestation; run the intended final candidate artifacts for submission. Package pins do not freeze all archive dependencies.

## Finish or diagnose a failure

Successful tests clean up their scoped mappings, mounts, identities, and temporary files. Interrupted processes, unavailable machines, or hung kernels can leave resources behind. Stop and inspect a failed run, or discard its explicitly named model and start with a new `RUN`; there is no automatic repair or cluster-resume engine. Case 05 leaves successful clusters aes256k-only, so they cannot be reused for cases 01–04.

After case 07, save its output and destroy the dedicated kernel VM:

```bash
juju destroy-model "$CONTROLLER:$KERNEL_MODEL" --destroy-storage
```

Keep or remove the external build directories only after saving the required evidence. Never install the test kernel or instrumented module on the host or a production client.
