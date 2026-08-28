#!/usr/bin/env bash
#
# cephx-upgrade-demo.sh
#
# Demonstrate how the CVE-2025-30156 cephx rework (new aes256k key type,
# Ceph 19.2.6) behaves during package upgrades from 19.2.3, and where
# things break when the order is wrong.
#
# The demo builds a small package-based cluster in LXD containers:
#
#   cephx-mon-a, cephx-mon-b, cephx-mon-c   3 monitors, 19.2.3
#   cephx-osd-1                             1 OSD node (file-backed bluestore)
#   cephx-client                            a client that is NEVER upgraded
#
# After the baseline cluster is built and verified, every container is
# snapshotted. Each scenario is ISOLATED: it starts by restoring all
# containers to that snapshot, so scenarios never depend on each other
# and can be run individually and in any order.
#
# Scenarios:
#   osd-first            upgrade the OSD node before the mons
#   one-mon              upgrade a single non-leader mon and stop there
#   leader-flip          acceptance depends on which mon leads; a
#                        new-type key crashes the remaining old mons,
#                        and upgrading them recovers the cluster
#   mons-first           the recommended order: no breakage either way
#   unordered            any upgrade order keeps the cluster running,
#                        as long as no key is created or rotated
#   provisioning-matrix  which key-creation paths are safe mid-upgrade
#   fresh-cluster        a brand-new 19.2.6 cluster vs an old client
#
# With the default (classic) election strategy the lowest-ranked mon in
# quorum is always the leader, and ranks are fixed when the monmap is
# created (in practice they follow the mons' addresses, not their names).
# The script therefore detects rank 0/1/2 at runtime after each restore
# instead of assuming which mon leads.
#
# Requirements: LXD initialised, network access to the Ubuntu archive and
# to NEW_SOURCE. Run as root (or a user in the lxd group).
#
# Tunables (environment variables):
#   OLD_VERSION  pinned pre-fix version   (default 19.2.3-0ubuntu0.24.04.3)
#   NEW_SOURCE   apt source with 19.2.6   (default the SRU staging PPA)
#   PREFIX       container name prefix    (default cephx)
#   SOAK_SECS    length of the osd-first soak loop (default 180)
#
# Usage:
#   cephx-upgrade-demo.sh                  run setup + all scenarios
#   cephx-upgrade-demo.sh setup            build baseline + snapshot only
#   cephx-upgrade-demo.sh <scenario>...    run the named scenarios
#   cephx-upgrade-demo.sh --clean          delete the demo containers

set -euo pipefail

OLD_VERSION=${OLD_VERSION:-19.2.3-0ubuntu0.24.04.3}
NEW_SOURCE=${NEW_SOURCE:-ppa:johnramsden/noble-caracal-ceph-squid-sru}
PREFIX=${PREFIX:-cephx}
SOAK_SECS=${SOAK_SECS:-180}
OLD_GLOB="${OLD_VERSION%%-*}*"

MONS=("$PREFIX-mon-a" "$PREFIX-mon-b" "$PREFIX-mon-c")
OSD_NODE="$PREFIX-osd-1"
CLIENT="$PREFIX-client"
ALL=("${MONS[@]}" "$OSD_NODE" "$CLIENT")
BASE="$PREFIX-base"
SNAP=baseline
SCENARIOS=(osd-first one-mon leader-flip mons-first unordered provisioning-matrix fresh-cluster)

WORKDIR=$(mktemp -d)
trap 'rm -rf "$WORKDIR"' EXIT
RESULTS=()
# Node used for cluster-state probes (quorum, leader, osd tree). Normally
# the client; the fresh-cluster scenario switches it to the upgraded OSD
# node because the old client cannot talk to that cluster at all.
CEPH_NODE=$CLIENT

banner() {
    printf '\n============================================================\n'
    printf '== %s\n' "$*"
    printf '============================================================\n'
}

describe() {
    printf '\n'
    printf '%s\n' "$@" | fold -s -w 70 | sed 's/^/   | /'
    printf '\n'
}

note() {
    printf -- '-- %s\n' "$*"
}

record() {
    RESULTS+=("$*")
    printf 'RESULT: %s\n' "$*"
}

