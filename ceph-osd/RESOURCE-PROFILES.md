# OSD resource allocation profiles

The `performance-profile` config option makes the charm allocate host CPU
resources for the OSDs on a unit and enforce it through an automatic rolling
operation across the application's hosts.
This is the first iteration of the CE148 design: it covers **CPU only**,
counted in **logical CPUs** (SMT siblings are individual CPUs).

## Profiles

| Profile | NVMe/SSD OSD | HDD OSD | NUMA alignment | Unsatisfiable request |
| --- | --- | --- | --- | --- |
| `unmanaged` (default) | - | - | - | - |
| `performance` | 12 logical CPUs | 4 logical CPUs | strict: data device, `block.db`/`block.wal`, Ceph NIC and OSD memory | refused, unit blocked |
| `balanced` | 8 logical CPUs | 2 logical CPUs | data device and OSD memory, best effort | degraded with a warning |
| `minimal` | 2 logical CPUs | 2 logical CPUs | none requested | degraded with a warning |

Counts are **per OSD**. Two OSDs sharing one NVMe drive therefore request
two full allocations; that is a deliberate anti-pattern, not a supported
layout.

Device tiers follow the kernel's rotational flag: rotational devices get
the HDD counts, non-rotational devices (NVMe and SATA/SAS SSD alike) get
the NVMe counts. A device whose flag cannot be read is treated as
rotational, which is the frugal choice. The tier and the NUMA node come
from the OSD's **data** device.

Memory is only handled as a locality hint in this iteration: `performance`
sets systemd `NUMAPolicy=bind`, `balanced` sets `NUMAPolicy=preferred`.
No memory sizing, hugepage accounting, thread/shard tuning, kernel queue
tuning or Ceph configuration is performed, and existing charm tuning
options such as `tune-osd-memory-target` are untouched.

## Strict alignment

`performance` refuses anything it cannot prove. On a host with more than
one NUMA node the unit is blocked when:

* the data device's NUMA node is unknown (for example a virtio disk),
* a `block.db`/`block.wal` device sits on a different node than the data
  device, or
* the Ceph network interface sits on a different node than the data
  device.

A single-NUMA-node host satisfies all of these trivially. An interface
whose NUMA node cannot be determined (virtual interfaces, containers) is
a warning rather than an error, since alignment cannot be violated by
information that does not exist.

The practical consequence on a two-socket host with drives on both
sockets and a single Ceph NIC is that `performance` can never be
satisfied for the drives on the far socket. Use `balanced` there, or
provide a Ceph network interface per socket.

The whole host is planned as a unit: if any OSD cannot be satisfied,
nothing is claimed, no drop-in is written and no OSD is restarted.

## Degradation ladder

`balanced` and `minimal` never block on capacity. Each OSD is attempted
in this order, and each downgrade is logged and reported in the unit
status:

1. the planned NUMA node, at the full count,
2. any node, at the full count,
3. any node, reduced to the CPUs that are still free,
4. nothing: the OSD is left unpinned.

Set `suppress-profile-warnings=true` to keep deviations out of the unit
status message. Errors, drift and unrelated problems are never
suppressed.

## The EPA orchestrator

