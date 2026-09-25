# EPA resource-profile functional tests

This is a **disruptive, opt-in Zaza suite** for disposable models. It changes
profiles, restarts OSDs, resets systemd start-limit counters, and stops/masks one
OSD on the middle rollout host. Do not run it against production or a shared
model. It is deliberately separate from `tests/tests.yaml` and the default CI
bundles: the EPA snap is not yet published to the store.

## Coverage

The suite normalizes to a healthy `balanced` baseline, then verifies:

- `balanced -> minimal -> balanced` rollouts, with fresh generations and every
  host completing before its successor starts;
- real OSD process identity, systemd drop-ins, every observed thread's CPU
  affinity, and disjoint non-preemptive EPA claims;
- a second transition to `minimal`, with the first OSD on the **middle host**
  stopped and masked before the rollout;
- first-host completion, explicit middle-host failure, and no last-host start
  marker or change to its PIDs, claims, affinity or drop-ins;
- a queued `balanced` request that cannot supersede the unfinished generation;
- recovery after unmasking and a config-change retry, **without manually
  starting the OSD**, including a pending restart with an unchanged drop-in;
- completion of the old generation before the new generation starts, with
  host order preserved and no overlap in actual systemd restart windows;
- final Ceph health and allocation state.

Host order is discovered from `(hostname, unit name)`, matching the charm. No
OSD IDs, unit numbering order, IP addresses, generation IDs or LXD instance
names are hard-coded. The test uses Zaza/Juju to execute on units, not `lxc`.
The default bundle provides three separate 8-vCPU VMs, each with one MON and
two loop-backed OSD block devices. Allow roughly 36 GiB RAM and 96 GiB root
storage. Synchronized guest clocks are needed for timestamp comparisons.

This suite isolates the injected failure by resetting the OSD start counters;
it does not test exhaustion of Ceph's 3-starts/30-minute limit. It also does not
test VM power loss, leadership changes, `update-status` immutability, continuous
RADOS traffic, or physical multi-NUMA/SSD placement. Those remain separate
scenarios rather than implicit claims of this test.

## Build and deploy locally

The bundle uses `ch:ceph-mon` from `tentacle/edge`. Build the current ceph-osd
charm for Ubuntu 24.04 and the EPA snap from its local source tree using
Charmcraft/Snapcraft. For example:

```sh
(cd ceph-osd && charmcraft -v pack --use-lxd --platform ubuntu-24.04-amd64)
(cd ~/src/snap-epa-orchestrator && snapcraft -v pack --use-lxd)
```

From the ceph-charms repository root, export **absolute paths** to those two
artifacts. The bundle uses the specified OSD charm and EPA snap directly,
without a store fallback or automatic selection of an old local artifact.

```sh
export TEST_EPA_OSD_CHARM="$PWD/ceph-osd/ceph-osd_ubuntu-24.04-amd64.charm"
export TEST_EPA_SNAP="$HOME/src/snap-epa-orchestrator/epa-orchestrator_2026.1-06648a34_amd64.snap"
# Adjust the snap filename to your actual build output.
export TEST_EPA_ARTIFACTS="$HOME/epa-functest-results"

tox -c ceph-osd -e func-resource-profiles
```

Zaza's strict Jinja rendering rejects missing artifact environment variables.
`TEST_*` variables are passed through by the charm's existing tox configuration.
Select a local LXD Juju controller before invoking the runner if testing locally;
Zaza creates a fresh model on the selected controller. `--keep-model` retains it
for inspection. The bundle's `loop` storage follows the existing Ceph functional
bundles and does not require formatting physical host disks.

The equivalent command from `ceph-osd/` in an environment with the existing
functional test requirements installed is:

```sh
functest-run-suite --keep-model --test-directory tests/resource-profiles
```

## Run against an existing disposable deployment

The model must have the current EPA-enabled charm, the local snap resource
attached, exactly three OSD units on distinct hosts, an OSD on each host, and a
healthy Ceph cluster. Allow enough CPU capacity to satisfy balanced/minimal
without warnings. Do not run other config changes, upgrades or failure tests
concurrently.

From `ceph-osd/`:

```sh
functest-test --model lxd-controller:MY-DISPOSABLE-MODEL \
  --test-directory tests/resource-profiles \
  --tests tests.resource_profiles.ResourceProfileRolloutTest
```

## Evidence and cleanup

Each run writes a unique subdirectory beneath `TEST_EPA_ARTIFACTS` (default
`/tmp/ceph-epa-functest`). It contains snapshots before/after each transition,
MON markers, allocation-action results, systemd journal events, restart windows
and the latest status. Keep the Zaza runner log as well.

Cleanup is registered before mutation. It captures evidence before repair,
unmasks the injected OSD even after assertions fail, resets start counters,
reconciles a healthy managed profile, then restores the original profile and
warning option. Cleanup errors fail the test; inspect the retained model if
repair cannot complete. As documented by the charm, restoring `unmanaged`
retains CPU allocations and drop-ins; it does not undo enforcement or uninstall
EPA. The model is not destroyed by the test.

The test oracle itself has fast unit tests (no Zaza dependency needed):

```sh
python3 -m unittest discover -s ceph-osd/unit_tests \
  -p test_resource_profile_functest.py -v
```