run() {  # run <container> <command...>
    local c=$1
    shift
    lxc exec "$c" -- bash -c "$*"
}

# Run a command that MUST fail with the given message on stderr.
expect_fail() {  # expect_fail <container> <expected-substring> <command...>
    local c=$1 expect=$2
    shift 2
    local out rc=0
    out=$(lxc exec "$c" -- bash -c "$*" 2>&1) || rc=$?
    if [ "$rc" -eq 0 ]; then
        echo "ERROR: command unexpectedly succeeded: $*"
        echo "$out"
        return 1
    fi
    if ! grep -q "$expect" <<< "$out"; then
        echo "ERROR: command failed but without expected message '$expect':"
        echo "$out"
        return 1
    fi
    note "failed as expected with: $(grep -m1 "$expect" <<< "$out" | sed 's/^ *//')"
}

wait_for() {  # wait_for <description> <timeout-seconds> <container> <command...>
    local desc=$1 timeout=$2 c=$3
    shift 3
    local waited=0
    # Each attempt gets its own timeout: ceph commands block indefinitely
    # when the cluster has no quorum, which would hang the poll loop.
    until lxc exec "$c" -- timeout 45 bash -c "$*" > /dev/null 2>&1; do
        sleep 5
        waited=$((waited + 5))
        if [ "$waited" -ge "$timeout" ]; then
            echo "ERROR: timed out waiting for: $desc"
            lxc exec "$c" -- timeout 45 bash -c "$*" || true
            return 1
        fi
    done
    note "$desc"
}

container_ip() {
    lxc list "$1" -f csv -c 4 | head -1 | awk '{print $1}'
}

quorum_check="ceph quorum_status -f json | python3 -c 'import json,sys; sys.exit(0 if len(json.load(sys.stdin)[\"quorum\"]) == %d else 1)'"

wait_quorum() {  # wait_quorum <size>
    # shellcheck disable=SC2059
    wait_for "mon quorum of $1 formed" 180 "$CEPH_NODE" "$(printf "$quorum_check" "$1")"
}

leader() {
    run "$CEPH_NODE" "ceph quorum_status -f json" |
        grep -oP '"quorum_leader_name":\s*"\K[^"]+'
}

mon_rank() {  # mon_rank <n>: name of the mon holding rank n
    run "$CEPH_NODE" "ceph mon dump 2>/dev/null" | grep -oP "^$1: .*mon\.\K\S+"
}

wait_leader() {  # wait_leader <mon-name>
    wait_for "quorum leader is $1" 180 "$CEPH_NODE" \
        "ceph quorum_status -f json | grep -q '\"quorum_leader_name\":\"$1\"'"
}

# gen_key <node> [cipher]: generate a cephx secret with that node's tools.
gen_key() {
    local node=$1 type=${2:-}
    if [ -n "$type" ]; then
        # Note: 19.2.6 ceph-authtool advertises -t in its usage text but its
        # argument parser only accepts the long form --key-type.
        run "$node" "ceph-authtool --gen-print-key --key-type $type"
    else
        run "$node" "ceph-authtool --gen-print-key"
    fi
}

# try_osd_new <node> <key> [extra ceph args]: the mon-side registration
# step that ceph-volume performs internally when creating an OSD.
try_osd_new() {
    local node=$1 key=$2 extra=${3:-}
    run "$node" "echo '{\"cephx_secret\": \"$key\"}' | ceph $extra osd new \$(uuidgen) -i -"
}

# make_osd <node> <id> [cipher]: full OSD bring-up (register + mkfs + start).
make_osd() {
    local node=$1 id=$2 type=${3:-} key uuid
    key=$(gen_key "$node" "$type")
    uuid=$(run "$node" "uuidgen")
    run "$node" "echo '{\"cephx_secret\": \"$key\"}' | ceph osd new $uuid -i -"
    run "$node" "mkdir -p /var/lib/ceph/osd/ceph-$id &&
        ceph-osd -i $id --mkfs --osd-uuid $uuid --no-mon-config &&
        ceph-authtool --create-keyring /var/lib/ceph/osd/ceph-$id/keyring --name osd.$id --add-key '$key' &&
        chown -R ceph:ceph /var/lib/ceph/osd &&
        systemctl enable --now ceph-osd@$id"
    wait_for "osd.$id is up" 120 "$CEPH_NODE" "ceph osd tree | grep -E 'osd.$id\s.*up'"
}

