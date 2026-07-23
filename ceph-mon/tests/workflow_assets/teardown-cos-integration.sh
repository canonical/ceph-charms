#!/usr/bin/env bash
# ==============================================================================
# teardown-cos-integration.sh
# Tears down all resources created by run-cos-integration.sh:
#   - Juju controllers 'lxd' and 'ck8s-cloud' (and all their models/machines)
#   - Juju k8s cloud definition 'ck8s-cloud' (local client)
#   - LXD VM 'k8s-node' and profile 'k8s-profile'
#   - kubeconfig.yaml
#
# SAFE: does NOT touch the pre-existing 'lxd-controller' / 'cephtools' model.
# Idempotent: skips anything that is already gone.
#
# Note: k8s-backed controllers (ck8s-cloud) often hang on PV/PVC reclamation
# during graceful destroy.  This script falls back to deleting the k8s-node VM
# (which kills the cluster) and then unregistering the dead controller.
# ==============================================================================
set -uo pipefail

LXD_CONTROLLER="${LXD_CONTROLLER:-lxd}"
K8S_CLOUD="${K8S_CLOUD:-ck8s-cloud}"
K8S_VM="${K8S_VM:-k8s-node}"
K8S_PROFILE="${K8S_PROFILE:-k8s-profile}"
REPO_ROOT="${REPO_ROOT:-/home/ubuntu/src/ceph-charms}"
# Graceful-destroy timeout (seconds) before falling back to VM deletion.
DESTROY_TIMEOUT="${DESTROY_TIMEOUT:-120}"

log() { echo -e "\n==> $*"; }

# --- 1. Destroy the LXD (localhost) controller gracefully ---
if juju show-controller "$LXD_CONTROLLER" >/dev/null 2>&1; then
  log "Destroying Juju controller '$LXD_CONTROLLER' (all models + storage)"
  juju destroy-controller "$LXD_CONTROLLER" \
    --destroy-all-models --destroy-storage --force --no-prompt 2>&1 || true
else
  log "Controller '$LXD_CONTROLLER' not found — skipping"
fi

# --- 2. Destroy the k8s controller (with fallback for stuck storage) ---
if juju show-controller "$K8S_CLOUD" >/dev/null 2>&1; then
  log "Attempting graceful destroy of k8s controller '$K8S_CLOUD' (timeout ${DESTROY_TIMEOUT}s)"
  if timeout "$DESTROY_TIMEOUT" juju destroy-controller "$K8S_CLOUD" \
       --destroy-all-models --destroy-storage --force --no-prompt 2>&1; then
    log "Graceful destroy of '$K8S_CLOUD' succeeded"
  else
    log "Graceful destroy stalled (k8s storage reclamation) — falling back"
    log "Deleting k8s-node VM to kill the stuck k8s cluster"
    lxc delete "$K8S_VM" --force 2>&1 || true
    log "Unregistering dead controller '$K8S_CLOUD'"
    echo "$K8S_CLOUD" | juju unregister "$K8S_CLOUD" 2>&1 || true
  fi
else
  log "Controller '$K8S_CLOUD' not found — skipping"
fi

# --- 3. Delete the k8s-node LXD VM (if still present) ---
if lxc info "$K8S_VM" >/dev/null 2>&1; then
  log "Deleting LXD VM '$K8S_VM'"
  lxc delete "$K8S_VM" --force 2>&1 || true
else
  log "LXD VM '$K8S_VM' not found — skipping"
fi

# --- 4. Delete the k8s-profile ---
if lxc profile show "$K8S_PROFILE" >/dev/null 2>&1; then
  log "Deleting LXD profile '$K8S_PROFILE'"
  lxc profile delete "$K8S_PROFILE" 2>&1 || true
else
  log "LXD profile '$K8S_PROFILE' not found — skipping"
fi

# --- 5. Remove the local k8s cloud definition (leftover from `juju add-k8s`) ---
#     The controller may be gone, but the client-side cloud definition persists
#     and would make the next `juju add-k8s ck8s-cloud` fail with
#     "k8s 'ck8s-cloud' already exists".
if juju clouds --format yaml 2>/dev/null | grep -q "^${K8S_CLOUD}:"; then
  log "Removing local k8s cloud definition '${K8S_CLOUD}'"
  juju remove-cloud "$K8S_CLOUD" --client 2>&1 || true
else
  log "Local k8s cloud '${K8S_CLOUD}' not found — skipping"
fi

# --- 6. Remove kubeconfig ---
rm -f "$REPO_ROOT/kubeconfig.yaml" 2>/dev/null && log "Removed kubeconfig.yaml" || true

log "Teardown complete."
echo ""
echo "Remaining LXD instances:"
lxc list 2>/dev/null || true
echo ""
echo "Remaining Juju controllers:"
juju controllers 2>/dev/null || true
echo ""
echo "Remaining Juju clouds (local):"
juju clouds 2>/dev/null || true
