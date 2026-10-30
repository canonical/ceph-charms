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
| `performance` | 16 logical CPUs | 4 logical CPUs | strict: data device, `block.db`/`block.wal`, Ceph cluster NIC and OSD memory; public NIC best effort | refused, unit blocked |
| `balanced` | 8 logical CPUs | 2 logical CPUs | data device, OSD memory and Ceph NICs, best effort | degraded with a warning |
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

## Overriding profile resources

The `performance-profile-resources` option maps profile names to the
resources allocated per OSD, replacing the defaults above. Only the `cpus`
resource of `performance` can be overridden in this iteration:

```
juju config ceph-osd performance-profile-resources='{performance: {cpus: 16}}'
juju config ceph-osd \
    performance-profile-resources='{performance: {cpus: {nvme: 16, hdd: 6}}}'
```

A single number applies to every device tier; a mapping overrides only the
tiers it names. The value is YAML (JSON works too). An unknown profile,
resource or tier, or a count that is not a positive integer, blocks the unit
without allocating anything. Like the profile itself, the override applies to
the whole application.

The charm never offers more than 75% of a NUMA node's logical CPUs to the EPA
orchestrator (see [CPU pool](#cpu-pool)), so a count above that is logged as a
warning naming the affected nodes. The count is not refused because of the
warning alone, since an externally managed pool is not capped by the charm. If
the allocator cannot grant the CPUs, `performance` blocks the unit on the
capacity check.

Changing the override is a configuration change like any other: it starts a
new rolling generation, and OSDs whose CPU count changed are stopped, have
their claim replaced and are restarted one at a time. See
[Changing profiles after deployment](#changing-profiles-after-deployment).

## Strict alignment

`performance` refuses anything it cannot prove. On a host with more than
one NUMA node the unit is blocked when:

* the data device's NUMA node is unknown (for example a virtio disk),
* a `block.db`/`block.wal` device sits on a different node than the data
  device, or
* no interface holding a Ceph cluster network address sits on the data
  device's node. An interface that is administratively down does not
  count, and neither does an active-backup bond whose primary leg is on
  another node (see [Candidate addresses](#candidate-addresses)).

A single-NUMA-node host satisfies all of these trivially. When the NUMA
node of every cluster network interface is unknown (virtual interfaces,
containers), the result is a warning rather than an error, since alignment
cannot be violated by information that does not exist. An interface whose
node is unknown is ignored when other interfaces' nodes are known. An
interface spanning several nodes, such as a bond with legs on both
sockets and no primary leg, satisfies the check only with a deviation in
the unit status (see
[Interfaces spanning NUMA nodes](#interfaces-spanning-numa-nodes)).

The public network is not part of the strict requirement, because it is
commonly a single interface shared by the whole host. An OSD without a
public network address on its node is reported as a deviation in the unit
status instead.

The practical consequence on a two-socket host with drives on both
sockets and a single Ceph cluster NIC is that `performance` can never be
satisfied for the drives on the far socket. Use `balanced` there, or
give each socket a cluster network interface with its own address; the
OSDs then bind to the interface on their own socket, as described under
[Network address binding](#network-address-binding).

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

## Network address binding

Under `performance` and `balanced`, an OSD pinned to a NUMA node also uses
a Ceph public and cluster network address on that node, when the host has
one there. This is for hosts with a network interface per socket, each with
an address of its own. Without such addresses nothing changes: every OSD
uses the host-wide `public addr` and `cluster addr` from `ceph.conf`.

### Candidate addresses

The public and cluster networks are handled separately. The candidate
addresses of each are:

* every local address inside the networks listed in `ceph-public-network`
  or `ceph-cluster-network`, when the option is set;
* otherwise, every address Juju reports for the `public` or `cluster`
  binding, not only the primary one;
* in both cases, the host-wide address that the charm writes to
  `ceph.conf`.

Only stable, globally scoped addresses of the same family (IPv4 or IPv6)
as the host-wide address are considered. Link-local, temporary
(privacy extension), deprecated and tentative addresses are ignored.
So are the addresses of interfaces that are administratively down, which
the kernel keeps after `ip link set <interface> down`, even when one is
the host-wide address; each is logged at `INFO` and never replaces a
working address. An interface that is up but has no carrier, or whose
operational state is unknown (common for virtual interfaces), stays a
candidate, so a cable fault does not change the plan.
An interface's NUMA node is that of its PCI device. Bonds, bridges and
VLANs resolve to their member interfaces:

* An active-backup bond with a primary leg is on that leg's node, since
  it sends all traffic over the primary whenever that leg is up. While
  the primary is down the traffic crosses to the backup leg's node; the
  charm does not react to failovers. It follows the primary whatever the
  bond's `primary_reselect` policy, so keep the default `always`: with
  `better` or `failure` the bond can stay on the backup leg after the
  primary recovers.
* Any other bond with legs on several sockets spans them: an
  active-backup bond without a primary uses whichever leg happens to be
  active, and the other modes use all legs. Such an interface counts as
  local to each socket it spans, but is reported (see
  [Interfaces spanning NUMA nodes](#interfaces-spanning-numa-nodes)).

`/sys/class/net/<bond>/bonding/primary` shows the primary leg, and is
empty when none is set. With netplan, set it as `primary: <leg>` in the
bond's `parameters`.

### Choosing an address

For an OSD pinned to NUMA node N, on each network:

1. If no candidate is on node N, the OSD keeps the host-wide address.
2. Otherwise an interface wholly on node N is preferred over one spanning
   nodes, then the host-wide address, then the lowest address.
3. If the choice is the host-wide address, nothing changes. Otherwise the
   OSD's drop-in passes the address to Ceph:

   ```
   Environment="CEPH_ARGS=--public_addr=10.0.1.5 --cluster_addr=10.1.1.5 --ms_bind_before_connect=true"
   ```

Ceph daemons read `CEPH_ARGS` as command line options, which take
precedence over the host-wide addresses in `ceph.conf` for that OSD only.
The addresses decide where the OSD listens and what it advertises, so
traffic that clients and other daemons send to it arrives on the local
interface. `ms_bind_before_connect` also makes the OSD use them as the
source of the connections it opens itself, to monitors, managers and other
OSDs (including replication). Without it the kernel would pick the source
by destination, typically the host-wide address, and that traffic would
leave through the other socket's interface. Whether it leaves through the
local interface depends on source-based routing; see
[Network requirements](#network-requirements).

Only the addresses that differ from the host-wide ones are passed, and
`ms_bind_before_connect` is only set alongside such an address. OSDs that
keep the host-wide addresses, and every OSD under `minimal`, get neither,
so single-NIC hosts get exactly the same drop-ins as before. The option
applies to the whole daemon: when only the cluster address is local, the
OSD's public connections are bound to the host-wide public address, which
is normally the source the kernel would choose anyway. With
`prefer-ipv6` and a configured network, `ceph.conf` names no address and
Ceph selects one itself; in that case the local address is always passed
explicitly.

The address follows the node the OSD's CPUs are pinned to, not its
device. An OSD that is not pinned to a single node keeps the host-wide
addresses: every OSD under `minimal`, and `balanced` OSDs whose device node
is unknown or that were degraded to "any node". A host with a single NUMA
node never gets per-OSD addresses.

### Ceph's `osd_numa_prefer_iface`

Ceph has an option with a similar aim, `osd_numa_prefer_iface`, enabled by
default. The charm does not rely on it, and it does not replace the
addresses above:

* Ceph only consults it for an address that is not set, and only picks
  from `public network` and `cluster network`. The charm sets the
  host-wide addresses in `ceph.conf` (apart from the `prefer-ipv6` case
  above), so the option normally has no effect, and with Juju bindings
  alone there is no network for Ceph to pick from.
* Ceph finds an interface's NUMA node only through
  `/sys/class/net/<interface>/device/numa_node` or a bond's member ports.
  For VLAN interfaces, bridges and virtio NICs that lookup fails, and Ceph
  then ignores locality without saying so. A bond whose ports sit on
  different nodes is local to none, whatever its primary leg.
* It prefers the node of the OSD's devices, and only when the block, DB and
  WAL devices share one, rather than the node the OSD is pinned to.
* Ceph neither logs the address it picks nor reports when none is local,
  and the option does not set `ms_bind_before_connect`.

Addresses passed through `CEPH_ARGS` take precedence, so the charm leaves
the option at its default.

### When no address is local

| Profile | Cluster network | Public network |
| --- | --- | --- |
| `performance` | refused, unit blocked (see [Strict alignment](#strict-alignment)) | host-wide address, deviation in the unit status |
| `balanced` | host-wide address, warning in the log | host-wide address, warning in the log |

When the NUMA nodes of a network's interfaces are unknown, `performance`
reports a deviation and `balanced` logs a warning. When no separate cluster
network is configured, both networks have the same candidates and
`performance` reports only the cluster network check.

### Interfaces spanning NUMA nodes

When no interface on a network is wholly on an OSD's node, but one spans
it, the OSD uses that interface's address, which is not reliably local:

| Profile | Cluster network | Public network |
| --- | --- | --- |
| `performance` | deviation in the unit status | deviation in the unit status |
| `balanced` | warning in the log | warning in the log |

The message names the bond and says why it spans nodes, for example:

```
osd.1: keeping host cluster address 10.1.0.5 on bond0, which spans NUMA nodes 0,1 and is not reliably local to node 1; bond0 is an active-backup bond without a primary leg, so its traffic uses whichever leg is active; set a primary leg to make it local to that leg's node
```

Setting a primary leg puts an active-backup bond on that leg's node. With
`performance`, the OSDs on the other node then fail the strict check, as
they would with a single cluster NIC on the primary's node.

### Logging and inspection

Every decision is logged for each OSD and network: bindings and
host-wide addresses that are already local at `INFO`, fallbacks and
addresses on interfaces spanning NUMA nodes at `WARNING`. For example:

```
osd.3: binding public address 10.0.1.5 on ens2f0, local to NUMA node 1 (from ceph-public-network)
osd.3: no cluster network address on NUMA node 1 (candidates: 10.1.0.5 on ens1f1 (NUMA node 0)); keeping the host cluster address 10.1.0.5
```

The `resource-allocation-status` action lists each network's candidates
under `networks`. For each OSD it reports `desired-public-addr`,
`desired-cluster-addr`, `applied-public-addr` and `applied-cluster-addr`,
where `null` means the host-wide address. `desired-address-binding` holds
the decisions for the planned NUMA node, and `applied-address-binding` those
made when the allocation was applied, on the node in `applied-numa-node`.
They differ when `balanced` degraded an OSD to any node (`applied-mode`
`any`), or left it unpinned: such an OSD keeps the host-wide addresses.

### Enforcement

A change of the chosen address rewrites the drop-in, and the OSD is
restarted in the rolling operation like a CPU change. Address and
interface changes are picked up the next time the charm reconciles (see
[Lifecycle](#lifecycle)).

After each restart, and on `update-status`, the charm reads the OSD's
effective `public_addr`, `cluster_addr` and `ms_bind_before_connect` through
its admin socket and blocks the unit if they differ from the drop-in. One
way this happens is
`/etc/default/ceph` setting its own `CEPH_ARGS`: systemd lets that file
override the drop-in's environment.

### Network requirements

The charm only chooses addresses. It does not configure routing or ARP:

* clients, monitors and other OSDs must be able to reach every per-socket
  address, so per-socket subnets need routes between them;
* traffic sourced from a per-socket address must leave through that
  address's interface. This needs source-based policy routing, for example
  `ip rule add from 10.0.1.5 table 101` with a default route through that
  interface in table 101 (netplan `routing-policy`). Without it, connections
  the OSD opens can leave through another interface with the per-socket
  source address, and peers with strict reverse path filtering
  (`rp_filter=1`) drop them;
* two interfaces in the same subnet additionally need ARP set up by the
  operator (for example `arp_ignore` and `arp_announce`); otherwise the
  other socket's interface can answer for the address;
* every per-socket subnet must be listed in `ceph-public-network` or
  `ceph-cluster-network`, or be part of the space bound to `public` or
  `cluster`.

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
* requires the daemon to advertise `non-preemptive-allocations` and
  `cpu-pools`, and to confirm both `pool: general` and
  `preemption_policy: non-preemptive` on every claim. Without
  that confirmation the unit blocks rather than taking an allocation that
  another client could reclaim;
* uses one EPA service identity per OSD, `ceph-osd.<id>`.

EPA is **bookkeeping, not enforcement**: it never applies affinity, moves
processes or activates kernel isolation.

### CPU pool

EPA defaults requests to the `isolated` pool, even when `cpu-pool` is
configured. The charm explicitly selects `general` for listing, allocation,
and release. An externally managed snap must already have a general pool
configured and its daemon restarted. The charm checks capabilities through
the default isolated listing before attempting any general-pool operation.

When the charm owns the installation it configures the general pool:

* every NUMA node contributes CPUs, so NUMA-aligned requests can be
  served wherever an OSD's device lives;
* per node the size is the profile's demand, floored at 20% and capped at
  75% of that node's logical CPUs;
* whole physical cores are preferred, selected from the highest numbered
  cores downwards, and CPU 0 with its SMT siblings is never offered;
* isolated CPUs are excluded, because EPA requires disjoint pools;
* the pool only ever grows. Shrinking it requires coordinated workload
  migration, and EPA rejects pool changes that exclude CPUs it already
  granted.

Pool changes are journaled before configuring the snap. If configuration or
restart is interrupted, the next managed reconciliation retries activation
before checking daemon readiness, even when profile demand is unchanged.
The pending change is cleared only after the general pool answers successfully.
An old interrupted change made before this journal existed still requires
`sudo snap restart epa-orchestrator.daemon` before retrying reconciliation;
the charm does not infer permission to restart from arbitrary listing errors.

Note that pool eligibility is accounting, not kernel isolation: nothing
prevents unrelated host processes from running on those CPUs.

## Enforcement and restarts

Each OSD gets a systemd drop-in at
`/etc/systemd/system/ceph-osd@<id>.service.d/50-charm-resource-profile.conf`:

```
[Service]
CPUAffinity=
CPUAffinity=4-15
NUMAPolicy=bind
NUMAMask=
NUMAMask=0
```

systemd merges `CPUAffinity` and `NUMAMask` assignments from the unit and its
drop-ins, so the empty assignments discard any mask set before this drop-in.
The drop-in can also carry a `CEPH_ARGS` line with per-OSD network addresses;
see [Network address binding](#network-address-binding).

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
rather than acknowledging the allocation. An OSD whose drop-in is unchanged is
only checked while it runs: a stopped OSD, or one that stops or restarts during
the check, has no effective settings to compare and applies its drop-in when it
starts, so it is skipped with an `INFO` log entry. On charm upgrade, retained
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
verifies that OSD before touching the next one. OSDs that need no more CPUs
on their target NUMA node than they already hold there are replaced first, so
an OSD that shrinks frees CPUs for one that grows. An OSD with an unfinished
replacement or restart comes before both, so a retry recovers it before
stopping any other OSD. Before stopping an OSD the
charm checks that its target node has room for the new count, counting the
OSD's own claim; if not, the replacement is refused and the OSD keeps running
on its current allocation. This can happen when OSDs swap NUMA nodes and
neither fits until the other has moved. Any other runtime allocator/restart
failure can leave only the failing OSD down and the host fenced with
a recoverable pending transition; previously migrated OSDs remain running and
are recorded on their new allocations. First allocations are journaled before
EPA mutation, so an incomplete un-enforced claim is cleaned up or left as an
explicit blocked cleanup transition. Retrying the rollout resumes the recorded
transition. If an OSD cannot be confirmed stopped, its claim is kept and the
unit stays blocked; every retry must stop it again before the allocation
changes. Unchanged allocations do not restart OSDs, but the local OSDs must
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

## Changing profiles after deployment

Set `performance-profile` and `performance-profile-resources` when deploying
the application. Changing either later is supported, but treat it as a
maintenance operation rather than online tuning:

* Every OSD whose allocation changes is restarted, one host at a time and one
  OSD at a time on each host. Under `performance` each of these OSDs is down
  while its claim is replaced. The rollout takes about one OSD restart per
  changed OSD in the application, which can be hours on a large cluster.
* Between OSDs and between hosts the rollout only waits for the restarted OSD
  to report `active`. It does not set `noout` and does not wait for
  placement groups to be clean.
* A change can be refused for an OSD whose target NUMA node lacks free CPUs
  for the new count. That OSD keeps running on its current allocation and
  the unit is blocked.
* Any failure stops the rollout and fences the remaining hosts until it is
  fixed and retried, as described in
  [Enforcement and restarts](#enforcement-and-restarts).

Plan such a change for a maintenance window. The charm deliberately does not
try to keep a reconfiguration fully online, for example by restarting a
host's OSDs in parallel or by waiting for cluster health between hosts.

## Lifecycle

* `config-changed`, `storage.real` (new OSDs), the `add-disk` action,
  `upgrade-charm` and `post-series-upgrade` request reconciliation. Peer
  events advance the
  rolling operation, and `start` can resume an interrupted local turn.
  Recalculation is idempotent: an unchanged plan restarts nothing.
* `update-status` only **verifies**. Drift between the applied
  allocation and the drop-ins, EPA's records or a running OSD blocks the
  unit; OSDs that are not running are skipped. Nothing is allocated or
  restarted from `update-status`. It also reports stalled
  or failed predecessor handoffs without advancing the rollout.
* `remove-disk` releases that OSD's claim and removes its drop-in.
  Claims, drop-ins and records for OSDs that no longer exist on the host
  are released on the next recalculation, including when no OSD remains;
  an OSD whose systemd unit is still active keeps its claim.
* Unit teardown (`stop`) stops each OSD with a recorded allocation, drop-in
  or claim, then releases
  every claim this unit owns. If a release fails the hook fails, so that
  the claims are not orphaned; resolving the unit retries the cleanup.
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

* Resource overrides other than the CPU count of the `performance`
  profile.
* Memory sizing, hugepages, Ceph thread/shard settings, kernel queue
  depth and IRQ placement.
* Network routing and ARP configuration for per-socket addresses.
* Coordination with other applications, release upgrades or manual restarts;
  placement-group health admission checks.