io_check() {  # io_check <node> <tag>: prove that node can read and write data
    run "$1" "echo payload-$2 > /tmp/obj &&
        rados -p demo put obj-$2 /tmp/obj &&
        rados -p demo get obj-$2 - | grep -q payload-$2"
    note "I/O OK from $1 ($2) -- $(run "$1" "ceph health" | head -1)"
}

soak() {  # soak <seconds> <tag>: repeated I/O from the old client. With
    # auth_service_ticket_ttl=120 the client renews its tickets roughly
    # every 90 seconds, so anything over ~200s covers two renewals.
    local secs=$1 tag=$2 elapsed=0
    note "soaking for ${secs}s (client renews auth tickets ~every 90s)"
    while [ "$elapsed" -lt "$secs" ]; do
        run "$CLIENT" "rados -p demo put obj-soak /etc/hostname && rados -p demo get obj-soak - > /dev/null"
        sleep 15
        elapsed=$((elapsed + 15))
    done
    note "I/O stayed healthy for ${secs}s ($tag)"
}

upgrade_node() {  # upgrade_node <container>
    note "upgrading $1 to the packages from $NEW_SOURCE"
    run "$1" "rm -f /etc/apt/preferences.d/zz-cephx-demo-old"
    run "$1" "DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a apt-get install -y ceph-mon ceph-osd ceph-common > /dev/null"
    run "$1" "dpkg-query -W -f='\${Version}' ceph-common | grep -qv '^${OLD_VERSION%%-*}-'"
}

upgrade_mon() {  # upgrade_mon <container>: upgrade + restart + wait for quorum
    upgrade_node "$1"
    run "$1" "systemctl restart ceph-mon@$1"
    wait_quorum 3
}

clean() {
    for c in "${ALL[@]}" "$BASE"; do
        lxc delete -f "$c" 2> /dev/null || true
    done
}

# ---------------------------------------------------------------------------
# Setup: build the 19.2.3 baseline cluster once and snapshot every
# container. Scenarios restore these snapshots to start from scratch.
# ---------------------------------------------------------------------------
setup() {
    banner "Setup: base container with ceph $OLD_VERSION (new packages cached)"
    clean
    lxc launch ubuntu:24.04 "$BASE"
    wait_for "cloud-init finished" 180 "$BASE" "cloud-init status --wait"
    run "$BASE" "cat > /etc/apt/preferences.d/zz-cephx-demo-old <<'EOF'
Package: ceph* librados* librbd* libcephfs* librgw* libsqlite3-mod-ceph python3-ceph* python3-rados python3-rbd rados*
Pin: version $OLD_GLOB
Pin-Priority: 1001
EOF"
    run "$BASE" "DEBIAN_FRONTEND=noninteractive add-apt-repository -y '$NEW_SOURCE' > /dev/null"
    run "$BASE" "DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a apt-get install -y ceph-mon ceph-osd ceph-common > /dev/null"
    run "$BASE" "dpkg-query -W -f='ceph-common \${Version}\n' ceph-common | grep -F '$OLD_VERSION'"
    # Pre-download the 19.2.6 debs (pin lifted only for the download) so
    # per-scenario upgrades after a snapshot restore are fast.
    run "$BASE" "mv /etc/apt/preferences.d/zz-cephx-demo-old /root/pin.bak &&
        apt-get -d -y install ceph-mon ceph-osd ceph-common > /dev/null &&
        mv /root/pin.bak /etc/apt/preferences.d/zz-cephx-demo-old"
    lxc stop "$BASE"

    for c in "${ALL[@]}"; do
        lxc copy "$BASE" "$c"
        lxc start "$c"
    done
    for c in "${ALL[@]}"; do
        wait_for "$c is up" 120 "$c" "systemctl is-system-running --wait || true"
        run "$c" "hostnamectl set-hostname $c"
    done

    banner "Setup: bootstrapping a 3-mon cluster on $OLD_VERSION"
    build_cluster "$OLD_VERSION"
    run "$CLIENT" "ceph osd pool create demo 8 8"
    io_check "$CLIENT" setup

    banner "Setup: snapshotting the baseline"
    for c in "${ALL[@]}"; do
        run "$c" "systemctl stop ceph.target ceph-mon.target ceph-osd.target 2>/dev/null || true"
        lxc stop "$c"
        lxc snapshot "$c" "$SNAP"
        lxc start "$c"
    done
    wait_quorum 3
    wait_for "osd.0 is up" 180 "$CLIENT" "ceph osd tree | grep -E 'osd.0\s.*up'"
    note "baseline snapshot '$SNAP' taken on all containers"
}

