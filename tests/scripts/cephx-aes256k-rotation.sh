#!/usr/bin/env bash
#
# cephx-aes256k-rotation.sh
#
# Self-contained functional test of the CVE-2025-30156 cephx rework on a Juju
# Ceph cluster: deploy ceph-mon/ceph-osd/ceph-fs/ceph-radosgw on 19.2.3, upgrade
# the packages to 19.2.6 by hand (the charms do not do it for a point release),
# then rotate every cephx key to aes256k and cut over to aes256k-only. Each step
# asserts its outcome; the run ends with a PASS/FAIL summary. This is the
# executable form of the "Upgrading a Juju Ceph cluster to 19.2.6 and rotating
# CephX keys" how-to.
#
# Requires: a bootstrapped Juju controller on an LXD cloud (juju bootstrap
# localhost lxd) and enough RAM for three ceph-osd VMs (>= 4G each; an OSD unit
# that gets OOM-killed mid-install cannot be recovered, only replaced).
#
# Environment:
#   MODEL      juju model name to create           (default cephx-test)
#   SOURCE     the 19.2.6 apt source               (default ppa:johnramsden/noble-caracal-ceph-squid-sru)
#   CHANNEL    charmhub channel for the charms      (default squid/stable)
#   BASE       charm base                           (default ubuntu@24.04)
#
# Usage:
#   tests/scripts/cephx-aes256k-rotation.sh          run the test
#   tests/scripts/cephx-aes256k-rotation.sh --clean  destroy the model
#
set -uo pipefail

MODEL="${MODEL:-cephx-test}"
SOURCE="${SOURCE:-ppa:johnramsden/noble-caracal-ceph-squid-sru}"
CHANNEL="${CHANNEL:-squid/stable}"
BASE="${BASE:-ubuntu@24.04}"

PASS=(); FAIL=()
ok()  { echo "PASS: $1"; PASS+=("$1"); }
bad() { echo "FAIL: $1"; FAIL+=("$1"); }
banner() { printf '\n============================================================\n== %s\n============================================================\n' "$*"; }

# run a ceph admin command string on a monitor unit
c() { juju ssh -m "$MODEL" ceph-mon/0 -- "sudo $*" 2>/dev/null; }
uhost() { juju ssh -m "$MODEL" "$1" -- hostname 2>/dev/null | tr -d '\r'; }
osd_units() { juju status -m "$MODEL" ceph-osd --format=json 2>/dev/null | jq -r '.applications["ceph-osd"].units | keys[]'; }
qleader() { c "ceph quorum_status -f json" | jq -r .quorum_leader_name; }
wait_osds() { local n; for _ in $(seq 1 60); do n=$(c "ceph osd stat" | grep -oE '[0-9]+ up' | grep -oE '[0-9]+'); [ "${n:-0}" -ge 3 ] && return 0; sleep 5; done; c "ceph osd stat"; return 1; }
wait_quorum() { for _ in $(seq 1 60); do [ "$(c 'ceph quorum_status -f json' | jq '.quorum | length')" = 3 ] && return 0; sleep 5; done; return 1; }
wait_versions_uniform() { for _ in $(seq 1 40); do c "ceph versions -f json" | grep -q 19.2.3 || return 0; sleep 6; done; return 1; }
# upgrade the installed ceph packages on a unit, no daemon restart (packaging does not restart)
upgrade_pkgs() {
    juju ssh -m "$MODEL" "$1" -- "sudo add-apt-repository -y ${SOURCE}; sudo apt-get update -q; \
        PKGS=\$(dpkg-query -W -f='\${db:Status-Abbrev} \${binary:Package}\n' | awk '\$1==\"ii\"{print \$2}' | grep -E '^(ceph|rbd|radosgw|python3-(ceph|rados|rbd|rgw)|lib(rados|rbd|cephfs|rgw|radosstriper))' | grep -vE '\-dbg'); \
        sudo NEEDRESTART_MODE=l apt-get install -y --only-upgrade \$PKGS >/dev/null; \
        dpkg-query -W -f='ceph-common \${Version}\n' ceph-common"
}

if [ "${1:-}" = "--clean" ]; then juju destroy-model --no-prompt --force --destroy-storage "$MODEL" 2>/dev/null; echo cleaned; exit 0; fi

##############################################################################
banner "Deploy a Ceph cluster on ${CHANNEL} (19.2.3)"
juju add-model "$MODEL"
juju switch "$MODEL" >/dev/null 2>&1 || true
juju deploy -m "$MODEL" ch:ceph-mon -n 3 --channel "$CHANNEL" --base "$BASE" \
    --config monitor-count=3 --config expected-osd-count=3 --config source=distro
juju deploy -m "$MODEL" ch:ceph-osd -n 3 --channel "$CHANNEL" --base "$BASE" --config source=distro \
    --constraints "virt-type=virtual-machine cores=2 mem=4G root-disk=20G"
