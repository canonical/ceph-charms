#!/bin/bash
# ==============================================================================
# 04-deploy-cos-lite.sh — Deploy COS Lite bundle, grant trust, wait for active
# ==============================================================================
#
# GitHub Actions integration notes:
# ---------------------------------
# - Runs after 03-juju-add-cloud.sh; Juju controller must be bootstrapped.
# - COS Lite charms require cluster-scoped RBAC (--trust). The bundle deploy
#   does NOT propagate --trust to individual charms, so we grant trust to each
#   application explicitly after deploy. This prevents the "Unauthorized" errors
#   seen with prometheus-k8s and transient failures in grafana/loki.
# - The wait loop at the end polls `juju status` until all units reach
#   active/idle or until WAIT_TIMEOUT is exceeded. Adjust WAIT_TIMEOUT for CI.
# - MODEL_NAME can be customized; defaults to "cos-lite".
#
# Workflow example:
#   - name: Deploy COS Lite
#     run: bash 04-deploy-cos-lite.sh
#     timeout-minutes: 30
#     env:
#       MODEL_NAME: cos-lite
#       WAIT_TIMEOUT: 1200
# ==============================================================================

set -euo pipefail

MODEL_NAME="${MODEL_NAME:-cos-lite}"
# Maximum seconds to wait for all units to settle to active/idle.
WAIT_TIMEOUT="${WAIT_TIMEOUT:-1200}"

# --- Create the model ---
echo "==> Adding model '${MODEL_NAME}'"
juju add-model "${MODEL_NAME}"

# --- Deploy the COS Lite bundle ---
echo "==> Deploying cos-lite bundle"
juju deploy cos-lite --trust

# --- Grant cluster trust to every COS Lite application ---
# The --trust flag on bundle deploy does not always propagate correctly.
# Granting explicitly avoids "Unauthorized" errors from prometheus-k8s
# (resource limit patch), grafana-k8s (dashboard relations), and loki-k8s
# (agent connectivity issues).
echo "==> Granting cluster-scoped trust to all COS Lite applications"
COS_APPS="prometheus alertmanager grafana loki traefik catalogue"
for app in ${COS_APPS}; do
  echo "    Trusting ${app}"
  juju trust "${app}" --scope=cluster
done

# --- Wait for all units to reach active/idle ---
echo "==> Waiting for all units to settle (timeout: ${WAIT_TIMEOUT}s)"
if ! juju wait-for model "${MODEL_NAME}" \
  --query='forEach(units, unit => unit.workload-status=="active" && unit.agent-status=="idle")' \
  --timeout="${WAIT_TIMEOUT}s"; then
  echo "==> Juju debug-log (last 50 lines):"
  juju debug-log --replay --tail 50 --no-tail || true
  echo ""
  echo "==> k8s pod status in cos-lite namespace:"
  lxc exec "${VM_NAME:-k8s-node}" -- k8s kubectl get pods -n "${MODEL_NAME}" -o wide 2>/dev/null || true
  echo ""
  echo "==> k8s events in cos-lite namespace (last 20):"
  lxc exec "${VM_NAME:-k8s-node}" -- k8s kubectl get events -n "${MODEL_NAME}" --sort-by='.lastTimestamp' 2>/dev/null | tail -20 || true
  echo ""
  echo "==> k8s node resources:"
  lxc exec "${VM_NAME:-k8s-node}" -- k8s kubectl describe nodes 2>/dev/null | grep -A 10 "Allocated resources" || true
  exit 1
fi

echo ""
echo "==> COS Lite deployment complete."
juju status