# build_cluster <version-label>: manual mon bootstrap + one OSD, using
# whatever ceph packages are installed on the containers right now.
build_cluster() {
    local fsid mon_ips=() args="" i
    fsid=$(run "${MONS[0]}" "uuidgen")
    for m in "${MONS[@]}"; do
        mon_ips+=("$(container_ip "$m")")
    done

    cat > "$WORKDIR/ceph.conf" <<EOF
[global]
fsid = $fsid
mon host = $(IFS=,; echo "${mon_ips[*]}")
auth_cluster_required = cephx
auth_service_required = cephx
auth_client_required = cephx
auth_service_ticket_ttl = 120
osd pool default size = 2
osd pool default min size = 1
osd crush chooseleaf type = 0
[osd]
osd memory target = 939524096
bluestore block size = 2147483648
EOF
    for c in "${ALL[@]}"; do
        lxc file push -q "$WORKDIR/ceph.conf" "$c/etc/ceph/ceph.conf"
    done

    run "${MONS[0]}" "ceph-authtool --create-keyring /tmp/mon.keyring --gen-key -n mon. --cap mon 'allow *'"
    run "${MONS[0]}" "ceph-authtool --create-keyring /etc/ceph/ceph.client.admin.keyring --gen-key -n client.admin --cap mon 'allow *' --cap osd 'allow *' --cap mds 'allow *' --cap mgr 'allow *'"
    run "${MONS[0]}" "ceph-authtool /tmp/mon.keyring --import-keyring /etc/ceph/ceph.client.admin.keyring"
    for i in "${!MONS[@]}"; do
        args+=" --add ${MONS[$i]} ${mon_ips[$i]}"
    done
    run "${MONS[0]}" "monmaptool --create --clobber --fsid $fsid $args /tmp/monmap"

    lxc file pull -q "${MONS[0]}/tmp/mon.keyring" "$WORKDIR/mon.keyring"
    lxc file pull -q "${MONS[0]}/tmp/monmap" "$WORKDIR/monmap"
    lxc file pull -q "${MONS[0]}/etc/ceph/ceph.client.admin.keyring" "$WORKDIR/admin.keyring"

    for m in "${MONS[@]}"; do
        lxc file push -q "$WORKDIR/mon.keyring" "$m/tmp/mon.keyring"
        lxc file push -q "$WORKDIR/monmap" "$m/tmp/monmap"
        run "$m" "rm -rf /var/lib/ceph/mon/ceph-$m &&
            mkdir -p /var/lib/ceph/mon/ceph-$m &&
            ceph-mon --mkfs -i $m --monmap /tmp/monmap --keyring /tmp/mon.keyring &&
            chown -R ceph:ceph /var/lib/ceph/mon &&
            systemctl enable --now ceph-mon@$m"
    done
    for c in "$OSD_NODE" "$CLIENT"; do
        lxc file push -q "$WORKDIR/admin.keyring" "$c/etc/ceph/ceph.client.admin.keyring"
    done
    wait_quorum 3
    run "$CEPH_NODE" "ceph mon enable-msgr2 || true"
    make_osd "$OSD_NODE" 0
}

have_snapshot() {
    lxc query "/1.0/instances/${MONS[0]}/snapshots" 2>/dev/null | grep -q "/$SNAP\""
}