juju deploy -m "$MODEL" ch:ceph-fs --channel "$CHANNEL" --base "$BASE" --config source=distro
juju deploy -m "$MODEL" ch:ceph-radosgw --channel "$CHANNEL" --base "$BASE" --config source=distro
juju integrate -m "$MODEL" ceph-osd:mon ceph-mon:osd
juju integrate -m "$MODEL" ceph-fs:ceph-mds ceph-mon:mds
juju integrate -m "$MODEL" ceph-radosgw:mon ceph-mon:radosgw
# ceph-mon stays "waiting" until OSDs exist, so gate on the ceph-osd units settling
# (they reach "blocked: No block devices", meaning installed + mon relation ready) then add disks.
juju wait-for application ceph-osd --query='forEach(units, u => u.workload-status == "blocked" || u.workload-status == "active")' --timeout=30m || true

banner "Attach a loop-backed OSD to each ceph-osd unit"
for u in $(osd_units); do
    juju ssh -m "$MODEL" "$u" -- 'sudo bash -euc "
        IMG=/var/lib/osd-loop.img; [ -f \$IMG ] || truncate -s 10G \$IMG
        losetup /dev/loop4 \$IMG 2>/dev/null || true
        printf \"[Unit]\nDefaultDependencies=no\nAfter=local-fs.target\nBefore=ceph-volume@.service\n[Service]\nType=oneshot\nRemainAfterExit=yes\nExecStart=/usr/sbin/losetup /dev/loop4 \$IMG\nExecStartPost=/usr/sbin/vgchange -ay\n[Install]\nWantedBy=local-fs.target\n\" > /etc/systemd/system/osd-loop.service
        systemctl enable osd-loop.service"'
done
for u in $(osd_units); do juju run -m "$MODEL" "$u" add-disk osd-devices=/dev/loop4 --wait=15m; done
wait_osds && ok "3 OSDs up on 19.2.3" || bad "OSDs did not come up"
c "ceph version | grep -q 19.2.3" && ok "cluster starts on 19.2.3" || bad "expected 19.2.3"

##############################################################################
banner "Set source= and confirm the charm does NOT upgrade a point release"
for app in ceph-mon ceph-osd ceph-fs ceph-radosgw; do juju config -m "$MODEL" "$app" source="$SOURCE"; done
juju wait-for model "$MODEL" --query='forEach(units, u => u.agent-status == "idle")' --timeout=15m || true
if c "dpkg-query -W -f='\${Version}' ceph-common | grep -q 19.2.3"; then
    ok "setting source is a no-op: monitors still run 19.2.3 packages"
else
    bad "unexpected package change from source alone"
fi

##############################################################################
banner "Upgrade the packages by hand: mons (leader last), OSDs, MDS, RGW"
LEADER=$(qleader)
order=""; for u in $(juju status -m "$MODEL" ceph-mon --format=json | jq -r '.applications["ceph-mon"].units|keys[]'); do
    if [ "$(uhost "$u")" = "$LEADER" ]; then last="$u"; else order="$order $u"; fi; done
for u in $order $last; do
    h=$(uhost "$u"); echo "--- $u ($h)"
    upgrade_pkgs "$u"
    juju ssh -m "$MODEL" "$u" -- "sudo systemctl restart ceph-mon@${h} ceph-mgr@${h}"
    wait_quorum
done
c "ceph osd set noout"
for u in $(osd_units); do
    h=$(uhost "$u"); echo "--- $u ($h)"
    upgrade_pkgs "$u"
    juju ssh -m "$MODEL" "$u" -- "sudo systemctl restart ceph-osd.target"
    wait_osds
done
c "ceph osd unset noout"
for u in $(juju status -m "$MODEL" ceph-fs --format=json | jq -r '.applications["ceph-fs"].units|keys[]'); do
    h=$(uhost "$u"); upgrade_pkgs "$u"; juju ssh -m "$MODEL" "$u" -- "sudo systemctl restart ceph-mds@${h}"; done
for u in $(juju status -m "$MODEL" ceph-radosgw --format=json | jq -r '.applications["ceph-radosgw"].units|keys[]'); do
    h=$(uhost "$u"); upgrade_pkgs "$u"; juju ssh -m "$MODEL" "$u" -- "sudo systemctl restart ceph-radosgw@rgw.${h}"; done
if wait_versions_uniform; then ok "all daemons upgraded to 19.2.6"; else bad "some daemons still on the old release"; c "ceph versions"; fi

##############################################################################
banner "Rotate: prefer aes256k, then rotate mon."
c "ceph mon set auth_preferred_cipher aes256k"
c "ceph mon dump | grep -q 'auth_preferred_cipher aes256k'" && ok "preferred cipher aes256k" || bad "preferred cipher not set"
# rotate mon. and import the new key into every monitor's keyring (the charm auths as mon.)
c "ceph auth rotate --key-type=aes256k mon. >/dev/null"
KR=$(c "ceph auth get mon." | grep -vE '^exported|^[[:space:]]*$')
for u in $(juju status -m "$MODEL" ceph-mon --format=json | jq -r '.applications["ceph-mon"].units|keys[]'); do
    h=$(uhost "$u")
    printf '%s\n' "$KR" | juju ssh -m "$MODEL" "$u" -- "sudo tee /tmp/mon.keyring >/dev/null && sudo ceph-authtool /var/lib/ceph/mon/ceph-${h}/keyring --import-keyring /tmp/mon.keyring && sudo rm /tmp/mon.keyring && sudo systemctl restart ceph-mon@${h}"
    wait_quorum
