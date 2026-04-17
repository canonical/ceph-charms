#!/bin/bash
# ==============================================================================
# 01-setup-lxd-vm.sh — Create an LXD VM profile and launch a Ubuntu VM for k8s
# ==============================================================================
#
# GitHub Actions integration notes:
# ---------------------------------
# - Assumes LXD is pre-installed and initialized on the self-hosted runner.
#   If not, add a prior step: snap install lxd && lxd init --auto
# - The LXD bridge (LXD_BRIDGE) must exist. Default "lxdbr0" is created by
#   lxd init --auto. Adjust if your runner uses a custom bridge.
# - VM_NAME and PROFILE_NAME can be overridden as env vars from the workflow.
# - This script is idempotent — safe to re-run (creates only if missing).
#
# Workflow example:
#   - name: Setup LXD VM
#     run: bash 01-setup-lxd-vm.sh
#     env:
#       VM_NAME: k8s-node
#       LXD_BRIDGE: lxdbr0
# ==============================================================================

set -euo pipefail

# --- Configurable variables (override via env in CI) ---
VM_NAME="${VM_NAME:-k8s-node}"
PROFILE_NAME="${PROFILE_NAME:-k8s-profile}"
LXD_BRIDGE="${LXD_BRIDGE:-lxdbr0}"
LXD_POOL="${LXD_POOL:-default}"
VM_CPUS="${VM_CPUS:-4}"
VM_MEMORY="${VM_MEMORY:-16GB}"
VM_DISK="${VM_DISK:-40GB}"
UBUNTU_IMAGE="${UBUNTU_IMAGE:-ubuntu:24.04}"

echo "==> Creating LXD profile '${PROFILE_NAME}'"
lxc profile create "${PROFILE_NAME}" 2>/dev/null || echo "    Profile already exists, updating."
lxc profile set "${PROFILE_NAME}" limits.cpu="${VM_CPUS}" limits.memory="${VM_MEMORY}"

# Add or update root disk device
if lxc profile device get "${PROFILE_NAME}" root pool >/dev/null 2>&1; then
  lxc profile device set "${PROFILE_NAME}" root size="${VM_DISK}"
else
  lxc profile device add "${PROFILE_NAME}" root disk pool="${LXD_POOL}" path=/ size="${VM_DISK}"
fi

# Add or update eth0 NIC bridged to the LXD bridge
if lxc profile device get "${PROFILE_NAME}" eth0 nictype >/dev/null 2>&1; then
  lxc profile device set "${PROFILE_NAME}" eth0 parent="${LXD_BRIDGE}"
else
  lxc profile device add "${PROFILE_NAME}" eth0 nic nictype=bridged parent="${LXD_BRIDGE}" name=eth0
fi

echo "==> Launching VM '${VM_NAME}'"
if lxc info "${VM_NAME}" >/dev/null 2>&1; then
  echo "    VM '${VM_NAME}' already exists, skipping launch."
else
  lxc launch "${UBUNTU_IMAGE}" "${VM_NAME}" --vm --profile "${PROFILE_NAME}"
fi

echo "==> Waiting for VM agent to be ready"
for i in $(seq 1 30); do
  if lxc exec "${VM_NAME}" -- true 2>/dev/null; then
    break
  fi
  echo "    Attempt ${i}/30: VM agent not ready, retrying in 10s..."
  sleep 10
done

# Check if VM agent became ready
if ! lxc exec "${VM_NAME}" -- true 2>/dev/null; then
  echo "ERROR: VM '${VM_NAME}' agent failed to become ready after 300 seconds" >&2
  exit 1
fi

echo "==> Waiting for cloud-init to complete inside VM"
lxc exec "${VM_NAME}" -- cloud-init status --wait

echo "==> VM '${VM_NAME}' is ready."
lxc list "${VM_NAME}" --format csv -c n,s,4