restore_baseline() {
    CEPH_NODE=$CLIENT
    note "restoring all containers to the '$SNAP' snapshot"
    # Stop everything before restoring anything: a freshly restored mon
    # that boots while other mons still run their pre-restore state will
    # sync that state right back in.
    for c in "${ALL[@]}"; do
        lxc stop -f "$c" 2>/dev/null || true
    done
    for c in "${ALL[@]}"; do
        lxc restore "$c" "$SNAP"
    done
    for c in "${ALL[@]}"; do
        lxc start "$c"
    done
    wait_quorum 3
    # Ranks are fixed in the monmap; detect who leads (rank 0) and who
    # would take over (rank 1) instead of assuming.
    LEAD_MON=$(mon_rank 0)
    NEXT_MON=$(mon_rank 1)
    THIRD_MON=$(mon_rank 2)
    wait_leader "$LEAD_MON"
    note "mon ranks: leader=$LEAD_MON, next=$NEXT_MON, third=$THIRD_MON"
    wait_for "osd.0 is up" 180 "$CLIENT" "ceph osd tree | grep -E 'osd.0\s.*up'"
    io_check "$CLIENT" restore
}

# ---------------------------------------------------------------------------
# Scenarios. Each starts from the restored baseline.
# ---------------------------------------------------------------------------
scenario_osd_first() {
    banner "Scenario: osd-first"
    describe \
        "We upgrade the OSD machine before the mons. This is the wrong" \
        "order. The cluster keeps serving reads and writes the whole" \
        "time, because everything already running keeps using its old" \
        "key. But adding a NEW disk from the upgraded machine fails:" \
        "its new tools create a key type the old mons do not understand."
    upgrade_node "$OSD_NODE"
    run "$OSD_NODE" "systemctl restart ceph-osd@0"
    wait_for "restarted 19.2.6 osd.0 rejoined with its old key" 120 "$CLIENT" \
        "ceph osd tree | grep -E 'osd.0\s.*up'"
    soak "$SOAK_SECS" "old mons, new OSD node"
    [ "$(leader)" = "$LEAD_MON" ] || { echo "ERROR: unexpected leader"; return 1; }
    KEY=$(gen_key "$OSD_NODE")
    expect_fail "$OSD_NODE" "invalid cephx secret" \
        "echo '{\"cephx_secret\": \"$KEY\"}' | ceph osd new \$(uuidgen) -i -"
    record "osd-first: I/O fine throughout, new-OSD registration rejected (invalid cephx secret)"
}

scenario_one_mon() {
    banner "Scenario: one-mon"
    describe \
        "We upgrade a single mon (a non-leader) and stop there. Nothing" \
        "breaks and nothing changes: every key in use is still the old" \
        "type, and old machines can even keep adding disks. Then we also" \
        "upgrade the OSD machine. Now adding a disk from it fails," \
        "because write commands are decided by the lead mon, which is" \
        "still old. Asking the upgraded mon directly does not help: it" \
        "forwards the command to the leader."
    upgrade_mon "$THIRD_MON"
    io_check "$CLIENT" one-mon-upgraded
    make_osd "$OSD_NODE" 1
    note "provisioning from the still-old OSD node works: old tools make old-type keys"
    io_check "$CLIENT" osd1-added
    upgrade_node "$OSD_NODE"
    [ "$(leader)" = "$LEAD_MON" ] || { echo "ERROR: unexpected leader"; return 1; }
    KEY=$(gen_key "$OSD_NODE")
    expect_fail "$OSD_NODE" "invalid cephx secret" \
        "echo '{\"cephx_secret\": \"$KEY\"}' | ceph osd new \$(uuidgen) -i -"
    note "same command pointed at the upgraded mon ($(container_ip "$THIRD_MON")): still fails"
    KEY=$(gen_key "$OSD_NODE")
    expect_fail "$OSD_NODE" "invalid cephx secret" \
        "echo '{\"cephx_secret\": \"$KEY\"}' | ceph -m $(container_ip "$THIRD_MON") osd new \$(uuidgen) -i -"
    record "one-mon: mixed mon quorum is harmless on its own; new-type keys still rejected by the old leader"
}

