# CephX SRU tests

Host-side Zaza/libjuju helpers prepare disposable Ceph labs and run the numbered SRU cases. Guests do not need pytest or a copied Python test suite.

Never use production or shared labs. Run cases serially; stop and inspect any failure.

## Prepare the host and inputs

Use an Ubuntu 24.04 amd64 host with Python 3.12, Juju 3.6, and initialized local LXD with VM support in its `default` project. Allow 16 GiB RAM, 88 GiB root disks, and two 8 GiB OSD volumes for one cluster, plus controller/build resources. Guests need access to Ubuntu archives, Charmhub, and the Ceph PPA.

Export these inputs before starting; paths must be absolute. For example, `export IMAGE=/path/to/candidate-image.deb`. Expected digests must come from the chosen build's record, not from arbitrary files found on disk.

| Variable | Required value |
| --- | --- |
| `IMAGE` | Candidate `linux-image[-unsigned]-RELEASE` package |
| `MODULES` | Matching `linux-modules-RELEASE` package |
| `IMAGE_SHA256`, `MODULES_SHA256` | Expected SHA-256 digests of those packages |

Run the following blocks in the **same Bash shell**, initially from the repository root. Choose an unused `RUN` value. Setup refuses existing model names and output directories. Stop on any failure; do not continue to later cases.

```bash
set -euo pipefail
: "${IMAGE:?Export the candidate image package path}"
: "${MODULES:?Export the matching modules package path}"
: "${IMAGE_SHA256:?Export the expected image digest}"
: "${MODULES_SHA256:?Export the expected modules digest}"
export CONTROLLER=cephx-rbd-controller
export RUN=review1
export FRESH_MODEL="cephx-sru-${RUN}-fresh"

sudo apt-get install -y python3-venv git
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
export SRU_ENVIRONMENT="$CANDIDATE_ENV"
```

## Finish or diagnose a failure

Interrupted processes, unavailable machines, or hung kernels can leave resources behind. Inspect the explicitly named disposable model or discard it and use a new `RUN`; there is no automatic cluster repair. Save terminal output and environment records before destroying a completed lab.

```bash
juju destroy-model "$CONTROLLER:$FRESH_MODEL" --destroy-storage
```