done
c "ceph --name mon. --keyring /var/lib/ceph/mon/ceph-\$(hostname)/keyring auth get client.admin >/dev/null" \
    && ok "mon. keyring re-synced (charm auth path works)" || bad "mon. keyring stale after rotation"

banner "Rotate mgr / osd / mds / rgw via the ceph-mon rotate-key action"
for u in $(juju status -m "$MODEL" ceph-mon --format=json | jq -r '.applications["ceph-mon"].units|keys[]'); do
    juju run -m "$MODEL" "$u" rotate-key entity=mgr --wait=5m; done
juju run -m "$MODEL" ceph-mon/leader rotate-key entity=osd --wait=10m
# the action updates the OSD keyring file but NOT the bluestore label: fix the label so a
# reboot (ceph-volume activate) does not re-prime the old key.
for id in $(c "ceph osd ls"); do c "sh -c 'ceph auth get-key osd.${id} | ceph tell osd.${id} rotate-stored-key -i -'"; done
wait_osds
MDS=$(c "ceph fs status -f json" | jq -r '.mdsmap[]|select(.state=="active").name')
juju run -m "$MODEL" ceph-mon/leader rotate-key "entity=mds.${MDS}" --wait=5m
# the mds action installs a PENDING key; commit it by restarting the mds with the pending key
for u in $(juju status -m "$MODEL" ceph-fs --format=json | jq -r '.applications["ceph-fs"].units|keys[]'); do
    PK=$(c "ceph auth get-or-create-pending mds.${MDS} --format json" | jq -r '.[0].pending_key')
    printf '[mds.%s]\n\tkey = %s\n' "$MDS" "$PK" | juju ssh -m "$MODEL" "$u" -- "sudo tee /var/lib/ceph/mds/ceph-${MDS}/keyring >/dev/null && sudo chown ceph:ceph /var/lib/ceph/mds/ceph-${MDS}/keyring && sudo systemctl restart ceph-mds@${MDS}"; done
RGWH=$(uhost ceph-radosgw/0)
juju run -m "$MODEL" ceph-mon/leader rotate-key "entity=client.rgw.${RGWH}" --wait=5m
svc_clear=false
for _ in $(seq 1 24); do
    c "ceph --format=json health detail" | jq -e '.checks | has("AUTH_INSECURE_SERVICE_KEY_TYPE") | not' >/dev/null && { svc_clear=true; break; }
    sleep 5
done
if $svc_clear; then
    ok "all service daemon keys are aes256k"
else
    bad "service keys still insecure"; c "ceph health detail | grep -A8 AUTH_INSECURE_SERVICE_KEY_TYPE"
fi

banner "Switch the service cipher and rotate the admin key on every mon unit"
c "ceph mon set auth_service_cipher aes256k"
c "ceph config set mon mon_auth_allow_insecure_key false" || true
c "ceph auth get-or-create client.admin-backup mon 'allow *' | sudo tee /root/admin-backup.keyring >/dev/null"
NEW=$(c "ceph auth rotate --key-type=aes256k client.admin")
for u in $(juju status -m "$MODEL" ceph-mon --format=json | jq -r '.applications["ceph-mon"].units|keys[]'); do
    printf '%s\n' "$NEW" | juju ssh -m "$MODEL" "$u" -- "sudo tee /tmp/a.keyring >/dev/null && sudo ceph-authtool --import-keyring /tmp/a.keyring /etc/ceph/ceph.client.admin.keyring && sudo rm /tmp/a.keyring && sudo ceph -s >/dev/null"; done
c "ceph auth rm client.admin-backup"
c "ceph -s >/dev/null" && ok "admin key rotated on all mon units" || bad "admin rotation broke access"

##############################################################################
banner "Cut over to aes256k-only"
c "ceph health mute AUTH_INSECURE_CLIENT_KEY_TYPE 8w" || true
c "ceph mon set auth_allowed_ciphers aes256k"
c "ceph mon dump | grep -q 'auth_allowed_ciphers aes256k'" && ok "auth_allowed_ciphers is aes256k-only" || bad "cutover did not take"
c "ceph -s >/dev/null" && ok "cluster reachable under aes256k-only" || bad "cluster unreachable after cutover"

##############################################################################
banner "Summary"
for p in "${PASS[@]:-}"; do [ -n "$p" ] && echo "  PASS  $p"; done
for f in "${FAIL[@]:-}"; do [ -n "$f" ] && echo "  FAIL  $f"; done
if [ "${#FAIL[@]}" -eq 0 ]; then echo "ALL CEPHX AES256K CHECKS PASSED"; else echo "${#FAIL[@]} FAILURE(S)"; exit 1; fi