scenario_leader_flip() {
    banner "Scenario: leader-flip"
    describe \
        "Whether a new-type key is accepted depends on ONE mon: the" \
        "current leader. We upgrade the second-ranked mon and the OSD" \
        "machine. While the old leader runs, adding a disk fails. We" \
        "stop the old leader: the upgraded mon takes over, and the same" \
        "command now works. But this is a trap, not a win. The new key" \
        "lands in the shared key database, and every mon still on the" \
        "old version CRASHES when it reads it - and cannot start again." \
        "Only the upgraded mon survives, and one mon out of three is" \
        "not a working cluster. Never create new-type keys while any" \
        "old mon remains. The damage is not permanent though: only the" \
        "old program chokes on the key, the database itself is fine, so" \
        "upgrading the crashed mons brings the cluster back."
    upgrade_mon "$NEXT_MON"
    upgrade_node "$OSD_NODE"
    wait_leader "$LEAD_MON"
    KEY=$(gen_key "$OSD_NODE")
    expect_fail "$OSD_NODE" "invalid cephx secret" \
        "echo '{\"cephx_secret\": \"$KEY\"}' | ceph osd new \$(uuidgen) -i -"
    note "stopping the old leader $LEAD_MON so upgraded $NEXT_MON takes over"
    run "$LEAD_MON" "systemctl stop ceph-mon@$LEAD_MON"
    wait_leader "$NEXT_MON"
    KEY=$(gen_key "$OSD_NODE")
    # The upgraded leader now ACCEPTS the new-type key. The command itself
    # may or may not return: the old mon can crash while acknowledging the
    # write, taking the quorum down underneath it. Either way the damage
    # is done, so bound the call and move on to what matters.
    run "$OSD_NODE" "echo '{\"cephx_secret\": \"$KEY\"}' | timeout 90 ceph osd new \$(uuidgen) -i - || true" > /dev/null
    note "the upgraded leader accepted the new-type key (no 'invalid cephx secret' this time)"
    note "the new key replicates into the key database shared by all mons"
    wait_for "old mon $THIRD_MON crashed reading the new-type key (core-dump, cannot restart)" 240 \
        "$THIRD_MON" "systemctl is-failed --quiet ceph-mon@$THIRD_MON ||
            journalctl -u ceph-mon@$THIRD_MON --since -15min | grep -q malformed_input"
    note "starting the stopped old leader $LEAD_MON: it syncs the same database and crashes too"
    run "$LEAD_MON" "systemctl start ceph-mon@$LEAD_MON || true"
    wait_for "old mon $LEAD_MON crashed as well" 240 \
        "$LEAD_MON" "systemctl is-failed --quiet ceph-mon@$LEAD_MON ||
            journalctl -u ceph-mon@$LEAD_MON --since -15min | grep -q malformed_input"
    note "only the upgraded mon survives; 1 of 3 mons is no quorum, the cluster is down"
    note "recovery: upgrade the crashed mons - the new binary reads the key fine"
    for m in "$THIRD_MON" "$LEAD_MON"; do
        upgrade_node "$m"
        run "$m" "systemctl reset-failed ceph-mon@$m 2>/dev/null || true;
            systemctl start ceph-mon@$m"
    done
    wait_quorum 3
    io_check "$CLIENT" recovered
    record "leader-flip: a new-type key created under an upgraded leader CRASHES every remaining old mon; upgrading them recovers the cluster"
}

scenario_mons_first() {
    banner "Scenario: mons-first"
    describe \
        "The recommended order: upgrade all mons first, then everything" \
        "else. Upgraded mons understand both key types, so there is no" \
        "broken window in either direction. Old machines keep adding" \
        "disks with old-type keys. Keys minted BY the mons stay old-type" \
        "too, until an operator opts into the new type. Once the OSD" \
        "machine is upgraded, its new-type keys are accepted as well."
    for m in "${MONS[@]}"; do
        upgrade_mon "$m"
    done
    io_check "$CLIENT" mons-upgraded
    make_osd "$OSD_NODE" 1
    note "provisioning from the still-old OSD node works fine"
    wait_for "mons report legacy keys are creatable" 60 "$CLIENT" \
        "ceph health detail | grep -q AUTH_INSECURE_KEYS_CREATABLE"
    KEY=$(run "$CLIENT" "ceph auth get-or-create-key client.mons-first mon 'allow r'")
    case $KEY in
        AQ*) note "mon-minted key starts with 'AQ': still the old aes type" ;;
        *)   echo "ERROR: expected an aes key, got: $KEY"; return 1 ;;
    esac
    upgrade_node "$OSD_NODE"
    make_osd "$OSD_NODE" 2
    note "provisioning from the upgraded OSD node (new-type key) works too"
    io_check "$CLIENT" all-upgraded
    record "mons-first: no breakage in either direction; mons keep minting old-type keys until told otherwise"
}