CPU ownership is recorded with the [EPA
orchestrator](https://github.com/canonical/snap-epa-orchestrator) so that
other cooperating workloads on the host do not claim the same CPUs. The
charm:

* uses an EPA orchestrator that is **already installed**, as-is: neither
  its revision nor its `cpu-pool` is modified;
* otherwise installs the `epa-orchestrator` charm resource, falling back
  to the store channel in `epa-orchestrator-channel`, and then owns the
  CPU pool;
* requires the daemon to advertise `non-preemptive-allocations` and to
  confirm `preemption_policy: non-preemptive` on every claim. Without
  that confirmation the unit blocks rather than taking an allocation that
  another client could reclaim;
* uses one EPA service identity per OSD, `ceph-osd.<id>`.

EPA is **bookkeeping, not enforcement**: it never applies affinity, moves
processes or activates kernel isolation.

### CPU pool

By default EPA only offers the CPUs in
`/sys/devices/system/cpu/isolated`, which is normally empty. When the
charm owns the installation it configures an explicit pool instead:

* every NUMA node contributes CPUs, so NUMA-aligned requests can be
  served wherever an OSD's device lives;
* per node the size is the profile's demand, floored at 20% and capped at
  75% of that node's logical CPUs;
* whole physical cores are preferred, selected from the highest numbered
  cores downwards, and CPU 0 with its SMT siblings is never offered;
* the pool only ever grows. Shrinking it requires coordinated workload
  migration, and EPA rejects pool changes that exclude CPUs it already
  granted.

Note that pool eligibility is accounting, not kernel isolation: nothing
prevents unrelated host processes from running on those CPUs.

## Enforcement and restarts

Each OSD gets a systemd drop-in at
`/etc/systemd/system/ceph-osd@<id>.service.d/50-charm-resource-profile.conf`:

```
[Service]
CPUAffinity=4-15
NUMAPolicy=bind
NUMAMask=0
```

For a managed profile (and while a retained applied allocation exists), the
charm-owned `ceph.conf` also renders these OSD settings **after** user
`config-flags` settings:

```
osd numa auto affinity = false
osd numa node = -1
```

Ceph's automatic or explicitly selected NUMA affinity would otherwise widen
`CPUAffinity` to all CPUs in a NUMA node after the daemon starts. The charm
checks both effective daemon values and that every OSD thread's non-empty
effective CPU mask is contained by the claimed CPUs after restart and during
drift verification. A conflicting effective setting or mask blocks the unit
rather than acknowledging the allocation. On charm upgrade, retained
allocations are restarted and verified once even when their existing drop-in
bytes already match, because the new Ceph setting cannot repair a process that
widened its affinity during a previous start.

Drop-ins take effect on the next start of the unit. The charm automatically
rolls the change across hosts using the host ordering and monitor
`start`/`alive`/`done` handoff used by Ceph release upgrades. Package upgrades
are not performed. The `resource-peers` relation lets the leader freeze an
explicit cohort and a fresh generation for each configuration/inventory
change. This includes units with no OSDs or no changes to apply, which still
acknowledge their turn. Repeating a profile later never reuses old completion
markers.

Only one participant in the application proceeds at a time. On that host,
changed OSDs restart sequentially, waiting for each to report `active` before
continuing. When a strict-profile change must replace an existing EPA claim,
the charm durably records the transition, stops the OSD and confirms it is
stopped **before** releasing any CPUs, then allocates, enforces, restarts and
verifies that OSD before touching the next one. A runtime allocator/restart
failure can therefore leave only the failing OSD down and the host fenced with
a recoverable pending transition; previously migrated OSDs remain running and
are recorded on their new allocations. First allocations are journaled before
EPA mutation, so an incomplete un-enforced claim is cleaned up or left as an
explicit blocked cleanup transition. Retrying the rollout resumes the recorded
transition. Unchanged allocations do not restart OSDs, but the local OSDs must
be active before the host acknowledges completion. Incomplete restarts are
recorded durably and retried even if the drop-in already contains the desired
bytes. This remains automatic, unlike the original CE148 proposal's
operator-initiated restarts.

Unlike the legacy upgrade timeout policy, resource rollouts **fail closed**:
a failed predecessor or a 30-minute wait never grants permission to skip that
host. New requests queue behind the unfinished generation; leader changes
also retain it. After fixing a failed host (for example resetting a systemd
start-limit failure), change the configuration or refresh the charm to retry.
A non-resource option such as `suppress-profile-warnings` can trigger the retry.
Incidental peer notifications do not repeatedly retry a known failed callback.
An unfinished generation is not cancelled by switching to `unmanaged`, and
pending failed restarts must be recovered under a managed profile first.

Do not remove an unfinished participant as a way to bypass a failed rollout;
its successors remain fenced until it completes. Coordination is scoped to
this application's resource changes, not to other applications, manual
service operations or concurrent release upgrades. This is not a distributed
cluster-wide mutex or a guarantee that all placement groups are clean.

## Lifecycle

* `config-changed`, `storage.real` (new OSDs), `upgrade-charm` and
  `post-series-upgrade` request reconciliation. Peer events advance the
  rolling operation, and `start` can resume an interrupted local turn.
  Recalculation is idempotent: an unchanged plan restarts nothing.
* `update-status` only **verifies**. Drift between the applied
  allocation and the drop-ins or EPA's records blocks the unit; nothing
  is allocated or restarted from `update-status`. It also reports stalled
  or failed predecessor handoffs without advancing the rollout.
* `remove-disk` releases that OSD's claim and removes its drop-in.
  Claims for OSDs that no longer exist on the host are released on the
  next recalculation.
* Unit teardown (`stop`) releases every claim this unit owns.
* Switching to `unmanaged` keeps the current allocation and enforcement
  in place. The status records `requested-profile: unmanaged` separately
  from the last applied managed profile so the retained claim, affinity and
  NUMA protection remain verifiable.

## Inspecting the allocation

```
juju run ceph-osd/0 resource-allocation-status
juju run ceph-osd/0 resource-allocation-status dry-run=performance
```

The action is read-only. `dry-run` previews another profile using the
same calculation the charm uses when applying one, without claiming
CPUs, writing drop-ins, restarting OSDs or changing unit status.
Heterogeneous hosts must be previewed per unit.

## Functional tests

An opt-in [Zaza suite](tests/resource-profiles/README.md) exercises live rolling
restarts, a failed middle participant, fencing of successors, and recovery of a
queued request. It requires three disposable OSD hosts and locally built charm
and EPA snap artifacts; it is not part of the default functional-test gate.

## Out of scope in this iteration

* Per-setting overrides of profile values.
* Memory sizing, hugepages, Ceph thread/shard settings, kernel queue
  depth and IRQ placement.
* Coordination with other applications, release upgrades or manual restarts;
  placement-group health admission checks.