scenario_unordered() {
    banner "Scenario: unordered"
    describe \
        "The release-notes claim: an upgrade in ANY order keeps the" \
        "cluster running, as long as nobody creates or rotates a key" \
        "until it is done. We upgrade in a deliberately wrong order -" \
        "the OSD machine first, then the mons one by one, the leader in" \
        "the middle - and check client reads and writes after every" \
        "single step. Everything keeps authenticating with its existing" \
        "old-type key. Once every mon is upgraded, adding a disk works" \
        "again too."
    upgrade_node "$OSD_NODE"
    run "$OSD_NODE" "systemctl restart ceph-osd@0"
    wait_for "19.2.6 osd.0 rejoined with its old key" 120 "$CLIENT" \
        "ceph osd tree | grep -E 'osd.0\s.*up'"
    io_check "$CLIENT" osd-upgraded
    upgrade_mon "$THIRD_MON"
    io_check "$CLIENT" third-mon-upgraded
    upgrade_mon "$LEAD_MON"
    wait_leader "$LEAD_MON"
    note "the upgraded $LEAD_MON leads again; still safe because no key was created"
    io_check "$CLIENT" leader-upgraded
    upgrade_mon "$NEXT_MON"
    io_check "$CLIENT" all-mons-upgraded
    soak 60 "fully upgraded, client still old"
    make_osd "$OSD_NODE" 1
    note "with every mon upgraded, provisioning with the default new-type key works"
    io_check "$CLIENT" osd1-added
    record "unordered: any upgrade order keeps I/O working when no key is created or rotated mid-window"
}

scenario_provisioning_matrix() {
    banner "Scenario: provisioning-matrix"
    describe \
        "During the mixed window (old mons, upgraded OSD machine), which" \
        "ways of creating a key are safe? Four cases:" \
        "1. keys made locally by the new tools: FAIL" \
        "2. the same tools forced to the old key type: OK" \
        "3. keys minted by the mons on request: OK, the old mons mint" \
        "   old-type keys no matter who asks" \
        "4. a mon-minted key handed to the old client: OK"
    upgrade_node "$OSD_NODE"
    KEY=$(gen_key "$OSD_NODE")
    expect_fail "$OSD_NODE" "invalid cephx secret" \
        "echo '{\"cephx_secret\": \"$KEY\"}' | ceph osd new \$(uuidgen) -i -"
    note "case 1: local key from new tools rejected"
    make_osd "$OSD_NODE" 1 aes
    note "case 2: forcing the old type (--key-type aes) works end to end"
    KEY=$(run "$OSD_NODE" "ceph auth get-or-create-key client.matrix mon 'allow r' osd 'allow rwx pool=demo'")
    case $KEY in
        AQ*) note "case 3: mon-minted key starts with 'AQ' (old type) even when a new node asks" ;;
        *)   echo "ERROR: expected an aes key, got: $KEY"; return 1 ;;
    esac
    run "$OSD_NODE" "ceph auth get client.matrix -o /tmp/matrix.keyring"
    lxc file pull -q "$OSD_NODE/tmp/matrix.keyring" "$WORKDIR/matrix.keyring"
    lxc file push -q "$WORKDIR/matrix.keyring" "$CLIENT/etc/ceph/ceph.client.matrix.keyring"
    run "$CLIENT" "echo payload-matrix > /tmp/obj &&
        rados -n client.matrix -p demo put obj-matrix /tmp/obj &&
        rados -n client.matrix -p demo get obj-matrix - | grep -q payload-matrix"
    note "case 4: the never-upgraded client can use that mon-minted key"
    record "provisioning-matrix: only locally-generated new-type keys break; mon-minted and forced-old keys are safe"
}

scenario_fresh_cluster() {
    banner "Scenario: fresh-cluster"
    describe \
        "This one is not an upgrade. It shows what happens when someone" \
        "installs 19.2.6 and builds a brand-new cluster: it speaks ONLY" \
        "the new key type from day one. Even its admin keyring file is" \
        "unreadable to an old client, so the old client cannot connect" \
        "at all. Two mon settings (plus allowing old-type key creation)" \
        "let old clients back in."
    for c in "${MONS[@]}" "$OSD_NODE"; do
        upgrade_node "$c"
        run "$c" "systemctl stop ceph.target ceph-mon.target ceph-osd.target 2>/dev/null || true;
            systemctl disable ceph-mon@$c ceph-osd@0 2>/dev/null || true;
            rm -rf /var/lib/ceph/mon/* /var/lib/ceph/osd/*"
    done
    note "rebuilding the cluster from scratch with 19.2.6 tools"
    # Probe cluster state from the upgraded OSD node: the whole point of
    # this scenario is that the old client cannot talk to this cluster.
    CEPH_NODE=$OSD_NODE
    build_cluster "19.2.6"
    run "$OSD_NODE" "ceph osd pool create demo 8 8"
    io_check "$OSD_NODE" fresh-cluster
    note "monmap cipher policy of the fresh cluster:"
    run "$OSD_NODE" "ceph mon dump 2>/dev/null | grep -i cipher || true"
    KEY=$(run "$OSD_NODE" "ceph auth get-or-create-key client.fresh mon 'allow r'")
    case $KEY in
        Ag*) note "keys minted here start with 'Ag': the new aes256k type" ;;
        *)   echo "ERROR: expected an aes256k key, got: $KEY"; return 1 ;;
    esac
    note "the old client cannot even read the new admin keyring file:"
    expect_fail "$CLIENT" "error reading file" \
        "ceph-authtool -l /etc/ceph/ceph.client.admin.keyring"
    if run "$CLIENT" "timeout 30 ceph -s > /dev/null 2>&1"; then
        echo "ERROR: old client unexpectedly connected to the fresh cluster"
        return 1
    fi
    note "old client cannot connect: $(run "$CLIENT" "timeout 30 ceph -s 2>&1 | head -1 || true")"
    note "remediation: allow the old cipher again and mint an old-type key"
    run "$OSD_NODE" "ceph mon set auth_allowed_ciphers aes,aes256k"
    run "$OSD_NODE" "ceph config set mon mon_auth_allow_insecure_key true"
    run "$OSD_NODE" "ceph mon set auth_preferred_cipher aes"
    run "$OSD_NODE" "ceph auth get-or-create client.legacy mon 'allow r' osd 'allow rwx pool=demo' -o /tmp/legacy.keyring"
    lxc file pull -q "$OSD_NODE/tmp/legacy.keyring" "$WORKDIR/legacy.keyring"
    lxc file push -q "$WORKDIR/legacy.keyring" "$CLIENT/etc/ceph/ceph.client.legacy.keyring"
    run "$CLIENT" "echo payload-legacy > /tmp/obj &&
        rados -n client.legacy -p demo put obj-legacy /tmp/obj &&
        rados -n client.legacy -p demo get obj-legacy - | grep -q payload-legacy"
    note "the old client works again with the freshly minted old-type key"
    record "fresh-cluster: new clusters lock old clients out by default; two mon settings let them back in"
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
if [ "${1:-}" = "--clean" ]; then
    clean
    echo "Demo containers removed."
    exit 0
fi

if [ "${1:-}" = "list" ]; then
    printf '%s\n' "${SCENARIOS[@]}"
    exit 0
fi

TO_RUN=()
if [ "$#" -eq 0 ] || [ "${1:-}" = "all" ]; then
    TO_RUN=("${SCENARIOS[@]}")
elif [ "${1:-}" = "setup" ]; then
    setup
    exit 0
else
    for s in "$@"; do
        case " ${SCENARIOS[*]} " in
            *" $s "*) TO_RUN+=("$s") ;;
            *) echo "unknown scenario: $s (try: list)"; exit 1 ;;
        esac
    done
fi

if ! have_snapshot; then
    setup
fi

for s in "${TO_RUN[@]}"; do
    restore_baseline
    "scenario_${s//-/_}"
done

banner "Summary"
for r in "${RESULTS[@]}"; do
    printf ' * %s\n' "$r"
done
printf '\nContainers are kept for inspection. Remove them with: %s --clean\n' "$0"
